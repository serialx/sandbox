"""
File operation API interfaces
"""

import json
import logging
from urllib.parse import quote
from typing import TYPE_CHECKING

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.core.exceptions import FileToolException
from app.core.service_container import services
from app.logging.sanitizer import sanitize_for_logging
from app.models.file import (
    FileDownloadChangePolicy,
    FileFindResult,
    FileGlobResult,
    FileGrepResult,
    FileListResult,
    FileOperationError,
    FileReadResult,
    FileReplaceResult,
    FileSearchResult,
    FileUploadResult,
    FileWriteResult,
    StrReplaceEditorResult,
)
from app.schemas.file import (
    FileFindRequest,
    FileGlobRequest,
    FileGrepRequest,
    FileListRequest,
    FileReadRequest,
    FileReplaceRequest,
    FileSearchRequest,
    FileWriteRequest,
    StrReplaceEditorRequest,
)
from app.schemas.response import Response


if TYPE_CHECKING:
    from app.services.file import FileService


router = APIRouter()

logger = logging.getLogger(__name__)
DOWNLOAD_STREAM_CHUNK_SIZE = 64 * 1024


class FileDownloadSourceChangedError(RuntimeError):
    """Raised when the source file changes during an abort-on-change download."""


def _file_tool_error_response(error: FileOperationError) -> Response[FileOperationError]:
    return Response(success=False, message=error.message, data=error)


def _build_content_disposition(filename: str) -> str:
    quoted = quote(filename)
    if quoted != filename:
        return f"attachment; filename*=utf-8''{quoted}"
    return f'attachment; filename="{filename}"'


def _log_download_source_changed(
    *,
    path: str,
    phase: str,
    bytes_sent: int,
    expected_size: int,
) -> None:
    payload = sanitize_for_logging(
        {
            'event': 'file_download_source_changed',
            'path': path,
            'phase': phase,
            'bytes_sent': bytes_sent,
            'expected_size': expected_size,
        },
        field_name='download',
    )
    logger.warning(
        'FILE_DOWNLOAD_SOURCE_CHANGED %s',
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


@router.post(
    '/read',
    response_model=Response[FileReadResult | FileOperationError],
    operation_id='read_file',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'read_file',
    },
)
async def read_file(request: FileReadRequest):
    """
    Read file content
    """
    file_service: 'FileService' = services.require('file_service')
    try:
        result = await file_service.read_file(
            file=request.file,
            start_line=request.start_line,
            end_line=request.end_line,
            sudo=request.sudo,
        )
    except FileToolException as exc:
        return _file_tool_error_response(exc.error)

    # Construct response
    return Response(
        success=True, message='File read successfully', data=result
    )


@router.post(
    '/write',
    response_model=Response[FileWriteResult | FileOperationError],
    operation_id='write_file',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'write_file',
    },
)
async def write_file(
    request: FileWriteRequest,
):
    """
    Write file content (supports both text and binary files)

    For binary files, set encoding to 'base64' and provide base64-encoded content.
    For text files, use default 'utf-8' encoding.
    """
    file_service: 'FileService' = services.require('file_service')

    try:
        result = await file_service.write_file(
            file=request.file,
            content=request.content,
            encoding=request.encoding,
            append=request.append,
            leading_newline=request.leading_newline,
            trailing_newline=request.trailing_newline,
            sudo=request.sudo,
        )
    except FileToolException as exc:
        return _file_tool_error_response(exc.error)

    # Construct response
    return Response(
        success=True, message='File written successfully', data=result
    )


@router.post(
    '/replace',
    response_model=Response[FileReplaceResult | FileOperationError],
    operation_id='replace_in_file',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'replace_in_file',
    },
)
async def replace_in_file(request: FileReplaceRequest):
    """
    Replace string in file
    """
    file_service: 'FileService' = services.require('file_service')
    try:
        result = await file_service.str_replace(
            file=request.file,
            old_str=request.old_str,
            new_str=request.new_str,
            sudo=request.sudo,
        )
    except FileToolException as exc:
        return _file_tool_error_response(exc.error)

    # Construct response
    return Response(
        success=True,
        message=f'Replacement completed, replaced {result.replaced_count} occurrences',
        data=result,
    )


@router.post(
    '/search',
    response_model=Response[FileSearchResult | FileOperationError],
    operation_id='search_in_file',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'search_in_file',
    },
)
async def search_in_file(request: FileSearchRequest):
    """
    Search in file content
    """
    file_service: 'FileService' = services.require('file_service')
    try:
        result = await file_service.find_in_content(
            file=request.file, regex=request.regex, sudo=request.sudo
        )
    except FileToolException as exc:
        return _file_tool_error_response(exc.error)

    # Construct response
    return Response(
        success=True,
        message=f'Search completed, found {len(result.matches)} matches',
        data=result,
    )


@router.post(
    '/find',
    response_model=Response[FileFindResult | FileOperationError],
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'find_files',
    },
)
async def find_files(request: FileFindRequest):
    """
    Find files by name pattern
    """
    file_service: 'FileService' = services.require('file_service')
    try:
        result = await file_service.find_by_name(
            path=request.path, glob_pattern=request.glob
        )
    except FileToolException as exc:
        return _file_tool_error_response(exc.error)

    # Construct response
    return Response(
        success=True,
        message=f'Search completed, found {len(result.files)} files',
        data=result,
    )


@router.post(
    '/grep',
    response_model=Response[FileGrepResult | FileOperationError],
    operation_id='grep_files',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'grep_files',
    },
)
async def grep_files(request: FileGrepRequest):
    """
    Multi-file content search (grep) with regex or fixed string support
    """
    file_service: 'FileService' = services.require('file_service')
    try:
        result = await file_service.grep(
            path=request.path,
            pattern=request.pattern,
            include=request.include,
            exclude=request.exclude,
            case_insensitive=request.case_insensitive,
            fixed_strings=request.fixed_strings,
            context_before=request.context_before,
            context_after=request.context_after,
            max_results=request.max_results,
            max_file_size=request.max_file_size,
            multiline=request.multiline,
            offset=request.offset,
            file_type=request.type,
            recursive=request.recursive,
        )
    except FileToolException as exc:
        return _file_tool_error_response(exc.error)

    return Response(
        success=True,
        message=f'Grep completed, found {result.match_count} matches in {result.files_matched} files',
        data=result,
    )


@router.post(
    '/glob',
    response_model=Response[FileGlobResult | FileOperationError],
    operation_id='glob_files',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'glob_files',
    },
)
async def glob_files(request: FileGlobRequest):
    """
    Enhanced file glob matching with optional metadata
    """
    file_service: 'FileService' = services.require('file_service')
    try:
        result = await file_service.glob_files(
            path=request.path,
            pattern=request.pattern,
            exclude=request.exclude,
            include_hidden=request.include_hidden,
            files_only=request.files_only,
            include_metadata=request.include_metadata,
            max_results=request.max_results,
            sort_by=request.sort_by,
            sort_desc=request.sort_desc,
        )
    except FileToolException as exc:
        return _file_tool_error_response(exc.error)

    return Response(
        success=True,
        message=f'Glob completed, found {result.total_count} matches',
        data=result,
    )


@router.post(
    '/upload',
    response_model=Response[FileUploadResult | FileOperationError],
    operation_id='upload_file',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'upload_file',
    },
)
async def upload_file(file: UploadFile = File(...), path: str = Form(None)):
    """
    Upload file using streaming
    """
    file_service: 'FileService' = services.require('file_service')
    if not path:
        path = f'/tmp/{file.filename}'

    try:
        result = await file_service.upload_file(path=path, file_stream=file)
    except FileToolException as exc:
        return _file_tool_error_response(exc.error)

    return Response(
        success=True, message='File uploaded successfully', data=result
    )


@router.get(
    '/download',
    response_class=StreamingResponse,
    responses={
        200: {
            'content': {
                'application/octet-stream': {
                    'schema': {'type': 'string', 'format': 'binary'}
                }
            }
        },
        409: {
            'description': 'File changed before or during download when change_policy=abort',
        }
    },
    operation_id='download_file',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'download_file',
    },
)
async def download_file(
    path: str,
    change_policy: FileDownloadChangePolicy = Query(
        FileDownloadChangePolicy.IGNORE
    ),
):
    """
    Download a file.

    When ``change_policy=abort``, the server aborts the download if the source
    file changes before streaming starts or while bytes are being sent.
    """
    file_service: 'FileService' = services.require('file_service')
    filename = path.split('/')[-1]

    if change_policy == FileDownloadChangePolicy.IGNORE:
        file_service.ensure_file(path)
        return FileResponse(
            path=path, filename=filename, media_type='application/octet-stream'
        )

    file_obj, source_state = await file_service.open_file_for_download(path)

    try:
        if await file_service.download_source_changed(path, source_state):
            _log_download_source_changed(
                path=path,
                phase='before_start',
                bytes_sent=0,
                expected_size=source_state.size,
            )
            await file_service.close_download_file(file_obj)
            return JSONResponse(
                status_code=409,
                content=Response.error(
                    message='File changed before download started',
                    data={
                        'path': path,
                        'change_policy': change_policy.value,
                        'phase': 'before_start',
                    },
                ).model_dump(),
            )

        first_chunk = await file_service.read_download_chunk(
            file_obj, min(DOWNLOAD_STREAM_CHUNK_SIZE, source_state.size)
        )
        first_chunk_len = len(first_chunk)

        if source_state.size > 0 and first_chunk_len == 0:
            _log_download_source_changed(
                path=path,
                phase='before_start',
                bytes_sent=0,
                expected_size=source_state.size,
            )
            await file_service.close_download_file(file_obj)
            return JSONResponse(
                status_code=409,
                content=Response.error(
                    message='File changed before download started',
                    data={
                        'path': path,
                        'change_policy': change_policy.value,
                        'phase': 'before_start',
                    },
                ).model_dump(),
            )

        if await file_service.download_source_changed(path, source_state):
            _log_download_source_changed(
                path=path,
                phase='before_start',
                bytes_sent=first_chunk_len,
                expected_size=source_state.size,
            )
            await file_service.close_download_file(file_obj)
            return JSONResponse(
                status_code=409,
                content=Response.error(
                    message='File changed before download started',
                    data={
                        'path': path,
                        'change_policy': change_policy.value,
                        'phase': 'before_start',
                    },
                ).model_dump(),
            )
    except Exception:
        await file_service.close_download_file(file_obj)
        raise

    async def generate_download():
        bytes_sent = first_chunk_len
        try:
            if first_chunk:
                yield first_chunk

            while bytes_sent < source_state.size:
                if await file_service.download_source_changed(path, source_state):
                    _log_download_source_changed(
                        path=path,
                        phase='streaming',
                        bytes_sent=bytes_sent,
                        expected_size=source_state.size,
                    )
                    raise FileDownloadSourceChangedError(
                        f'File changed during download: {path}'
                    )

                chunk = await file_service.read_download_chunk(
                    file_obj,
                    min(DOWNLOAD_STREAM_CHUNK_SIZE, source_state.size - bytes_sent),
                )
                if not chunk:
                    _log_download_source_changed(
                        path=path,
                        phase='streaming',
                        bytes_sent=bytes_sent,
                        expected_size=source_state.size,
                    )
                    raise FileDownloadSourceChangedError(
                        f'File became unreadable during download: {path}'
                    )

                if await file_service.download_source_changed(path, source_state):
                    _log_download_source_changed(
                        path=path,
                        phase='streaming',
                        bytes_sent=bytes_sent,
                        expected_size=source_state.size,
                    )
                    raise FileDownloadSourceChangedError(
                        f'File changed during download: {path}'
                    )

                bytes_sent += len(chunk)
                yield chunk
        finally:
            await file_service.close_download_file(file_obj)

    return StreamingResponse(
        generate_download(),
        media_type='application/octet-stream',
        headers={
            'content-disposition': _build_content_disposition(filename),
            'content-length': str(source_state.size),
            'accept-ranges': 'none',
        },
    )


@router.post(
    '/list',
    response_model=Response[FileListResult | FileOperationError],
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'list_path',
    },
)
async def list_path(request: FileListRequest):
    """
    List path contents with flexible options
    """
    file_service: 'FileService' = services.require('file_service')

    logger.info(
        'ListPath request: %s',
        sanitize_for_logging(request.model_dump(exclude_none=True), field_name='request'),
    )

    try:
        result = await file_service.list_path(
            path=request.path,
            recursive=request.recursive,
            show_hidden=request.show_hidden,
            file_types=request.file_types,
            max_depth=request.max_depth,
            include_size=request.include_size,
            include_permissions=request.include_permissions,
            sort_by=request.sort_by,
            sort_desc=request.sort_desc,
        )
    except FileToolException as exc:
        return _file_tool_error_response(exc.error)

    # Construct response
    return Response(
        success=True,
        message=f'Path listed successfully, found {result.total_count} items',
        data=result,
    )


# TODO: sync APIs temporarily disabled, pending redesign with supervisor-based lsyncd management
# @router.post('/sync', ...)
# @router.post('/sync/watch', ...)
# @router.get('/sync/watch', ...)
# @router.delete('/sync/watch', ...)


@router.post(
    '/str_replace_editor',
    response_model=Response[StrReplaceEditorResult | FileOperationError],
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'str_replace_editor',
    },
)
async def str_replace_editor(request: StrReplaceEditorRequest):
    """
    An filesystem editor tool that allows the agent to
    - view
    - create
    - navigate
    - edit files
    The tool parameters are defined by Anthropic and are not editable.
    """
    file_service: 'FileService' = services.require('file_service')

    logger.info(
        'StrReplaceEditor request: %s',
        sanitize_for_logging(request.model_dump(exclude_none=True), field_name='request'),
    )

    try:
        result = await file_service.str_replace_editor(
            command=request.command,
            path=request.path,
            file_text=request.file_text,
            old_str=request.old_str,
            new_str=request.new_str,
            insert_line=request.insert_line,
            view_range=request.view_range,
            replace_mode=request.replace_mode,
            # Binary file pagination parameters
            page_range=request.page_range,
            sheet_name=request.sheet_name,
            row_range=request.row_range,
            slide_range=request.slide_range,
            enable_metadata=request.enable_metadata,
        )
    except FileToolException as exc:
        return _file_tool_error_response(exc.error)

    return Response(
        success=True,
        message=f"StrReplaceEditor successfully executed command '{request.command}'",
        data=result,
    )
