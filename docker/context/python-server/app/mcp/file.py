"""
File MCP tools for file system operations and editing
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.core.exceptions import FileToolException
from app.core.service_container import services
from app.schemas.file import FileContentEncoding

from .app import mcp


if TYPE_CHECKING:
    from app.services.file import FileService

logger = logging.getLogger(__name__)


def _file_tool_error_result(action: str, path: str, exc: FileToolException) -> Dict[str, Any]:
    return {
        'action': action,
        'path': path,
        'success': False,
        'message': exc.error.message,
        'error': exc.error.model_dump(),
    }


@mcp.tool(output_schema=None, tags={'official'})
async def sandbox_file_operations(
    action: str,
    path: str,
    content: Optional[str] = None,
    target: Optional[str] = None,
    pattern: Optional[str] = None,
    encoding: Optional[str] = 'utf-8',
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    append: bool = False,
    recursive: bool = False,
    show_hidden: bool = False,
    file_types: Optional[List[str]] = None,
    sudo: bool = False,
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
    case_insensitive: bool = False,
    fixed_strings: bool = False,
    context_before: int = 0,
    context_after: int = 0,
    max_results: Optional[int] = None,
    include_metadata: bool = True,
    files_only: bool = True,
) -> Dict[str, Any]:
    """Unified file system operations tool for agents. `/tmp` and `/home/$USER` are fully accessible.

    Args:
        action: Operation type - "read", "write", "replace", "search", "find", "list", "grep", "glob"
        path: File or directory path
        content: Content for write/replace operations (or regex for search)
        target: Target string for replace operations (new_str)
        pattern: Pattern for find/grep/glob operations (glob syntax or regex)
        encoding: File encoding (utf-8, base64, raw)
        start_line: Starting line for read operations (0-based)
        end_line: Ending line for read operations (not included)
        append: Append mode for write operations
        recursive: Recursive mode for find/list/grep operations
        show_hidden: Show hidden files in list operations
        file_types: Filter by file extensions for list operations
        sudo: Use sudo privileges
        include: File glob filters for grep (e.g., ["*.py"])
        exclude: Glob patterns to exclude for grep/glob
        case_insensitive: Case insensitive search for grep
        fixed_strings: Treat pattern as literal string for grep
        context_before: Lines before each match for grep
        context_after: Lines after each match for grep
        max_results: Maximum number of results for grep/glob
        include_metadata: Include file metadata for glob
        files_only: Only return files for glob

    Returns:
        Dict containing operation result and relevant data
    """
    file_service: 'FileService' = services.get('file_service')

    try:
        if action == 'read':
            result = await file_service.read_file(
                file=path, start_line=start_line, end_line=end_line, sudo=sudo
            )
            return {
                'action': 'read',
                'path': path,
                'content': result.content,
                'success': True,
            }

        elif action == 'write':
            if content is None:
                raise ValueError('Content is required for write operation')

            result = await file_service.write_file(
                file=path,
                content=content,
                encoding=FileContentEncoding(encoding)
                if encoding in ['utf-8', 'base64', 'raw']
                else FileContentEncoding.UTF8,
                append=append,
                sudo=sudo,
            )
            return {
                'action': 'write',
                'path': path,
                'bytes_written': result.bytes_written,
                'success': True,
            }

        elif action == 'replace':
            if content is None or target is None:
                raise ValueError(
                    'Both content (old_str) and target (new_str) are required for replace operation'
                )

            result = await file_service.str_replace(
                file=path, old_str=content, new_str=target, sudo=sudo
            )
            return {
                'action': 'replace',
                'path': path,
                'replaced_count': result.replaced_count,
                'success': True,
            }

        elif action == 'search':
            if content is None:
                raise ValueError(
                    'Content (regex pattern) is required for search operation'
                )

            result = await file_service.find_in_content(
                file=path, regex=content, sudo=sudo
            )
            return {
                'action': 'search',
                'path': path,
                'matches': result.matches,
                'line_numbers': result.line_numbers,
                'match_count': len(result.matches),
                'success': True,
            }

        elif action == 'find':
            if pattern is None:
                raise ValueError('Pattern is required for find operation')

            result = await file_service.find_by_name(path=path, glob_pattern=pattern)
            return {
                'action': 'find',
                'path': path,
                'files': result.files,
                'file_count': len(result.files),
                'success': True,
            }

        elif action == 'list':
            result = await file_service.list_path(
                path=path,
                recursive=recursive,
                show_hidden=show_hidden,
                file_types=file_types,
                include_size=True,
                include_permissions=False,
                sort_by='name',
                sort_desc=False,
            )
            return {
                'action': 'list',
                'path': path,
                'files': [f.model_dump() for f in result.files],
                'total_count': result.total_count,
                'directory_count': result.directory_count,
                'file_count': result.file_count,
                'success': True,
            }

        elif action == 'grep':
            if pattern is None:
                raise ValueError('Pattern is required for grep operation')

            result = await file_service.grep(
                path=path,
                pattern=pattern,
                include=include,
                exclude=exclude,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
                context_before=context_before,
                context_after=context_after,
                max_results=max_results or 500,
                recursive=recursive,
            )
            return {
                'action': 'grep',
                'path': path,
                'matches': [m.model_dump() for m in result.matches],
                'match_count': result.match_count,
                'files_searched': result.files_searched,
                'files_matched': result.files_matched,
                'truncated': result.truncated,
                'success': True,
            }

        elif action == 'glob':
            if pattern is None:
                raise ValueError('Pattern is required for glob operation')

            result = await file_service.glob_files(
                path=path,
                pattern=pattern,
                exclude=exclude,
                include_hidden=show_hidden,
                files_only=files_only,
                include_metadata=include_metadata,
                max_results=max_results or 5000,
            )
            return {
                'action': 'glob',
                'path': path,
                'files': [f.model_dump() for f in result.files],
                'total_count': result.total_count,
                'truncated': result.truncated,
                'success': True,
            }

        else:
            raise ValueError(f'Unsupported action: {action}')

    except FileToolException as exc:
        logger.warning('File system operation failed: %s', exc.error.message)
        return _file_tool_error_result(action, path, exc)
    except Exception as e:
        logger.error(f'File system operation failed: {str(e)}')
        return {'action': action, 'path': path, 'success': False, 'error': str(e)}


@mcp.tool(output_schema=None, tags={'official'})
async def sandbox_str_replace_editor(
    command: str,
    path: str,
    file_text: Optional[str] = None,
    old_str: Optional[str] = None,
    new_str: Optional[str] = None,
    insert_line: Optional[int] = None,
    view_range: Optional[List[int]] = None,
    replace_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Professional file editor tool using openhands_aci editor.

    This tool provides advanced file editing capabilities compatible with Anthropic's
    str_replace_editor interface. Parameters and behavior match the standard interface.

    Args:
        command: Command to execute ("view", "create", "str_replace", "insert", "undo_edit")
        path: File path to operate on
        file_text: File content for create command
        old_str: Original string to replace (for str_replace command)
        new_str: New string to replace with (for str_replace and insert commands)
        insert_line: Line number to insert at (for insert command)
        view_range: Line range for view command [start, end]
        replace_mode: Replacement mode for str_replace ("ALL", "FIRST", "LAST"). If not specified, requires unique match.

    Returns:
        Dict containing editor operation result
    """
    file_service: 'FileService' = services.get('file_service')

    try:
        result = await file_service.str_replace_editor(
            command=command,
            path=path,
            file_text=file_text,
            old_str=old_str,
            new_str=new_str,
            insert_line=insert_line,
            view_range=view_range or [],
            replace_mode=replace_mode,
        )

        # Determine success based on whether there's an error
        success = result.error is None

        return {
            'command': command,
            'path': result.path,
            'output': result.output,
            'error': result.error,
            'prev_exist': result.prev_exist,
            'old_content': result.old_content,
            'new_content': result.new_content,
            'success': success,
        }

    except FileToolException as exc:
        logger.warning('str_replace_editor operation failed: %s', exc.error.message)
        return {
            'command': command,
            'path': path,
            'success': False,
            'message': exc.error.message,
            'error': exc.error.model_dump(),
        }
    except Exception as e:
        logger.error(f'str_replace_editor operation failed: {str(e)}')
        return {'command': command, 'path': path, 'success': False, 'error': str(e)}
