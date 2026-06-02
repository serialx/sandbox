"""
File operation related models
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field, field_serializer
from typing import Any, Dict, List, Optional


class FileDownloadChangePolicy(str, Enum):
    """How the download endpoint reacts when the source file changes."""

    IGNORE = 'ignore'
    ABORT = 'abort'


class FileReadResult(BaseModel):
    """File read result"""

    content: str = Field(..., description='File content')
    file: str = Field(..., description='Path of the read file')


class FileWriteResult(BaseModel):
    """File write result"""

    file: str = Field(..., description='Path of the written file')
    bytes_written: Optional[int] = Field(None, description='Number of bytes written')


class FileOperationError(BaseModel):
    """Structured file-tool execution error."""

    path: str = Field(..., description='Path used for the file operation')
    operation: str = Field(..., description='File operation name')
    message: str = Field(..., description='Human-readable error message')
    error_type: str = Field(..., description='Normalized file error category')
    retryable: bool = Field(False, description='Whether retrying may succeed')
    errno: Optional[int] = Field(None, description='Original OS errno when available')
    errno_name: Optional[str] = Field(
        None, description='Original OS errno symbolic name when available'
    )
    exception_type: Optional[str] = Field(
        None, description='Original Python exception type when available'
    )


class FileReplaceResult(BaseModel):
    """File content replacement result"""

    file: str = Field(..., description='Path of the operated file')
    replaced_count: int = Field(0, description='Number of replacements')


class FileSearchResult(BaseModel):
    """File content search result"""

    file: str = Field(..., description='Path of the searched file')
    matches: List[str] = Field([], description='List of matched content')
    line_numbers: List[int] = Field([], description='List of matched line numbers')


class FileFindResult(BaseModel):
    """File find result"""

    path: str = Field(..., description='Path of the search directory')
    files: List[str] = Field([], description='List of found files')


class GrepMatch(BaseModel):
    """Single grep match"""

    file: str = Field(..., description='File path containing the match')
    line_number: int = Field(..., description='Line number (1-based)')
    line_content: str = Field(..., description='Content of the matched line')
    context_before: Optional[List[str]] = Field(
        None, description='Lines before the match'
    )
    context_after: Optional[List[str]] = Field(
        None, description='Lines after the match'
    )


class FileGrepResult(BaseModel):
    """Multi-file grep result"""

    path: str = Field(..., description='Search directory path')
    pattern: str = Field(..., description='Search pattern used')
    matches: List[GrepMatch] = Field([], description='List of matches')
    match_count: int = Field(0, description='Total number of matches')
    files_searched: Optional[int] = Field(
        None, description='Number of files searched'
    )
    files_matched: Optional[int] = Field(
        None, description='Number of files with matches'
    )
    truncated: bool = Field(False, description='Whether results were truncated')


class GlobFileInfo(BaseModel):
    """Glob matched file info"""

    path: str = Field(..., description='Full file path')
    name: str = Field(..., description='File name')
    is_directory: bool = Field(False, description="Whether it's a directory")
    size: Optional[int] = Field(None, description='File size in bytes')
    modified_time: Optional[str] = Field(
        None, description='Last modified time (ISO format)'
    )


class FileGlobResult(BaseModel):
    """Enhanced glob result"""

    path: str = Field(..., description='Base directory path')
    pattern: str = Field(..., description='Glob pattern used')
    files: List[GlobFileInfo] = Field([], description='List of matched files')
    total_count: int = Field(0, description='Total number of matches')
    truncated: bool = Field(False, description='Whether results were truncated')


class FileUploadResult(BaseModel):
    """File upload result"""

    file_path: str = Field(..., description='Path of the uploaded file')
    file_size: int = Field(..., description='Size of the uploaded file in bytes')
    success: bool = Field(..., description='Whether upload was successful')


class FileInfo(BaseModel):
    """File information"""

    name: str = Field(..., description='File name')
    path: str = Field(..., description='Full file path')
    is_directory: bool = Field(..., description="Whether it's a directory")
    size: Optional[int] = Field(None, description='File size in bytes')
    modified_time: Optional[str] = Field(
        None, description='Last modified time (ISO format)'
    )
    permissions: Optional[str] = Field(None, description='File permissions')
    extension: Optional[str] = Field(None, description='File extension')


class FileListResult(BaseModel):
    """File list result"""

    path: str = Field(..., description='Listed directory path')
    files: List[FileInfo] = Field([], description='List of files and directories')
    total_count: int = Field(0, description='Total number of items')
    directory_count: int = Field(0, description='Number of directories')
    file_count: int = Field(0, description='Number of files')


class FileMetadata(BaseModel):
    """File metadata for binary files.

    Only fields relevant to the file type will be returned (None values excluded).
    """

    file_type: str = Field(..., description='File type (pdf, xlsx, xls, pptx, docx, etc.)')
    file_path: str = Field(..., description='Absolute path to the file')
    file_size: int = Field(..., description='File size in bytes')
    # PDF specific
    total_pages: Optional[int] = Field(None, description='Total number of pages (PDF)')
    current_page_range: Optional[List[int]] = Field(
        None, description='Current page range being returned [start, end]'
    )
    # Excel specific
    sheets: Optional[List[str]] = Field(None, description='List of sheet names (Excel)')
    current_sheet: Optional[str] = Field(
        None, description='Current sheet being returned (Excel)'
    )
    total_rows: Optional[int] = Field(
        None, description='Total number of rows in current sheet (Excel)'
    )
    current_row_range: Optional[List[int]] = Field(
        None, description='Current row range being returned [start, end] (Excel)'
    )
    # PPTX specific
    total_slides: Optional[int] = Field(
        None, description='Total number of slides (PPTX)'
    )
    current_slide_range: Optional[List[int]] = Field(
        None, description='Current slide range being returned [start, end]'
    )
    # DOCX specific
    total_paragraphs: Optional[int] = Field(
        None, description='Total number of paragraphs (DOCX)'
    )

    def model_dump(self, **kwargs):
        """Override to exclude None values by default."""
        kwargs.setdefault('exclude_none', True)
        return super().model_dump(**kwargs)


class FileSyncResult(BaseModel):
    """File sync (rsync) result"""

    source: str = Field(..., description='Source path')
    target: str = Field(..., description='Target path')
    files_transferred: int = Field(0, description='Number of files transferred')
    bytes_transferred: int = Field(0, description='Total bytes transferred')
    speed: Optional[str] = Field(
        None, description='Transfer speed (e.g., "12.34 MB/s")'
    )
    output: str = Field('', description='Full rsync output')
    dry_run: bool = Field(False, description='Whether this was a dry run')


class FileSyncWatchResult(BaseModel):
    """File sync watcher result"""

    id: str = Field(..., description='Watcher ID (for stop/status)')
    source: str
    target: str
    delay: int
    status: str = Field(..., description='"running" | "stopped" | "failed"')
    pid: Optional[int] = Field(None, description='lsyncd process PID')
    origin: str = Field(
        'api', description='"env" for env-configured, "api" for API-created'
    )


class FileSyncWatchListResult(BaseModel):
    """File sync watcher list result"""

    watchers: list[FileSyncWatchResult] = Field(default_factory=list)
    total: int = Field(0)


class FileSyncWatchStopResult(BaseModel):
    """File sync watcher stop result"""

    stopped: list[str] = Field(
        default_factory=list, description='IDs of stopped watchers'
    )


@dataclass
class LsyncdWatcher:
    """Internal lsyncd watcher state"""

    id: str
    source: str
    target: str
    delay: int
    exclude: list[str]
    process: asyncio.subprocess.Process
    config_path: str
    origin: str = 'api'
    created_at: float = field(default_factory=time.time)


class StrReplaceEditorResult(BaseModel):
    """String replace editor result based on openhands_aci CLIResult"""

    output: str = Field(..., description='Command execution output')
    error: Optional[str] = Field(None, description='Error message if any')
    path: str = Field(..., description='File path that was operated on')
    prev_exist: bool = Field(
        ..., description='Whether the file existed before operation'
    )
    old_content: Optional[str] = Field(None, description='Previous file content')
    new_content: Optional[str] = Field(
        None, description='New file content after operation'
    )
    metadata: Optional[FileMetadata] = Field(
        None, description='File metadata (only returned when enable_metadata=true for binary files)'
    )

    @field_serializer('metadata')
    def serialize_metadata(self, metadata: Optional[FileMetadata]) -> Optional[Dict[str, Any]]:
        """Serialize metadata with None values excluded."""
        if metadata is None:
            return None
        return metadata.model_dump(exclude_none=True)
