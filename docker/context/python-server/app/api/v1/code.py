import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, Tuple

from fastapi import APIRouter, HTTPException

from app.core.exceptions import BadRequestException
from app.core.service_container import services
from app.core.version import resolve_python_version
from app.models.jupyter import OutputType
from app.schemas.code import (
    CodeExecuteRequest,
    CodeExecuteResponse,
    CodeInfoResponse,
    CodeLanguageInfo,
)
from app.schemas.response import Response
from app.utils import get_workspace, validate_cwd


if TYPE_CHECKING:
    from app.services.jupyter import JupyterService
    from app.services.nodejs import NodeJSService


logger = logging.getLogger(__name__)

router = APIRouter()


ExecutionHandler = Callable[
    [CodeExecuteRequest], Awaitable[Tuple[CodeExecuteResponse, bool, str]]
]


async def _execute_python(
    request: CodeExecuteRequest,
) -> Tuple[CodeExecuteResponse, bool, str]:
    jupyter_service: 'JupyterService' = services.get('jupyter_service')
    if not jupyter_service:
        raise HTTPException(status_code=503, detail='Python runtime unavailable')

    logger.info(
        f'Executing Python code via unified endpoint (stateful={request.stateful})'
    )

    kernel_kwargs = {}
    kernel_kwargs['cwd'] = validate_cwd(request.cwd, default=get_workspace())

    # Use session_id only when stateful=True
    session_id = request.session_id if request.stateful else None

    result = await jupyter_service.execute_code(
        code=request.code,
        timeout=request.timeout or 30,
        kernel_name=resolve_python_version(),
        session_id=session_id,
        **kernel_kwargs,
    )

    stdout_chunks = []
    stderr_chunks = []
    outputs = []
    for output in result.outputs:
        outputs.append(output.model_dump())
        if output.output_type == OutputType.STREAM:
            if output.name == 'stdout' and output.text:
                stdout_chunks.append(output.text)
            elif output.name == 'stderr' and output.text:
                stderr_chunks.append(output.text)
        elif output.output_type == OutputType.ERROR:
            # Capture error information in stderr
            error_parts = []
            if output.ename:
                error_parts.append(f'{output.ename}')
            if output.evalue:
                error_parts.append(f': {output.evalue}')
            if error_parts:
                stderr_chunks.append(''.join(error_parts))

    response_data = CodeExecuteResponse(
        language='python',
        status=result.status.value,
        outputs=outputs,
        code=result.code,
        stdout=''.join(stdout_chunks) if stdout_chunks else None,
        stderr=''.join(stderr_chunks) if stderr_chunks else None,
        exit_code=None,
        session_id=result.session_id if request.stateful else None,
    )

    # Cleanup session immediately if not stateful
    if not request.stateful and result.session_id:
        jupyter_service.cleanup_session(result.session_id)

    success = result.status.value in {'ok', 'idle'}
    message = (
        'Python code executed successfully'
        + (' (stateful)' if request.stateful else '')
        if success
        else f'Python execution {result.status.value}'
    )

    return response_data, success, message


async def _execute_javascript_stateful(
    request: CodeExecuteRequest,
) -> Tuple[CodeExecuteResponse, bool, str]:
    """Execute JavaScript using Node.js REPL (stateful)"""
    nodejs_service: 'NodeJSService' = services.get('nodejs_service')
    if not nodejs_service:
        raise HTTPException(
            status_code=503, detail='JavaScript runtime unavailable'
        )

    logger.info('Executing JavaScript code via REPL (stateful)')

    result = await nodejs_service.execute_code_stateful(
        code=request.code,
        timeout=request.timeout or 30,
        session_id=request.session_id,
        cwd=validate_cwd(request.cwd, default=get_workspace()),
    )

    response_data = CodeExecuteResponse(
        language='javascript',
        status=result.status.value,
        outputs=[output.model_dump() for output in result.outputs],
        code=result.code,
        stdout=result.stdout or None,
        stderr=result.stderr or None,
        exit_code=result.exit_code,
        session_id=result.session_id,
    )

    success = result.status.value == 'ok'
    message = (
        'JavaScript code executed successfully (stateful via REPL)'
        if success
        else f'JavaScript execution {result.status.value}'
    )

    return response_data, success, message


async def _execute_javascript(
    request: CodeExecuteRequest,
) -> Tuple[CodeExecuteResponse, bool, str]:
    """Execute JavaScript - stateful via Node.js REPL, stateless via subprocess"""
    # Stateful execution: use Node.js REPL
    if request.stateful:
        return await _execute_javascript_stateful(request)

    # Stateless execution: use existing NodeJS subprocess approach
    nodejs_service: 'NodeJSService' = services.get('nodejs_service')
    if not nodejs_service:
        raise HTTPException(status_code=503, detail='JavaScript runtime unavailable')

    logger.info('Executing JavaScript code via subprocess (stateless)')

    result = await nodejs_service.execute_code(
        code=request.code,
        timeout=request.timeout or 30,
        stdin=None,
        files=None,
        cwd=validate_cwd(request.cwd, default=get_workspace()),
    )

    response_data = CodeExecuteResponse(
        language='javascript',
        status=result.status.value,
        outputs=[output.model_dump() for output in result.outputs],
        code=result.code,
        stdout=result.stdout or None,
        stderr=result.stderr or None,
        exit_code=result.exit_code,
        session_id=None,  # No session for stateless execution
    )

    success = result.status.value == 'ok'
    message = (
        'JavaScript code executed successfully'
        if success
        else f'JavaScript execution {result.status.value}'
    )

    return response_data, success, message


_LANGUAGE_EXECUTORS: Dict[str, ExecutionHandler] = {
    'python': _execute_python,
    'javascript': _execute_javascript,
}


@router.post(
    '/execute',
    response_model=Response[CodeExecuteResponse],
    openapi_extra={
        'x-fern-sdk-group-name': 'code',
        'x-fern-sdk-method-name': 'execute_code',
    },
)
async def execute_code(request: CodeExecuteRequest):
    """Run code through the unified runtime, dispatching to Python, Node.js, or future language executors"""

    handler = _LANGUAGE_EXECUTORS.get(request.language)
    if handler is None:
        supported = ', '.join(sorted(_LANGUAGE_EXECUTORS.keys()))
        raise BadRequestException(
            f"Unsupported language '{request.language}'. "
            f'Supported languages: {supported}. The unified runtime is designed to grow with additional interpreters.'
        )

    try:
        response_data, success, message = await handler(request)
    except ValueError as exc:
        raise BadRequestException(str(exc))

    return Response(
        success=success,
        message=message,
        data=response_data.model_dump(),
    )


@router.get(
    '/info',
    response_model=Response[CodeInfoResponse],
    openapi_extra={
        'x-fern-sdk-group-name': 'code',
        'x-fern-sdk-method-name': 'get_info',
    },
)
async def code_info():
    """Return metadata about supported code runtimes

    Note: Version info is cached at service level (first call only runs subprocess).
    """

    jupyter_service: 'JupyterService' = services.get('jupyter_service')
    nodejs_service: 'NodeJSService' = services.get('nodejs_service')

    python_details = {
        'available_kernels': (
            jupyter_service.get_available_kernels() if jupyter_service else []
        ),
    }

    # resolve_python_version() returns an explicit kernel like "python3.10".
    kernel_name = resolve_python_version()
    python_version = kernel_name.removeprefix('python')

    python_info = CodeLanguageInfo(
        language='python',
        description='Python execution via managed Jupyter kernel',
        runtime_version=python_version,
        default_timeout=30,
        max_timeout=300,
        details=python_details,
    )

    node_runtime = nodejs_service.get_runtime_info() if nodejs_service else None
    node_details = {}
    node_version = None
    if node_runtime:
        node_version = node_runtime.node_version
        node_details = {
            'npm_version': node_runtime.npm_version,
            'runtime_directory': node_runtime.runtime_directory,
            'supported_languages': node_runtime.supported_languages,
        }

    node_info = CodeLanguageInfo(
        language='javascript',
        description='Node.js JavaScript execution service',
        runtime_version=node_version,
        default_timeout=30,
        max_timeout=300,
        details=node_details,
    )

    response_data = CodeInfoResponse(languages=[python_info, node_info])

    return Response(
        success=True,
        message='Unified code service covering Python, Node.js, and extensible future runtimes',
        data=response_data.model_dump(),
    )
