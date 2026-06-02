"""
File operation request models
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class FileReadRequest(BaseModel):
    """File read request"""

    file: str = Field(..., description='Absolute file path')
    start_line: Optional[int] = Field(None, description='Start line (0-based)')
    end_line: Optional[int] = Field(None, description='End line (not inclusive)')
    sudo: Optional[bool] = Field(False, description='Whether to use sudo privileges')


class FileContentEncoding(str, Enum):
    """File content encoding type"""

    UTF8 = 'utf-8'
    BASE64 = 'base64'
    RAW = 'raw'


class FileWriteRequest(BaseModel):
    """File write request - supports both text and binary content"""

    file: str = Field(..., description='Absolute file path')
    content: str = Field(
        ..., description='Content to write (text or base64 encoded for binary)'
    )
    encoding: Optional[FileContentEncoding] = Field(
        FileContentEncoding.UTF8,
        description='Content encoding: utf-8 for text, base64 for binary data',
    )
    append: Optional[bool] = Field(False, description='Whether to use append mode')
    leading_newline: Optional[bool] = Field(
        False, description='Whether to add leading newline (only for text mode)'
    )
    trailing_newline: Optional[bool] = Field(
        False, description='Whether to add trailing newline (only for text mode)'
    )
    sudo: Optional[bool] = Field(False, description='Whether to use sudo privileges')


class FileReplaceRequest(BaseModel):
    """File content replacement request"""

    file: str = Field(..., description='Absolute file path')
    old_str: str = Field(..., description='Original string to replace')
    new_str: str = Field(..., description='New string to replace with')
    sudo: Optional[bool] = Field(False, description='Whether to use sudo privileges')


class FileSearchRequest(BaseModel):
    """File content search request"""

    file: str = Field(..., description='Absolute file path')
    regex: str = Field(..., description='Regular expression pattern')
    sudo: Optional[bool] = Field(False, description='Whether to use sudo privileges')


class FileFindRequest(BaseModel):
    """File find request"""

    path: str = Field(..., description='Directory path to search')
    glob: str = Field(..., description='Filename pattern (glob syntax)')


class FileListRequest(BaseModel):
    """File list request"""

    path: str = Field(..., description='Directory path to list')
    recursive: Optional[bool] = Field(False, description='Whether to list recursively')
    show_hidden: Optional[bool] = Field(
        True, description='Whether to show hidden files'
    )
    file_types: Optional[list[str]] = Field(
        None, description="Filter by file extensions (e.g., ['.py', '.txt'])"
    )
    max_depth: Optional[int] = Field(
        None, description='Maximum depth for recursive listing'
    )
    include_size: Optional[bool] = Field(
        True, description='Whether to include file size information'
    )
    include_permissions: Optional[bool] = Field(
        False, description='Whether to include file permissions'
    )
    sort_by: Optional[str] = Field(
        'name', description='Sort by: name, size, modified, type'
    )
    sort_desc: Optional[bool] = Field(False, description='Sort in descending order')


class FileGrepRequest(BaseModel):
    """File grep (multi-file content search) request"""

    path: str = Field(..., description='File or directory path to search')
    pattern: str = Field(..., description='Search pattern (regex or fixed string)')
    include: Optional[list[str]] = Field(
        None, description='File glob filters to include (e.g., ["*.py", "*.ts"])'
    )
    exclude: Optional[list[str]] = Field(
        None,
        description='Glob patterns to exclude (e.g., ["node_modules", "*.min.js"])',
    )
    case_insensitive: Optional[bool] = Field(
        False, description='Case insensitive search'
    )
    fixed_strings: Optional[bool] = Field(
        False, description='Treat pattern as literal string, not regex'
    )
    context_before: Optional[int] = Field(
        0, ge=0, le=20, description='Number of lines before each match (-B)'
    )
    context_after: Optional[int] = Field(
        0, ge=0, le=20, description='Number of lines after each match (-A)'
    )
    max_results: Optional[int] = Field(
        500, ge=1, le=10000, description='Maximum number of matches to return'
    )
    max_file_size: Optional[str] = Field(
        '1M', description='Skip files larger than this size (e.g., 1M, 500K)'
    )
    multiline: Optional[bool] = Field(
        False,
        description='Enable multiline matching where . matches newlines and patterns can span lines (rg -U --multiline-dotall)',
    )
    offset: Optional[int] = Field(
        0, ge=0, description='Skip first N matches before returning results (for pagination)'
    )
    type: Optional[str] = Field(
        None,
        description='File type filter using ripgrep type aliases (e.g., "py", "js", "rust", "go"). Maps to rg --type.',
    )
    recursive: Optional[bool] = Field(True, description='Search recursively')


class FileGlobRequest(BaseModel):
    """Enhanced file glob request"""

    path: str = Field(..., description='Base directory path')
    pattern: str = Field(..., description='Glob pattern (**, *, ?, [...])')
    exclude: Optional[list[str]] = Field(
        None, description='Glob patterns to exclude'
    )
    include_hidden: Optional[bool] = Field(
        False, description='Whether to include hidden files'
    )
    files_only: Optional[bool] = Field(
        True, description='Only return files (not directories)'
    )
    include_metadata: Optional[bool] = Field(
        True, description='Whether to include size and modified time'
    )
    max_results: Optional[int] = Field(
        5000, ge=1, le=50000, description='Maximum number of results'
    )
    sort_by: Optional[str] = Field(
        'path', description='Sort by: path, name, size, modified'
    )
    sort_desc: Optional[bool] = Field(False, description='Sort in descending order')


class FileSyncRequest(BaseModel):
    """File sync request (rsync)"""

    source: str = Field(..., description='Source path (trailing / to sync contents)')
    target: str = Field(..., description='Target path')
    exclude: Optional[list[str]] = Field(
        None, description='Patterns to exclude (rsync --exclude syntax)'
    )
    delete: bool = Field(
        False, description='Delete extraneous files from target (rsync --delete)'
    )
    dry_run: bool = Field(
        False,
        description='Show what would be transferred without actually doing it',
    )


class FileSyncWatchRequest(BaseModel):
    """File sync watch request (lsyncd)"""

    source: str = Field(..., description='Source directory to watch')
    target: str = Field(..., description='Target directory to sync to')
    delay: int = Field(
        15, ge=1, le=3600, description='Seconds to batch changes before syncing'
    )
    exclude: Optional[list[str]] = Field(
        None, description='Patterns to exclude from watching/syncing'
    )


class FileSyncWatchStopRequest(BaseModel):
    """File sync watch stop request"""

    id: Optional[str] = Field(
        None, description='Watcher ID to stop. If omitted, stop all watchers.'
    )


class StrReplaceEditorRequest(BaseModel):
    """String replace editor request based on openhands_aci"""

    command: Literal[
        'view',
        'create',
        'str_replace',
        'insert',
        'undo_edit',
    ] = Field(
        ...,
        description='The commands to run. Allowed options are: `view`, `create`, `str_replace`, `insert`, `undo_edit`.',
    )

    path: str = Field(
        ...,
        description='Absolute path to file or directory, e.g. `/workspace/file.py` or `/workspace`.',
    )
    file_text: Optional[str] = Field(
        None,
        description='Required parameter of `create` command, with the content of the file to be created.',
    )
    old_str: Optional[str] = Field(
        None,
        description='Required parameter of `str_replace` command containing the string in `path` to replace.',
    )
    new_str: Optional[str] = Field(
        None,
        description='Optional parameter of `str_replace` command containing the new string (if not given, no string will be added). Required parameter of `insert` command containing the string to insert.',
    )
    insert_line: Optional[int] = Field(
        None,
        description='Required parameter of `insert` command. The `new_str` will be inserted AFTER the line `insert_line` of `path`.',
    )
    view_range: Optional[list[int]] = Field(
        [],
        description='Optional parameter of `view` command when `path` points to a file. If none is given, the full file is shown. If provided, the file will be shown in the indicated line number range, e.g. [11, 12] will show lines 11 and 12. Indexing at 1 to start. Setting `[start_line, -1]` shows all lines from `start_line` to the end of the file.',
    )
    replace_mode: Optional[Literal['ALL', 'FIRST', 'LAST']] = Field(
        None,
        description="Optional parameter of `str_replace` command. When specified, controls how multiple occurrences are handled: 'ALL' replaces all occurrences, 'FIRST' replaces only the first, 'LAST' replaces only the last. If not specified, requires unique match (original behavior).",
    )
    # Binary file pagination parameters
    page_range: Optional[list[int]] = Field(
        None,
        description='Optional parameter for `view` command on PDF files. Specifies page range [start, end] (1-indexed). E.g., [1, 5] reads pages 1-5.',
    )
    sheet_name: Optional[str] = Field(
        None,
        description='Optional parameter for `view` command on Excel files. Specifies which sheet to read. If not provided, all sheets are returned.',
    )
    row_range: Optional[list[int]] = Field(
        None,
        description='Optional parameter for `view` command on Excel files. Specifies row range [start, end] (1-indexed). E.g., [1, 100] reads rows 1-100.',
    )
    slide_range: Optional[list[int]] = Field(
        None,
        description='Optional parameter for `view` command on PPTX files. Specifies slide range [start, end] (1-indexed). E.g., [1, 5] reads slides 1-5.',
    )
    enable_metadata: Optional[bool] = Field(
        False,
        description='Optional parameter for `view` command. If true, returns file metadata (total pages, sheets, slides, etc.) in the response.',
    )
