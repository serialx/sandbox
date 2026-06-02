import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from app.core.version import resolve_python_version
from app.core.service_container import services
from app.models.jupyter import ActiveSessionsResult
from app.schemas.jupyter import (
    JupyterCreateSessionRequest,
    JupyterCreateSessionResponse,
    JupyterExecuteRequest,
    JupyterExecuteResponse,
    JupyterInfoResponse,
)
from app.schemas.response import Response
from app.utils import get_workspace, validate_cwd


if TYPE_CHECKING:
    from app.services.jupyter import JupyterService


logger = logging.getLogger(__name__)

router = APIRouter()


def _default_kernel_name() -> str:
    """Resolve the default Jupyter kernel from unified runtime config."""
    return resolve_python_version()


@router.post(
    '/execute',
    response_model=Response[JupyterExecuteResponse],
    operation_id='jupyter_execute_code',
    openapi_extra={
        'x-fern-sdk-method-name': 'execute_code',
        'x-fern-sdk-group-name': 'jupyter',
    },
)
async def execute_jupyter_code(request: JupyterExecuteRequest):
    """
    Execute Python code using Jupyter kernel with session persistence

    This endpoint allows you to execute Python code and get results back.
    You can optionally specify a kernel_name.
    If omitted, the runtime default resolved from `PYTHON_VERSION` is used.
    Use session_id to maintain variable state across multiple requests.
    Sessions automatically expire after 30 minutes of inactivity.
    """
    try:
        jupyter_service: 'JupyterService' = services.require('jupyter_service')
        kernel_name = request.kernel_name or _default_kernel_name()
        logger.info(f'Executing Jupyter code with kernel_name: {kernel_name}')

        # Execute code using the service with session support
        kernel_kwargs = {}
        kernel_kwargs['cwd'] = validate_cwd(request.cwd, default=get_workspace())

        result = await jupyter_service.execute_code(
            code=request.code,
            timeout=request.timeout or 30,
            kernel_name=kernel_name,
            session_id=request.session_id,
            **kernel_kwargs,
        )

        logger.info(
            f'Execution result: {result.status}, kernel_name: {result.kernel_name}'
        )

        # Convert ExecuteCodeResult to JupyterExecuteResponse for API layer
        response_data = JupyterExecuteResponse(
            kernel_name=result.kernel_name,
            session_id=result.session_id,
            status=result.status.value,
            execution_count=result.execution_count,
            outputs=[output.model_dump() for output in result.outputs],
            code=result.code,
            msg_id=result.msg_id,
        )

        # Determine success based on status
        success = result.status.value in ['ok', 'idle']
        message = (
            'Code executed successfully'
            if success
            else f'Code execution {result.status.value}'
        )

        return Response(
            success=success, message=message, data=response_data.model_dump()
        )

    except ValueError as e:
        return Response(
            success=False,
            message=str(e),
            data=JupyterExecuteResponse(
                kernel_name=request.kernel_name or _default_kernel_name(),
                session_id=None, status='error', execution_count=None,
                outputs=[{'output_type': 'error', 'ename': 'DirectoryError', 'evalue': str(e), 'traceback': []}],
                code=request.code, msg_id=None,
            ).model_dump(),
        )

    except Exception as e:
        logger.error(f'Jupyter execution error: {e}', exc_info=True)

        # Return a proper error response
        error_response = JupyterExecuteResponse(
            kernel_name=request.kernel_name or _default_kernel_name(),
            session_id=None,
            status='error',
            execution_count=None,
            outputs=[
                {
                    'output_type': 'error',
                    'ename': 'ExecutionError',
                    'evalue': str(e),
                    'traceback': [str(e)],
                }
            ],
            code=request.code,
            msg_id=None,
        )

        return Response(
            success=False,
            message=f'Code execution failed: {str(e)}',
            data=error_response.model_dump(),
        )


@router.get(
    '/info',
    response_model=Response[JupyterInfoResponse],
    operation_id='get_jupyter_info',
    openapi_extra={
        'x-fern-sdk-group-name': 'jupyter',
        'x-fern-sdk-method-name': 'get_info',
    },
)
async def jupyter_info():
    """
    Get information about available Jupyter kernels
    """
    jupyter_service: 'JupyterService' = services.require('jupyter_service')
    available_kernels = jupyter_service.get_available_kernels()
    active_sessions = jupyter_service.get_active_sessions()

    info = JupyterInfoResponse(
        default_kernel=_default_kernel_name(),
        available_kernels=available_kernels,
        active_sessions=len(active_sessions.sessions),
        session_timeout_seconds=jupyter_service.session_timeout,
        max_sessions=jupyter_service.max_sessions,
        description='Session-based kernel management with automatic cleanup',
        kernel_detection='Automatic fallback to available kernels if requested kernel not found',
    )

    return Response(
        success=True,
        message=f'Jupyter service info - {len(available_kernels)} kernels, {len(active_sessions.sessions)} active sessions',
        data=info.model_dump(),
    )


@router.get(
    '/sessions',
    response_model=Response[ActiveSessionsResult],
    operation_id='list_jupyter_sessions',
    openapi_extra={
        'x-fern-sdk-group-name': 'jupyter',
        'x-fern-sdk-method-name': 'list_sessions',
    },
)
async def list_sessions():
    """
    List all active Jupyter sessions
    """
    jupyter_service: 'JupyterService' = services.require('jupyter_service')
    sessions = jupyter_service.get_active_sessions()

    return Response(
        success=True,
        message=f'Found {len(sessions.sessions)} active sessions',
        data=sessions.model_dump(),
    )


@router.delete(
    '/sessions/{session_id}',
    response_model=Response,
    operation_id='delete_jupyter_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'jupyter',
        'x-fern-sdk-method-name': 'delete_session',
    },
)
async def cleanup_session(session_id: str):
    """
    Manually cleanup a specific session
    """
    jupyter_service: 'JupyterService' = services.require('jupyter_service')
    success = jupyter_service.cleanup_session(session_id)

    if success:
        return Response(
            success=True,
            message=f'Session {session_id} cleaned up successfully',
            data={'session_id': session_id},
        )
    else:
        return Response(
            success=False,
            message=f'Session {session_id} not found',
            data={'session_id': session_id},
        )


@router.delete(
    '/sessions',
    response_model=Response,
    operation_id='delete_jupyter_sessions',
    openapi_extra={
        'x-fern-sdk-group-name': 'jupyter',
        'x-fern-sdk-method-name': 'delete_sessions',
    },
)
async def cleanup_all_sessions():
    """
    Cleanup all active sessions
    """
    jupyter_service: 'JupyterService' = services.require('jupyter_service')
    sessions_before = len(jupyter_service.get_active_sessions().sessions)
    jupyter_service.cleanup_all_sessions()

    return Response(
        success=True,
        message=f'Cleaned up {sessions_before} sessions',
        data={'cleaned_sessions': sessions_before},
    )


@router.post(
    '/sessions/create',
    response_model=Response[JupyterCreateSessionResponse],
    operation_id='create_jupyter_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'jupyter',
        'x-fern-sdk-method-name': 'create_session',
    },
)
async def create_jupyter_session(request: JupyterCreateSessionRequest):
    """
    Create a new Jupyter session
    """
    jupyter_service: 'JupyterService' = services.get('jupyter_service')
    if not jupyter_service:
        raise HTTPException(status_code=503, detail='Jupyter runtime unavailable')

    kernel_name = request.kernel_name or _default_kernel_name()

    logger.info(f'Creating Jupyter session with kernel: {kernel_name}')

    # Use async version which supports pool
    kernel_kwargs = {}
    kernel_kwargs['cwd'] = validate_cwd(request.cwd, default=get_workspace())

    session_result = await jupyter_service._get_or_create_session_async(
        request.session_id, kernel_name, **kernel_kwargs
    )

    response_data = JupyterCreateSessionResponse(
        session_id=session_result.session_id,
        kernel_name=session_result.kernel_name,
        message=f'Jupyter session created successfully with kernel {kernel_name}',
    )

    return Response(
        success=True,
        message=f'Jupyter session created via kernel {kernel_name}',
        data=response_data.model_dump(),
    )
