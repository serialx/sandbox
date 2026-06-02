"""
Code execution MCP tools
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
import re

from app.api.v1.code import _LANGUAGE_EXECUTORS
from app.schemas.code import CodeExecuteRequest, Language

from .app import mcp


logger = logging.getLogger(__name__)


@mcp.tool(output_schema=None, tags={'official'})
async def sandbox_execute_code(
    code: str,
    language: Language = Language.PYTHON,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute code in Python or JavaScript runtime.

    Args:
        code: Code to execute
        language: Programming language ('python', 'javascript')
        timeout: Optional execution timeout in seconds

    Returns:
        Dict containing output, errors, and execution details
    """

    handler = _LANGUAGE_EXECUTORS.get(language)
    supported = ', '.join(sorted(_LANGUAGE_EXECUTORS.keys()))
    if handler is None:
        raise ValueError(
            f"Unsupported language '{language}'. "
            f'Supported languages: {supported}. The unified runtime is designed to grow with additional interpreters.'
        )

    code_exec_req = CodeExecuteRequest(code=code, language=language, timeout=timeout)
    response_data, success, message = await handler(code_exec_req)

    # Collect Python traceback lines (list of strings)
    stack_raw = []
    for item in response_data.outputs or []:
        if isinstance(item, dict) and item.get('output_type') == 'error':
            tb = item.get('traceback')
            if isinstance(tb, list) and tb:
                stack_raw.extend(str(x) for x in tb if x)

    traceback_lines = None
    if stack_raw:
        # Strip ANSI escape sequences (e.g., \x1B[31m)
        ansi_re = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")
        stack_clean = [ansi_re.sub('', s) for s in stack_raw]
        traceback_lines = stack_clean if stack_clean else None

    return {
        'status': response_data.status,
        'stdout': response_data.stdout,
        'stderr': response_data.stderr,
        'exit_code': response_data.exit_code,
        'traceback': traceback_lines,
    }
