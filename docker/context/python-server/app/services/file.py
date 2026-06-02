"""
File Operation Service Implementation - Async Version
"""

import asyncio
import base64
import errno as errno_mod
import fnmatch
import glob
import json
import logging
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional, Union

from fastapi import UploadFile

from app.core.exceptions import (
    AppException,
    BadRequestException,
    FileToolException,
    ResourceNotFoundException,
)
from app.models.file import (
    FileFindResult,
    FileGlobResult,
    FileGrepResult,
    FileInfo,
    FileListResult,
    FileReadResult,
    FileReplaceResult,
    FileSearchResult,
    FileSyncResult,
    FileSyncWatchListResult,
    FileSyncWatchResult,
    FileSyncWatchStopResult,
    FileUploadResult,
    FileOperationError,
    FileWriteResult,
    GlobFileInfo,
    GrepMatch,
    LsyncdWatcher,
    StrReplaceEditorResult,
)
from app.schemas.file import FileContentEncoding
from app.services.editor_manager import editor_manager
from app.logging.decorators import trace_api


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileDownloadSourceState:
    """Pinned source metadata for an in-flight download."""

    device: int
    inode: int
    size: int
    modified_time_ns: int


_SUDO_FILE_HELPER = """
import errno
import json
import os
import sys

operation = sys.argv[1]
path = sys.argv[2]

try:
    if operation == "read":
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        sys.stdout.write(json.dumps({"success": True, "content": content}))
    elif operation == "write":
        mode = sys.argv[3]
        text_mode = sys.argv[4] == "1"
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        if text_mode:
            content = sys.stdin.read()
            with open(path, mode, encoding="utf-8") as f:
                f.write(content)
            bytes_written = len(content.encode("utf-8"))
        else:
            content = sys.stdin.buffer.read()
            with open(path, mode) as f:
                f.write(content)
            bytes_written = len(content)

        sys.stdout.write(json.dumps({"success": True, "bytes_written": bytes_written}))
    else:
        raise ValueError(f"Unsupported helper operation: {operation}")
except Exception as e:
    payload = {
        "success": False,
        "message": str(e),
        "exception_type": type(e).__name__,
    }
    if isinstance(e, OSError):
        payload["errno"] = e.errno
        payload["errno_name"] = errno.errorcode.get(e.errno)
    sys.stdout.write(json.dumps(payload))
    sys.exit(1)
"""


def _file_error_message(operation: str, error: BaseException | str) -> str:
    detail = str(error)
    if operation == 'read':
        return f'Failed to read file: {detail}'
    if operation == 'replace':
        return f'Failed to replace in file: {detail}'
    if operation == 'search':
        return f'Failed to search file: {detail}'
    if operation == 'find':
        return f'Failed to find files: {detail}'
    if operation == 'list':
        return f'Failed to list path: {detail}'
    if operation == 'grep':
        return f'Failed to grep path: {detail}'
    if operation == 'glob':
        return f'Failed to glob path: {detail}'
    if operation == 'upload':
        return f'Failed to upload file: {detail}'
    if operation == 'str_replace_editor':
        return f'[str_replace_editor] error: {detail}'
    if isinstance(error, PermissionError):
        return f'Permission denied: {detail}'
    return f'Failed to {operation} file: {detail}'


def _file_error_type(errno_code: int | None, exception_type: str | None = None) -> str:
    if errno_code in {getattr(errno_mod, 'EACCES', None), getattr(errno_mod, 'EPERM', None)}:
        return 'permission_denied'
    if errno_code == getattr(errno_mod, 'ENOENT', None):
        return 'not_found'
    if errno_code == getattr(errno_mod, 'EEXIST', None):
        return 'already_exists'
    if errno_code in {
        getattr(errno_mod, 'EISDIR', None),
        getattr(errno_mod, 'ENOTDIR', None),
    }:
        return 'invalid_target'
    if errno_code == getattr(errno_mod, 'ENAMETOOLONG', None):
        return 'invalid_path'
    if errno_code == getattr(errno_mod, 'EROFS', None):
        return 'read_only_filesystem'
    if errno_code == getattr(errno_mod, 'ENOSPC', None):
        return 'no_space_left'
    if exception_type == 'UnicodeDecodeError':
        return 'decode_error'
    return 'io_error'


def _load_rg_type_extensions(rg_path: str) -> dict[str, list[str]]:
    """Parse `rg --type-list` output into a type → extensions mapping."""
    import subprocess

    result = subprocess.run(
        [rg_path, '--type-list'],
        capture_output=True,
        text=True,
        timeout=5,
    )
    mapping: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        # Format: "py: *.py, *.pyi, *.pyw"
        if ':' not in line:
            continue
        name, globs = line.split(':', 1)
        exts = []
        for g in globs.split(','):
            g = g.strip()
            # Only keep simple "*.ext" patterns, skip complex ones like "*.h.in"
            if g.startswith('*.') and '.' not in g[2:]:
                exts.append(g[1:])  # "*.py" → ".py"
        if exts:
            mapping[name.strip()] = exts
    return mapping



class FileService:
    """File Operation Service"""

    _rg_path: Optional[str] = None
    _rg_checked: bool = False
    _rg_type_extensions: Optional[dict[str, list[str]]] = None

    def __init__(self):
        self._lsyncd_processes: dict[str, LsyncdWatcher] = {}
        self._lsyncd_lock = asyncio.Lock()
        self.MAX_WATCHERS = 10

    @staticmethod
    def _build_os_error(
        path: str,
        errno_code: int,
        *,
        message: str | None = None,
    ) -> OSError:
        detail = message or os.strerror(errno_code)
        if errno_code == getattr(errno_mod, 'ENOENT', None):
            return FileNotFoundError(errno_code, detail, path)
        if errno_code == getattr(errno_mod, 'ENOTDIR', None):
            return NotADirectoryError(errno_code, detail, path)
        if errno_code == getattr(errno_mod, 'EISDIR', None):
            return IsADirectoryError(errno_code, detail, path)
        if errno_code == getattr(errno_mod, 'EEXIST', None):
            return FileExistsError(errno_code, detail, path)
        if errno_code in {
            getattr(errno_mod, 'EACCES', None),
            getattr(errno_mod, 'EPERM', None),
        }:
            return PermissionError(errno_code, detail, path)
        return OSError(errno_code, detail, path)

    @classmethod
    def _ensure_existing_path(cls, path: str) -> None:
        if not os.path.exists(path):
            raise cls._build_os_error(path, errno_mod.ENOENT)

    @classmethod
    def _ensure_directory_path(cls, path: str) -> None:
        cls._ensure_existing_path(path)
        if not os.path.isdir(path):
            raise cls._build_os_error(path, errno_mod.ENOTDIR)

    @classmethod
    def _ensure_regular_file_path(cls, path: str) -> None:
        cls._ensure_existing_path(path)
        if os.path.isdir(path):
            raise cls._build_os_error(path, errno_mod.EISDIR)

    @staticmethod
    def _build_file_tool_error(
        operation: str,
        path: str,
        error: BaseException | str,
        *,
        errno_code: int | None = None,
        errno_name: str | None = None,
        exception_type: str | None = None,
    ) -> FileOperationError:
        if errno_code is None and not isinstance(error, str):
            errno_code = getattr(error, 'errno', None)
        if errno_name is None and errno_code is not None:
            errno_name = errno_mod.errorcode.get(errno_code)
        if exception_type is None and not isinstance(error, str):
            exception_type = type(error).__name__

        return FileOperationError(
            path=path,
            operation=operation,
            message=_file_error_message(operation, error),
            error_type=_file_error_type(errno_code, exception_type),
            retryable=False,
            errno=errno_code,
            errno_name=errno_name,
            exception_type=exception_type,
        )

    def _raise_if_file_tool_error(
        self,
        operation: str,
        path: str,
        error: BaseException | str,
        *,
        errno_code: int | None = None,
        errno_name: str | None = None,
        exception_type: str | None = None,
    ) -> None:
        if errno_code is None and not isinstance(error, str):
            errno_code = getattr(error, 'errno', None)
        if exception_type is None and not isinstance(error, str):
            exception_type = type(error).__name__

        if errno_code is not None or exception_type == 'UnicodeDecodeError':
            raise FileToolException(
                self._build_file_tool_error(
                    operation,
                    path,
                    error,
                    errno_code=errno_code,
                    errno_name=errno_name,
                    exception_type=exception_type,
                )
            )

    async def _run_sudo_file_helper(
        self,
        operation: str,
        path: str,
        *,
        input_bytes: bytes | None = None,
        mode: str | None = None,
        text_mode: bool | None = None,
    ) -> dict:
        command = ['sudo', 'python3', '-c', _SUDO_FILE_HELPER, operation, path]
        if operation == 'write':
            if mode is None or text_mode is None:
                raise ValueError('mode and text_mode are required for sudo write helper')
            command.extend([mode, '1' if text_mode else '0'])

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(input=input_bytes)

        raw_output = stdout.decode('utf-8', errors='replace').strip()
        try:
            payload = json.loads(raw_output or '{}')
        except json.JSONDecodeError as exc:
            raise AppException(
                message=(
                    f'Failed to parse sudo {operation} helper output: '
                    f'{raw_output or stderr.decode("utf-8", errors="replace")}'
                )
            ) from exc

        if process.returncode == 0 and payload.get('success'):
            return payload

        self._raise_if_file_tool_error(
            operation,
            path,
            payload.get('message')
            or stderr.decode('utf-8', errors='replace')
            or f'sudo {operation} failed',
            errno_code=payload.get('errno'),
            errno_name=payload.get('errno_name'),
            exception_type=payload.get('exception_type'),
        )
        raise AppException(
            message=_file_error_message(
                operation,
                payload.get('message')
                or stderr.decode('utf-8', errors='replace')
                or f'sudo {operation} failed',
            )
        )

    @classmethod
    def _check_rg_available(cls) -> Optional[str]:
        """Check if ripgrep is available (cached)."""
        if not cls._rg_checked:
            cls._rg_path = shutil.which('rg')
            cls._rg_checked = True
        return cls._rg_path

    @classmethod
    def _get_rg_type_extensions(cls) -> dict[str, list[str]]:
        """Get ripgrep type → extensions mapping (cached, loaded from rg --type-list)."""
        if cls._rg_type_extensions is None:
            rg_path = cls._check_rg_available()
            if rg_path:
                try:
                    cls._rg_type_extensions = _load_rg_type_extensions(rg_path)
                except Exception:
                    cls._rg_type_extensions = {}
            else:
                cls._rg_type_extensions = {}
        return cls._rg_type_extensions

    @trace_api('file')
    async def read_file(
        self,
        file: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        sudo: bool = False,
    ) -> FileReadResult:
        """
        Asynchronously read file content

        Args:
            file: Absolute file path
            start_line: Starting line (0-based)
            end_line: Ending line (not included)
            sudo: Whether to use sudo privileges
        """
        # Check if file exists
        try:
            content = ''

            # Read with sudo
            if sudo:
                payload = await self._run_sudo_file_helper('read', file)
                content = payload['content']
            else:
                # Asynchronously read file
                def read_file_async():
                    with open(file, 'r', encoding='utf-8') as f:
                        return f.read()

                # Execute IO operation in thread pool
                content = await asyncio.to_thread(read_file_async)

            # Process line range
            if start_line is not None or end_line is not None:
                lines = content.splitlines()
                start = start_line if start_line is not None else 0
                end = end_line if end_line is not None else len(lines)
                content = '\n'.join(lines[start:end])

            return FileReadResult(content=content, file=file)
        except Exception as e:
            if isinstance(e, BadRequestException) or isinstance(
                e, ResourceNotFoundException
            ):
                raise e
            self._raise_if_file_tool_error('read', file, e)
            raise AppException(message=f'Failed to read file: {str(e)}')

    @trace_api('file')
    async def write_file(
        self,
        file: str,
        content: str,
        encoding: Optional[Union[FileContentEncoding, str]] = FileContentEncoding.UTF8,
        append: bool = False,
        leading_newline: bool = False,
        trailing_newline: bool = False,
        sudo: bool = False,
    ) -> FileWriteResult:
        """
        Asynchronously write file content (supports both text and binary)

        Args:
            file: Absolute file path
            content: Content to write (text or base64 encoded for binary)
            encoding: Content encoding type (utf-8, base64, or raw)
            append: Whether to append mode
            leading_newline: Whether to add a leading newline (only for text mode)
            trailing_newline: Whether to add a trailing newline (only for text mode)
            sudo: Whether to use sudo privileges
        """
        try:
            # Convert encoding to string if it's an enum
            if isinstance(encoding, FileContentEncoding):
                encoding = encoding.value

            # Prepare content based on encoding
            if encoding == FileContentEncoding.BASE64.value:
                # Decode base64 to bytes
                try:
                    file_bytes = base64.b64decode(content)
                    is_binary = True
                except Exception as e:
                    raise BadRequestException(f'Invalid base64 content: {str(e)}')
            elif encoding == FileContentEncoding.RAW.value:
                # Raw bytes mode
                file_bytes = content.encode(
                    'latin-1'
                )  # Use latin-1 to preserve byte values
                is_binary = True
            else:
                # Text mode (UTF-8)
                is_binary = False
                # Add newlines only in text mode
                if leading_newline:
                    content = '\n' + content
                if trailing_newline:
                    content = content + '\n'
                file_bytes = content.encode('utf-8')

            bytes_written = 0

            # Write with sudo
            if sudo:
                helper_mode = 'ab' if append and is_binary else 'a' if append else 'wb' if is_binary else 'w'
                payload = await self._run_sudo_file_helper(
                    'write',
                    file,
                    input_bytes=file_bytes,
                    mode=helper_mode,
                    text_mode=not is_binary,
                )
                bytes_written = payload['bytes_written']
            else:
                # Ensure directory exists
                os.makedirs(os.path.dirname(file), exist_ok=True)

                # Asynchronously write file
                def write_file_async():
                    if is_binary:
                        mode = 'ab' if append else 'wb'
                        with open(file, mode) as f:
                            f.write(file_bytes)
                            return len(file_bytes)
                    else:
                        mode = 'a' if append else 'w'
                        with open(file, mode, encoding='utf-8') as f:
                            f.write(content)
                            return len(file_bytes)

                bytes_written = await asyncio.to_thread(write_file_async)

            return FileWriteResult(file=file, bytes_written=bytes_written)
        except PermissionError as e:
            self._raise_if_file_tool_error('write', file, e)
            raise AppException(message=f'Permission denied: {str(e)}')
        except OSError as e:
            self._raise_if_file_tool_error('write', file, e)
            raise AppException(message=f'Failed to write file: {str(e)}')
        except Exception as e:
            if isinstance(e, BadRequestException):
                raise e
            self._raise_if_file_tool_error('write', file, e)
            raise AppException(message=f'Failed to write file: {str(e)}')

    @trace_api('file')
    async def str_replace(
        self, file: str, old_str: str, new_str: str, sudo: bool = False
    ) -> FileReplaceResult:
        """
        Asynchronously replace string in file

        Args:
            file: Absolute file path
            old_str: Original string to be replaced
            new_str: New replacement string
            sudo: Whether to use sudo privileges
        """
        # First read file content
        file_result = await self.read_file(file, sudo=sudo)
        content = file_result.content

        # Calculate replacement count
        replaced_count = content.count(old_str)
        if replaced_count == 0:
            return FileReplaceResult(file=file, replaced_count=0)

        # Perform replacement
        new_content = content.replace(old_str, new_str)

        # Write back to file
        await self.write_file(file, new_content, sudo=sudo)

        return FileReplaceResult(file=file, replaced_count=replaced_count)

    @trace_api('file')
    async def find_in_content(
        self, file: str, regex: str, sudo: bool = False
    ) -> FileSearchResult:
        """
        Asynchronously search in file content

        Args:
            file: Absolute file path
            regex: Regular expression pattern
            sudo: Whether to use sudo privileges
        """
        # Read file
        file_result = await self.read_file(file, sudo=sudo)
        content = file_result.content

        # Process line by line
        lines = content.splitlines()
        matches = []
        line_numbers = []

        # Compile regular expression
        try:
            pattern = re.compile(regex)
        except Exception as e:
            raise BadRequestException(f'Invalid regular expression: {str(e)}')

        # Find matches (use async processing for possibly large files)
        def process_lines():
            nonlocal matches, line_numbers
            for i, line in enumerate(lines):
                if pattern.search(line):
                    matches.append(line)
                    line_numbers.append(i)

        await asyncio.to_thread(process_lines)

        return FileSearchResult(file=file, matches=matches, line_numbers=line_numbers)

    @trace_api('file')
    async def find_by_name(self, path: str, glob_pattern: str) -> FileFindResult:
        """
        Asynchronously find files by name pattern

        Args:
            path: Directory path to search
            glob_pattern: File name pattern (glob syntax)
        """
        try:
            self._ensure_directory_path(path)

            # Asynchronously find files
            def glob_async():
                search_pattern = os.path.join(path, glob_pattern)
                return glob.glob(search_pattern, recursive=True)

            files = await asyncio.to_thread(glob_async)
        except OSError as e:
            self._raise_if_file_tool_error('find', path, e)
            raise AppException(message=f'Failed to find files: {str(e)}')

        return FileFindResult(path=path, files=files)

    @trace_api('file')
    async def upload_file(self, path: str, file_stream: UploadFile) -> FileUploadResult:
        """
        Upload file using streaming for large files

        Args:
            path: Target file path to save uploaded file
            file_stream: File stream from FastAPI UploadFile
        """
        try:
            chunk_size = 8192  # 8KB chunks
            total_size = 0

            # Ensure directory exists
            os.makedirs(os.path.dirname(path), exist_ok=True)

            # Stream write directly to target file
            def write_stream_direct():
                nonlocal total_size
                with open(path, 'wb') as f:
                    while True:
                        chunk = file_stream.file.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        total_size += len(chunk)

            await asyncio.to_thread(write_stream_direct)

            return FileUploadResult(file_path=path, file_size=total_size, success=True)
        except OSError as e:
            self._raise_if_file_tool_error('upload', path, e)
            raise AppException(message=f'Failed to upload file: {str(e)}')
        except Exception as e:
            raise AppException(message=f'Failed to upload file: {str(e)}')

    def ensure_file(self, path: str) -> None:
        """
        Ensure file exists

        Args:
            path: Path of the file to check
        """
        try:
            self._ensure_regular_file_path(path)
        except OSError as e:
            if e.errno == errno_mod.ENOENT:
                raise ResourceNotFoundException(f'File does not exist: {path}')
            if e.errno in {errno_mod.EISDIR, errno_mod.ENOTDIR}:
                raise BadRequestException(f'Path is not a file: {path}')
            raise AppException(message=f'Failed to ensure file: {str(e)}')
        except Exception as e:
            raise AppException(message=f'Failed to ensure file: {str(e)}')

    @staticmethod
    def _build_download_source_state(stat_result: os.stat_result) -> FileDownloadSourceState:
        return FileDownloadSourceState(
            device=stat_result.st_dev,
            inode=stat_result.st_ino,
            size=stat_result.st_size,
            modified_time_ns=stat_result.st_mtime_ns,
        )

    def _get_download_source_state_from_path(self, path: str) -> FileDownloadSourceState:
        return self._build_download_source_state(os.stat(path))

    def _get_download_source_state_from_file(
        self, file_obj: BinaryIO
    ) -> FileDownloadSourceState:
        return self._build_download_source_state(os.fstat(file_obj.fileno()))

    async def open_file_for_download(
        self, path: str
    ) -> tuple[BinaryIO, FileDownloadSourceState]:
        self.ensure_file(path)

        try:
            file_obj = await asyncio.to_thread(open, path, 'rb')
        except FileNotFoundError:
            raise ResourceNotFoundException(f'File does not exist: {path}')
        except IsADirectoryError:
            raise BadRequestException(f'Path is not a file: {path}')
        except NotADirectoryError:
            raise BadRequestException(f'Path is not a file: {path}')
        except OSError as e:
            raise AppException(message=f'Failed to open file for download: {str(e)}')

        try:
            source_state = await asyncio.to_thread(
                self._get_download_source_state_from_file, file_obj
            )
        except Exception:
            await asyncio.to_thread(file_obj.close)
            raise

        return file_obj, source_state

    async def close_download_file(self, file_obj: BinaryIO) -> None:
        await asyncio.to_thread(file_obj.close)

    async def read_download_chunk(self, file_obj: BinaryIO, chunk_size: int) -> bytes:
        return await asyncio.to_thread(file_obj.read, chunk_size)

    async def download_source_changed(
        self, path: str, expected_state: FileDownloadSourceState
    ) -> bool:
        try:
            current_state = await asyncio.to_thread(
                self._get_download_source_state_from_path, path
            )
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return True
        except OSError as e:
            raise AppException(message=f'Failed to stat file during download: {str(e)}')

        return current_state != expected_state

    @trace_api('file')
    async def list_path(
        self,
        path: str,
        recursive: bool = False,
        show_hidden: bool = False,
        file_types: Optional[list[str]] = None,
        max_depth: Optional[int] = None,
        include_size: bool = True,
        include_permissions: bool = False,
        sort_by: str = 'name',
        sort_desc: bool = False,
        max_files: Optional[int] = 10000,  # Prevent memory issues
    ) -> FileListResult:
        """
        Asynchronously list path contents with flexible options

        Args:
            path: Path to list (directory or file)
            recursive: Whether to list recursively
            show_hidden: Whether to show hidden files
            file_types: Filter by file extensions (e.g., ['.py', '.txt'])
            max_depth: Maximum depth for recursive listing
            include_size: Whether to include file size information
            include_permissions: Whether to include file permissions
            sort_by: Sort by: name, size, modified, type
            sort_desc: Sort in descending order
            max_files: Maximum number of files to process (default: 10000)
        """
        try:
            self._ensure_directory_path(path)

            # Normalize file types for faster comparison
            # Accept both 'py' and '.py' formats, normalize to '.py'
            normalized_file_types = None
            if file_types:
                normalized_file_types = set()
                for ft in file_types:
                    ft_lower = ft.lower()
                    # Add dot prefix if not present
                    if not ft_lower.startswith('.'):
                        ft_lower = '.' + ft_lower
                    normalized_file_types.add(ft_lower)

            def list_files():
                files = []
                file_count = 0

                def process_directory(current_path: str, current_depth: int = 0):
                    nonlocal file_count

                    if max_depth is not None and current_depth > max_depth:
                        return

                    if max_files and file_count >= max_files:
                        return

                    try:
                        dir_entries = os.scandir(current_path)

                        for entry in dir_entries:
                            # Early termination check
                            if max_files and file_count >= max_files:
                                dir_entries.close()
                                return

                            # Skip hidden files if not requested
                            if not show_hidden and entry.name.startswith('.'):
                                continue

                            # Early file type filtering (before stat)
                            if normalized_file_types and not entry.is_dir():
                                _, ext = os.path.splitext(entry.name)
                                if ext.lower() not in normalized_file_types:
                                    continue

                            try:
                                # Use scandir's cached stat info when possible
                                file_stat = entry.stat()
                                is_directory = entry.is_dir()

                                # Create file info with minimal operations
                                file_info = FileInfo(
                                    name=entry.name,
                                    path=entry.path,
                                    is_directory=is_directory,
                                    size=(
                                        file_stat.st_size
                                        if include_size and not is_directory
                                        else None
                                    ),
                                    modified_time=(
                                        str(int(file_stat.st_mtime))
                                        if include_size
                                        else None
                                    ),
                                    permissions=(
                                        stat.filemode(file_stat.st_mode)
                                        if include_permissions
                                        else None
                                    ),
                                    extension=(
                                        os.path.splitext(entry.name)[1]
                                        if not is_directory
                                        else None
                                    ),
                                )

                                files.append(file_info)
                                file_count += 1

                                # Recursively process subdirectories
                                if recursive and is_directory:
                                    process_directory(entry.path, current_depth + 1)

                            except (OSError, PermissionError):
                                # Skip files that can't be accessed
                                continue

                        dir_entries.close()

                    except (OSError, PermissionError):
                        # Skip directories that can't be accessed
                        pass

                process_directory(path)
                return files

            # Execute file listing - use thread pool only for large operations
            if recursive or (max_files and max_files > 1000):
                files = await asyncio.to_thread(list_files)
            else:
                files = list_files()
        except OSError as e:
            self._raise_if_file_tool_error('list', path, e)
            raise AppException(message=f'Failed to list path: {str(e)}')

        # Sort files
        def sort_key(file_info: FileInfo):
            if sort_by == 'name':
                return file_info.name.lower()
            elif sort_by == 'size':
                return file_info.size or 0
            elif sort_by == 'modified':
                return file_info.modified_time or ''
            elif sort_by == 'type':
                return (0 if file_info.is_directory else 1, file_info.name.lower())
            else:
                return file_info.name.lower()

        files.sort(key=sort_key, reverse=sort_desc)

        # Calculate statistics
        total_count = len(files)
        directory_count = sum(1 for f in files if f.is_directory)
        file_count = total_count - directory_count

        return FileListResult(
            path=path,
            files=files,
            total_count=total_count,
            directory_count=directory_count,
            file_count=file_count,
        )

    @trace_api('file')
    async def grep(
        self,
        path: str,
        pattern: str,
        include: Optional[list[str]] = None,
        exclude: Optional[list[str]] = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        context_before: int = 0,
        context_after: int = 0,
        max_results: int = 500,
        max_file_size: str = '1M',
        multiline: bool = False,
        offset: int = 0,
        file_type: Optional[str] = None,
        recursive: bool = True,
    ) -> FileGrepResult:
        """
        Multi-file content search using ripgrep or Python fallback.

        Args:
            path: Directory path to search
            pattern: Search pattern (regex or fixed string)
            include: File glob filters to include
            exclude: Glob patterns to exclude
            case_insensitive: Case insensitive search
            fixed_strings: Treat pattern as literal string
            context_before: Lines before each match
            context_after: Lines after each match
            max_results: Maximum number of matches
            max_file_size: Skip files larger than this
            multiline: Enable multiline matching (rg -U --multiline-dotall)
            offset: Skip first N matches before returning results
            file_type: File type filter using ripgrep type aliases (e.g., "py", "js")
            recursive: Search recursively
        """
        try:
            self._ensure_existing_path(path)
        except OSError as e:
            self._raise_if_file_tool_error('grep', path, e)
            raise AppException(message=f'Failed to grep path: {str(e)}')

        # Validate regex pattern
        if not fixed_strings:
            try:
                flags = re.DOTALL if multiline else 0
                re.compile(pattern, flags)
            except re.error as e:
                raise BadRequestException(f'Invalid regular expression: {str(e)}')

        rg_path = self._check_rg_available()
        if rg_path:
            return await self._grep_with_rg(
                rg_path=rg_path,
                path=path,
                pattern=pattern,
                include=include,
                exclude=exclude,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
                context_before=context_before,
                context_after=context_after,
                max_results=max_results,
                max_file_size=max_file_size,
                multiline=multiline,
                offset=offset,
                file_type=file_type,
                recursive=recursive,
            )
        else:
            return await self._grep_with_python(
                path=path,
                pattern=pattern,
                include=include,
                exclude=exclude,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
                context_before=context_before,
                context_after=context_after,
                max_results=max_results,
                max_file_size=max_file_size,
                multiline=multiline,
                offset=offset,
                file_type=file_type,
                recursive=recursive,
            )

    async def _grep_with_rg(
        self,
        rg_path: str,
        path: str,
        pattern: str,
        include: Optional[list[str]],
        exclude: Optional[list[str]],
        case_insensitive: bool,
        fixed_strings: bool,
        context_before: int,
        context_after: int,
        max_results: int,
        max_file_size: str,
        multiline: bool,
        offset: int,
        file_type: Optional[str],
        recursive: bool,
    ) -> FileGrepResult:
        """Grep using ripgrep."""
        cmd = [rg_path, '--json', '--sort', 'path']

        if case_insensitive:
            cmd.append('-i')
        if fixed_strings:
            cmd.append('-F')
        if multiline:
            cmd.extend(['-U', '--multiline-dotall'])
        if not recursive:
            cmd.append('--max-depth=1')
        if context_before > 0:
            cmd.extend(['-B', str(context_before)])
        if context_after > 0:
            cmd.extend(['-A', str(context_after)])
        if max_file_size:
            cmd.extend(['--max-filesize', max_file_size])
        if file_type:
            cmd.extend(['--type', file_type])
        if include:
            for inc in include:
                cmd.extend(['--glob', inc])
        if exclude:
            for exc in exclude:
                cmd.extend(['--glob', f'!{exc}'])

        cmd.extend([pattern, path])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        # rg exit code: 0=matches found, 1=no matches, 2=error
        if process.returncode == 2:
            # If stdout has data, we got partial results (e.g. permission errors
            # on some dirs) — continue parsing. Only raise if no results at all.
            if not stdout.strip():
                error_msg = stderr.decode('utf-8', errors='replace').strip()
                raise BadRequestException(f'ripgrep error: {error_msg}')

        matches: list[GrepMatch] = []
        files_matched_set: set[str] = set()
        truncated = False
        seen_count = 0
        collect_limit = offset + max_results

        for line in stdout.decode('utf-8', errors='replace').splitlines():
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get('type')
            if msg_type == 'match':
                data = msg['data']
                file_path = data['path']['text']
                files_matched_set.add(file_path)

                if seen_count >= collect_limit:
                    truncated = True
                    break

                if seen_count >= offset:
                    line_num = data['line_number']
                    line_text = data['lines']['text'].rstrip('\n')
                    matches.append(
                        GrepMatch(
                            file=file_path,
                            line_number=line_num,
                            line_content=line_text,
                        )
                    )
                seen_count += 1
            elif msg_type == 'context':
                pass

        # Parse summary for files_searched if available
        files_searched = None
        for line in stdout.decode('utf-8', errors='replace').splitlines():
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get('type') == 'summary':
                stats = msg.get('data', {}).get('stats', {})
                files_searched = stats.get('searches', None)
                break

        return FileGrepResult(
            path=path,
            pattern=pattern,
            matches=matches,
            match_count=len(matches),
            files_searched=files_searched,
            files_matched=len(files_matched_set),
            truncated=truncated,
        )

    async def _grep_with_python(
        self,
        path: str,
        pattern: str,
        include: Optional[list[str]],
        exclude: Optional[list[str]],
        case_insensitive: bool,
        fixed_strings: bool,
        context_before: int,
        context_after: int,
        max_results: int,
        max_file_size: str,
        multiline: bool,
        offset: int,
        file_type: Optional[str],
        recursive: bool,
    ) -> FileGrepResult:
        """Pure Python grep fallback."""

        def _parse_size(size_str: str) -> int:
            """Parse size string like '1M', '500K' to bytes."""
            size_str = size_str.strip().upper()
            multipliers = {'K': 1024, 'M': 1024 * 1024, 'G': 1024 * 1024 * 1024}
            for suffix, mult in multipliers.items():
                if size_str.endswith(suffix):
                    return int(float(size_str[:-1]) * mult)
            return int(size_str)

        def _is_binary(file_path: str) -> bool:
            """Check if file is binary by looking for null bytes in first 8KB."""
            try:
                with open(file_path, 'rb') as f:
                    chunk = f.read(8192)
                    return b'\x00' in chunk
            except (OSError, PermissionError):
                return True

        def _matches_glob(name: str, patterns: list[str]) -> bool:
            return any(fnmatch.fnmatch(name, p) for p in patterns)

        max_bytes = _parse_size(max_file_size)
        if file_type:
            rg_types = self._get_rg_type_extensions()
            type_exts = rg_types.get(file_type) if rg_types else None
            if not type_exts:
                # Fallback: treat type name as extension (e.g. "py" → [".py"])
                type_exts = [f'.{file_type}']
        else:
            type_exts = None

        # Compile pattern
        if fixed_strings:
            if case_insensitive:
                pattern_lower = pattern.lower()

                def search_fn(text: str) -> bool:
                    return pattern_lower in text.lower()
            else:
                def search_fn(text: str) -> bool:
                    return pattern in text
        else:
            flags = re.IGNORECASE if case_insensitive else 0
            if multiline:
                flags |= re.DOTALL
            compiled = re.compile(pattern, flags)

            def search_fn(text: str) -> bool:
                return compiled.search(text) is not None

        def do_grep():
            matches: list[GrepMatch] = []
            files_searched = 0
            files_matched_set: set[str] = set()
            truncated = False
            seen_count = 0
            collect_limit = offset + max_results

            if os.path.isfile(path):
                walker = [(os.path.dirname(path), [], [os.path.basename(path)])]
            elif recursive:
                walker = os.walk(path)
            else:
                walker = [(path, [], os.listdir(path))]
            for dirpath, dirnames, filenames in walker:
                dirnames.sort()
                filenames.sort()
                for filename in filenames:
                    if truncated:
                        break

                    # Type filter
                    if type_exts:
                        if not any(filename.endswith(ext) for ext in type_exts):
                            continue

                    # Include filter
                    if include and not _matches_glob(filename, include):
                        continue

                    # Exclude filter
                    if exclude and _matches_glob(filename, exclude):
                        continue

                    filepath = os.path.join(dirpath, filename)

                    # Size check
                    try:
                        if os.path.getsize(filepath) > max_bytes:
                            continue
                    except OSError:
                        continue

                    # Binary check
                    if _is_binary(filepath):
                        continue

                    files_searched += 1

                    try:
                        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                            if multiline:
                                content = f.read()
                            else:
                                lines = f.readlines()
                    except (OSError, PermissionError):
                        continue

                    if multiline:
                        # Multiline: search full content, report first line of match
                        for m in compiled.finditer(content):
                            line_num = content[:m.start()].count('\n') + 1
                            match_text = m.group().split('\n')[0]

                            files_matched_set.add(filepath)

                            if seen_count >= collect_limit:
                                truncated = True
                                break

                            if seen_count >= offset:
                                matches.append(
                                    GrepMatch(
                                        file=filepath,
                                        line_number=line_num,
                                        line_content=match_text,
                                    )
                                )
                            seen_count += 1
                    else:
                        for i, line in enumerate(lines):
                            if search_fn(line):
                                files_matched_set.add(filepath)

                                if seen_count >= collect_limit:
                                    truncated = True
                                    break

                                if seen_count >= offset:
                                    ctx_before_list = None
                                    ctx_after_list = None
                                    if context_before > 0:
                                        start = max(0, i - context_before)
                                        ctx_before_list = [
                                            ln.rstrip('\n') for ln in lines[start:i]
                                        ]
                                    if context_after > 0:
                                        end = min(len(lines), i + 1 + context_after)
                                        ctx_after_list = [
                                            ln.rstrip('\n') for ln in lines[i + 1 : end]
                                        ]

                                    matches.append(
                                        GrepMatch(
                                            file=filepath,
                                            line_number=i + 1,
                                            line_content=line.rstrip('\n'),
                                            context_before=ctx_before_list,
                                            context_after=ctx_after_list,
                                        )
                                    )
                                seen_count += 1

                if truncated:
                    break

            return matches, files_searched, len(files_matched_set), truncated

        matches, files_searched, files_matched, truncated = await asyncio.to_thread(
            do_grep
        )

        return FileGrepResult(
            path=path,
            pattern=pattern,
            matches=matches,
            match_count=len(matches),
            files_searched=files_searched,
            files_matched=files_matched,
            truncated=truncated,
        )

    @trace_api('file')
    async def glob_files(
        self,
        path: str,
        pattern: str,
        exclude: Optional[list[str]] = None,
        include_hidden: bool = False,
        files_only: bool = True,
        include_metadata: bool = True,
        max_results: int = 5000,
        sort_by: str = 'path',
        sort_desc: bool = False,
    ) -> FileGlobResult:
        """
        Enhanced file glob matching with metadata support.

        Args:
            path: Base directory path
            pattern: Glob pattern
            exclude: Glob patterns to exclude
            include_hidden: Whether to include hidden files
            files_only: Only return files
            include_metadata: Whether to include size/mtime
            max_results: Maximum number of results
            sort_by: Sort by: path, name, size, modified
            sort_desc: Sort in descending order
        """
        try:
            self._ensure_directory_path(path)
        except OSError as e:
            self._raise_if_file_tool_error('glob', path, e)
            raise AppException(message=f'Failed to glob path: {str(e)}')

        def do_glob():
            base = Path(path)
            results: list[GlobFileInfo] = []
            truncated = False

            for match in base.glob(pattern):
                if len(results) >= max_results:
                    truncated = True
                    break

                # Skip hidden files
                if not include_hidden:
                    if any(part.startswith('.') for part in match.relative_to(base).parts):
                        continue

                # Skip directories if files_only
                is_dir = match.is_dir()
                if files_only and is_dir:
                    continue

                # Exclude filter
                if exclude:
                    rel_str = str(match.relative_to(base))
                    name = match.name
                    if any(
                        fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_str, pat)
                        for pat in exclude
                    ):
                        continue

                size = None
                modified_time = None
                if include_metadata:
                    try:
                        st = match.stat()
                        if not is_dir:
                            size = st.st_size
                        modified_time = str(int(st.st_mtime))
                    except OSError:
                        pass

                results.append(
                    GlobFileInfo(
                        path=str(match),
                        name=match.name,
                        is_directory=is_dir,
                        size=size,
                        modified_time=modified_time,
                    )
                )

            return results, truncated

        results, truncated = await asyncio.to_thread(do_glob)

        # Sort
        def sort_key(info: GlobFileInfo):
            if sort_by == 'name':
                return info.name.lower()
            elif sort_by == 'size':
                return info.size or 0
            elif sort_by == 'modified':
                return info.modified_time or ''
            else:
                return info.path.lower()

        results.sort(key=sort_key, reverse=sort_desc)

        return FileGlobResult(
            path=path,
            pattern=pattern,
            files=results,
            total_count=len(results),
            truncated=truncated,
        )

    @trace_api('file')
    async def str_replace_editor(
        self,
        command: str,
        path: str,
        file_text: Optional[str] = None,
        old_str: Optional[str] = None,
        new_str: Optional[str] = None,
        insert_line: Optional[int] = None,
        view_range: Optional[list[int]] = [],
        replace_mode: Optional[str] = None,
        # Binary file pagination parameters
        page_range: Optional[list[int]] = None,
        sheet_name: Optional[str] = None,
        row_range: Optional[list[int]] = None,
        slide_range: Optional[list[int]] = None,
        enable_metadata: bool = False,
    ) -> StrReplaceEditorResult:
        """
        使用 openhands_aci 编辑器执行文件操作

        Args:
            command: 要执行的命令 (view, create, str_replace, insert, undo_edit)
            path: 文件路径
            file_text: create 命令需要的文件内容
            old_str: str_replace 命令需要的原字符串
            new_str: str_replace 和 insert 命令需要的新字符串
            insert_line: insert 命令需要的插入行号
            view_range: view 命令的可选行范围
            replace_mode: str_replace 命令的替换模式 ('ALL', 'FIRST', 'LAST')
            page_range: PDF 文件的页码范围 [start, end] (1-indexed)
            sheet_name: Excel 文件的 sheet 名称
            row_range: Excel 文件的行范围 [start, end] (1-indexed)
            slide_range: PPTX 文件的 slide 范围 [start, end] (1-indexed)
            enable_metadata: 是否返回文件元信息
        """
        try:
            from openhands_aci.editor.exceptions import ToolError

            editor = await editor_manager.get_editor(path)

            # 准备参数
            kwargs = {}
            if file_text is not None:
                kwargs['file_text'] = file_text
            if old_str is not None:
                kwargs['old_str'] = old_str
            if new_str is not None:
                kwargs['new_str'] = new_str
            if insert_line is not None:
                kwargs['insert_line'] = insert_line
            if len(view_range) > 0:
                kwargs['view_range'] = view_range
            if replace_mode is not None:
                kwargs['replace_mode'] = replace_mode
            # Binary file pagination parameters
            if page_range is not None:
                kwargs['page_range'] = page_range
            if sheet_name is not None:
                kwargs['sheet_name'] = sheet_name
            if row_range is not None:
                kwargs['row_range'] = row_range
            if slide_range is not None:
                kwargs['slide_range'] = slide_range
            if enable_metadata:
                kwargs['enable_metadata'] = enable_metadata

            # 在线程池中执行编辑器操作
            def run_editor():
                # Patch: 如果是 create 命令，确保父目录存在
                if command == 'create':
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                return editor(command=command, path=path, **kwargs)

            # 异步执行编辑器操作
            result = await asyncio.to_thread(run_editor)

            # 转换 metadata 如果存在
            from app.models.file import FileMetadata
            metadata = None
            if hasattr(result, 'metadata') and result.metadata:
                metadata = FileMetadata(
                    file_type=result.metadata.get('file_type', 'unknown'),
                    file_path=result.metadata.get('file_path', ''),
                    file_size=result.metadata.get('file_size', 0),
                    total_pages=result.metadata.get('total_pages'),
                    current_page_range=result.metadata.get('current_page_range'),
                    sheets=result.metadata.get('sheets'),
                    current_sheet=result.metadata.get('current_sheet'),
                    total_rows=result.metadata.get('total_rows'),
                    current_row_range=result.metadata.get('current_row_range'),
                    total_slides=result.metadata.get('total_slides'),
                    current_slide_range=result.metadata.get('current_slide_range'),
                    total_paragraphs=result.metadata.get('total_paragraphs'),
                )

            # 转换结果为我们的模型
            return StrReplaceEditorResult(
                output=result.output,
                error=getattr(result, 'error', None),
                path=result.path,
                prev_exist=result.prev_exist,
                old_content=getattr(result, 'old_content', None),
                new_content=getattr(result, 'new_content', None),
                metadata=metadata,
            )

        except ToolError as e:
            error_msg = str(e)
            original = e.__cause__ or e.__context__

            # Check the original exception type from the exception chain
            # All OS-level errors are client errors (invalid path, permission denied, etc.)
            if isinstance(original, OSError):
                self._raise_if_file_tool_error('str_replace_editor', path, original)
                raise BadRequestException(message=error_msg)

            # Categorize ToolError based on the error message
            if any(
                keyword in error_msg.lower()
                for keyword in ['missing', 'parameter', 'required']
            ):
                raise BadRequestException(message=error_msg)
            elif any(
                keyword in error_msg.lower()
                for keyword in ['invalid', 'not found', 'does not exist', 'nonexistent']
            ):
                raise BadRequestException(message=error_msg)
            elif any(
                keyword in error_msg.lower()
                for keyword in [
                    'multiple occurrences',
                    'no replacement',
                    'must be different',
                ]
            ):
                raise BadRequestException(message=error_msg)
            elif any(
                keyword in error_msg.lower()
                for keyword in ['no edit history', 'history found', 'undo']
            ):
                raise BadRequestException(message=error_msg)
            else:
                raise AppException(message=f'[str_replace_editor] error: {error_msg}')
        except OSError as e:
            self._raise_if_file_tool_error('str_replace_editor', path, e)
            raise BadRequestException(message=f'[str_replace_editor] error: {str(e)}')
        except Exception as e:
            raise AppException(message=f'[str_replace_editor] error: {str(e)}')

    # ---- File Sync (rsync + lsyncd) ----

    @staticmethod
    def _parse_rsync_stats(output: str) -> tuple[int, int, Optional[str]]:
        """Parse rsync --stats output. Returns (files_transferred, bytes_transferred, speed)."""
        files = 0
        bytes_val = 0
        speed = None

        for line in output.splitlines():
            line = line.strip()
            if 'files transferred:' in line:
                m = re.search(r'(\d[\d,]*)', line.split('transferred:')[1])
                if m:
                    files = int(m.group(1).replace(',', ''))
            elif 'Total transferred file size:' in line:
                m = re.search(r'([\d,]+)', line.split(':')[1])
                if m:
                    bytes_val = int(m.group(1).replace(',', ''))
            elif 'bytes/sec' in line:
                m = re.search(r'([\d,.]+)\s+bytes/sec', line)
                if m:
                    bps = float(m.group(1).replace(',', ''))
                    if bps >= 1_000_000:
                        speed = f'{bps / 1_000_000:.2f} MB/s'
                    elif bps >= 1_000:
                        speed = f'{bps / 1_000:.2f} KB/s'
                    else:
                        speed = f'{bps:.0f} B/s'
        return files, bytes_val, speed

    def _generate_lsyncd_config(
        self, watcher_id: str, source: str, target: str, delay: int, exclude: list[str]
    ) -> str:
        config_path = f'/tmp/lsyncd-{watcher_id}.conf.lua'
        exclude_lua = ', '.join(f'"{e}"' for e in exclude) if exclude else ''
        exclude_block = f'    exclude = {{ {exclude_lua} }},' if exclude else ''

        config = f'''settings {{
    logfile    = "/tmp/lsyncd-{watcher_id}.log",
    statusFile = "/tmp/lsyncd-{watcher_id}.status",
    nodaemon   = true,
    insist     = true,
    maxProcesses = 2,
}}

sync {{
    default.rsync,
    source = "{source}",
    target = "{target}",
    delay  = {delay},
{exclude_block}
    rsync = {{
        archive  = true,
        compress = false,
    }},
}}
'''
        with open(config_path, 'w') as f:
            f.write(config)
        return config_path

    @trace_api('file')
    async def rsync_sync(
        self,
        source: str,
        target: str,
        exclude: Optional[list[str]] = None,
        delete: bool = False,
        dry_run: bool = False,
    ) -> FileSyncResult:
        """Execute one-time rsync file sync."""
        self._ensure_existing_path(source)

        cmd = ['rsync', '-av', '--no-owner', '--no-group', '--stats']
        if delete:
            cmd.append('--delete')
        if dry_run:
            cmd.append('--dry-run')
        if exclude:
            for pattern in exclude:
                cmd.extend(['--exclude', pattern])
        cmd.extend([source, target])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode('utf-8', errors='replace')

        if process.returncode != 0 and process.returncode != 23:
            error = stderr.decode('utf-8', errors='replace')
            raise AppException(message=f'rsync failed (exit {process.returncode}): {error}')

        files, bytes_val, speed = self._parse_rsync_stats(output)

        return FileSyncResult(
            source=source,
            target=target,
            files_transferred=files,
            bytes_transferred=bytes_val,
            speed=speed,
            output=output,
            dry_run=dry_run,
        )

    @trace_api('file')
    async def lsyncd_start(
        self,
        source: str,
        target: str,
        delay: int = 15,
        exclude: Optional[list[str]] = None,
        origin: str = 'api',
    ) -> FileSyncWatchResult:
        """Start lsyncd continuous file sync watcher."""
        self._ensure_directory_path(source)

        async with self._lsyncd_lock:
            # Check duplicate
            for w in self._lsyncd_processes.values():
                if w.source == source and w.target == target and w.process.returncode is None:
                    return FileSyncWatchResult(
                        id=w.id, source=w.source, target=w.target,
                        delay=w.delay, status='running', pid=w.process.pid, origin=w.origin,
                    )

            # Check max
            active = sum(1 for w in self._lsyncd_processes.values() if w.process.returncode is None)
            if active >= self.MAX_WATCHERS:
                raise BadRequestException(f'Maximum watchers ({self.MAX_WATCHERS}) reached')

            watcher_id = f'watch_{os.urandom(6).hex()}'
            config_path = self._generate_lsyncd_config(
                watcher_id, source, target, delay, exclude or [],
            )

            process = await asyncio.create_subprocess_exec(
                'lsyncd', '-nodaemon', config_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Brief wait to check immediate failure
            await asyncio.sleep(0.3)
            if process.returncode is not None:
                stderr_data = await process.stderr.read()
                raise AppException(
                    message=f'lsyncd failed to start: {stderr_data.decode(errors="replace")}'
                )

            watcher = LsyncdWatcher(
                id=watcher_id, source=source, target=target, delay=delay,
                exclude=exclude or [], process=process, config_path=config_path, origin=origin,
            )
            self._lsyncd_processes[watcher_id] = watcher

        return FileSyncWatchResult(
            id=watcher_id, source=source, target=target,
            delay=delay, status='running', pid=process.pid, origin=origin,
        )

    async def lsyncd_status(self) -> FileSyncWatchListResult:
        """Check all watcher statuses."""
        watchers = []
        for w in self._lsyncd_processes.values():
            status = 'running' if w.process.returncode is None else 'stopped'
            watchers.append(FileSyncWatchResult(
                id=w.id, source=w.source, target=w.target,
                delay=w.delay, status=status,
                pid=w.process.pid if status == 'running' else None,
                origin=w.origin,
            ))
        return FileSyncWatchListResult(watchers=watchers, total=len(watchers))

    @trace_api('file')
    async def lsyncd_stop(self, watcher_id: Optional[str] = None) -> FileSyncWatchStopResult:
        """Stop lsyncd watcher(s)."""
        stopped = []
        async with self._lsyncd_lock:
            targets = (
                [watcher_id] if watcher_id
                else list(self._lsyncd_processes.keys())
            )
            for wid in targets:
                watcher = self._lsyncd_processes.pop(wid, None)
                if watcher is None:
                    continue
                if watcher.process.returncode is None:
                    watcher.process.terminate()
                    try:
                        await asyncio.wait_for(watcher.process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        watcher.process.kill()
                        await watcher.process.wait()
                try:
                    os.unlink(watcher.config_path)
                except OSError:
                    pass
                stopped.append(wid)
        return FileSyncWatchStopResult(stopped=stopped)

    async def start_env_watchers(self) -> None:
        """Start lsyncd watchers from environment configuration."""
        rules_path = '/tmp/lsyncd-env-rules.json'
        if not os.path.exists(rules_path):
            return

        try:
            with open(rules_path) as f:
                rules = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f'Failed to load env sync rules: {e}')
            return

        for i, rule in enumerate(rules):
            try:
                await self.lsyncd_start(
                    source=rule['source'],
                    target=rule['target'],
                    delay=rule.get('delay', 15),
                    exclude=rule.get('exclude'),
                    origin='env',
                )
                logger.info(f'Started env watcher {i}: {rule["source"]} → {rule["target"]}')
            except Exception as e:
                logger.warning(f'Failed to start env watcher {i}: {e}')

    async def cleanup_all_watchers(self) -> None:
        """Stop all lsyncd watchers. Called during shutdown."""
        await self.lsyncd_stop(watcher_id=None)
