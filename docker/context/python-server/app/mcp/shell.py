"""
Shell MCP tools for command execution
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from app.core.service_container import services
from app.utils import get_workspace, validate_cwd

from .app import mcp


if TYPE_CHECKING:
    from app.services.shell import OpenHandsShellManager


logger = logging.getLogger(__name__)

# Store the default session ID for automatic session management
_default_session_id: Optional[str] = None


async def _get_or_create_session(
    terminal_manager: 'OpenHandsShellManager',
    preserve_symlinks: bool = False,
) -> str:
    """Get existing default session or create a new one."""
    global _default_session_id

    # Check if default session exists and is active
    if _default_session_id:
        session = terminal_manager.get_session(_default_session_id)
        if session and session.active:
            if session.bash_session:
                session.bash_session.preserve_symlinks = preserve_symlinks
            return _default_session_id

    # Create new default session
    session = await terminal_manager.create_session(
        working_dir=get_workspace(),
        preserve_symlinks=preserve_symlinks,
    )
    _default_session_id = session.id
    return _default_session_id


@mcp.tool(output_schema=None, tags={'official'})
async def sandbox_execute_bash(
    cmd: str,
    cwd: Optional[str] = None,
    new_session: bool = False,
    timeout: Optional[int] = 30,
    preserve_symlinks: bool = False,
) -> Dict[str, Any]:
    """Execute a shell command. Sessions are managed automatically.

    Args:
        cmd: Shell command to execute
        cwd: Optional working directory (absolute path). When provided, the
            hidden shell session is synchronized to this directory before the
            command runs. The response includes the actual cwd after execution,
            which agents should pass back on subsequent calls to keep state in
            sync.
        new_session: If True, creates a new session instead of using the default
        timeout: Optional timeout in seconds for command execution (default: 30)
        preserve_symlinks: If True, preserve symlinks in working directory path
            (pwd shows symlink path). If False, symlinks are resolved to physical
            paths. Defaults to False for backward compatibility.

    Returns:
        Dict containing command status, output, exit code, and resulting cwd
    """
    terminal_manager: 'OpenHandsShellManager' = services.require('terminal_manager')

    requested_cwd = validate_cwd(cwd) if cwd else None

    # Get or create session
    if new_session:
        session = await terminal_manager.create_session(
            working_dir=requested_cwd or get_workspace(),
            preserve_symlinks=preserve_symlinks,
        )
        session_id = session.id
    else:
        session_id = await _get_or_create_session(terminal_manager, preserve_symlinks)

    # Execute command
    result = await terminal_manager.execute_command(
        session_id,
        cmd,
        async_mode=False,
        timeout=timeout,
        working_dir=requested_cwd,
    )
    session = terminal_manager.get_session(session_id)
    current_cwd = session.working_dir if session else (requested_cwd or get_workspace())

    return {
        'status': result.status.value,
        'output': result.output,
        'exit_code': result.exit_code,
        'cwd': current_cwd,
    }
