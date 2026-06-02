import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Query

from app.core.exceptions import ResourceNotFoundException
from app.core.service_container import services
from app.models.nodejs import NodeJSRuntimeInfo
from app.schemas.nodejs import (
    NodeJSExecuteRequest,
    NodeJSExecuteResponse,
    NodeJSCreateSessionRequest,
    NodeJSUpdateSessionRequest,
    NodeJSSessionInfo,
    NodeJSSessionListResponse,
    NodeJSSessionResponse,
    NodeJSCreateSessionResponse,
    NodeJSUpdateSessionResponse,
    NodeJSDeleteSessionResponse,
)
from app.schemas.response import Response
from app.utils import get_workspace, validate_cwd


if TYPE_CHECKING:
    from app.services.nodejs import NodeJSService


logger = logging.getLogger(__name__)

router = APIRouter()

NODE_VERSION_QUERY = Query(
    default=None,
    description='Node.js version to target: "node20", "node22", "node24", or aliases "20", "22", "24"',
)


@router.post(
    '/execute',
    response_model=Response[NodeJSExecuteResponse],
    operation_id='execute_nodejs_code',
    openapi_extra={
        'x-fern-sdk-group-name': 'nodejs',
        'x-fern-sdk-method-name': 'execute_code',
    },
)
async def execute_nodejs_code(request: NodeJSExecuteRequest):
    """
    Execute JavaScript code using Node.js

    This endpoint allows you to execute JavaScript code and get results back.

    For stateless execution (default):
    - Each request creates a fresh execution environment
    - Environment is cleaned up automatically after execution

    For stateful execution (stateful=True):
    - Uses persistent REPL session that maintains state between requests
    - Variables, functions, and imports persist across calls
    - Returns session_id to continue the session in subsequent requests
    - Supports async/await at top level
    """
    try:
        nodejs_service: 'NodeJSService' = services.require('nodejs_service')
        logger.info(
            f'Executing JavaScript code (stateful={request.stateful}, '
            f'session_id={request.session_id})'
        )

        try:
            effective_cwd = validate_cwd(request.cwd, default=get_workspace())
        except ValueError as e:
            return Response(
                success=False,
                message=str(e),
                data=NodeJSExecuteResponse(
                    language='javascript', status='error', execution_count=None,
                    outputs=[{'output_type': 'error', 'ename': 'DirectoryError', 'evalue': str(e), 'traceback': []}],
                    code=request.code, stdout='', stderr=str(e), exit_code=1,
                ).model_dump(),
            )

        # Choose execution method based on stateful flag
        if request.stateful:
            result = await nodejs_service.execute_code_stateful(
                code=request.code,
                timeout=request.timeout or 30,
                session_id=request.session_id,
                cwd=effective_cwd,
                version=request.version,
            )
        else:
            result = await nodejs_service.execute_code(
                code=request.code,
                timeout=request.timeout or 30,
                stdin=request.stdin,
                files=request.files,
                cwd=effective_cwd,
                version=request.version,
            )

        logger.info(f'Execution result: {result.status}')

        # Convert NodeJSExecuteResult to NodeJSExecuteResponse for API layer
        response_data = NodeJSExecuteResponse(
            language=result.language,
            status=result.status.value,
            execution_count=result.execution_count,
            outputs=[output.model_dump() for output in result.outputs],
            code=result.code,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            session_id=result.session_id,
        )

        # Determine success based on status
        success = result.status.value == 'ok'
        message = (
            'JavaScript code executed successfully'
            if success
            else f'Code execution {result.status.value}'
        )

        return Response(
            success=success, message=message, data=response_data.model_dump()
        )

    except Exception as e:
        logger.error(f'Node.js execution error: {e}', exc_info=True)

        # Return a proper error response
        error_response = NodeJSExecuteResponse(
            language='javascript',
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
            stdout='',
            stderr=str(e),
            exit_code=1,
        )

        return Response(
            success=False,
            message=f'Code execution failed: {str(e)}',
            data=error_response.model_dump(),
        )


@router.get(
    '/info',
    response_model=Response[NodeJSRuntimeInfo],
    operation_id='get_nodejs_info',
    openapi_extra={
        'x-fern-sdk-group-name': 'nodejs',
        'x-fern-sdk-method-name': 'get_info',
    },
)
async def nodejs_info():
    """
    Get information about Node.js REPL runtime, including installed packages

    Returns Node.js version, npm version, and lists of installed packages
    from both the runtime directory and global npm directory.
    """
    nodejs_service: 'NodeJSService' = services.require('nodejs_service')
    # Get detailed info from REPL server (includes packages)
    info = await nodejs_service.get_repl_runtime_info()

    pkg_count = len(info.runtime_packages) + len(info.global_packages)
    return Response(
        success=True,
        message=f'Node.js {info.node_version} - {pkg_count} packages available',
        data=info.model_dump(),
    )


# ==================== Session CRUD Endpoints ====================


@router.post(
    '/sessions',
    response_model=Response[NodeJSCreateSessionResponse],
    operation_id='create_nodejs_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'nodejs',
        'x-fern-sdk-method-name': 'create_session',
    },
)
async def create_nodejs_session(
    request: NodeJSCreateSessionRequest,
    version: str | None = NODE_VERSION_QUERY,
):
    """
    Create a new Node.js REPL session

    Creates a new persistent REPL session with configurable working directory
    and idle timeout. Use the returned session_id in subsequent execute requests.
    """
    nodejs_service: 'NodeJSService' = services.require('nodejs_service')
    logger.info(
        f'Creating session (session_id={request.session_id}, cwd={request.cwd}, version={version})'
    )

    result = await nodejs_service.create_session(
        session_id=request.session_id,
        cwd=request.cwd or get_workspace(),
        max_idle_time=request.max_idle_time or 1800,
        version=version,
    )

    # Convert session info if present
    session_info = None
    if result.get('session'):
        session_data = result['session']
        session_info = NodeJSSessionInfo(
            session_id=session_data['session_id'],
            cwd=session_data['cwd'],
            created_at=session_data['created_at'],
            last_used=session_data['last_used'],
            max_idle_time=session_data['max_idle_time'],
            age_seconds=session_data['age_seconds'],
            state=session_data['state'],
        )

    response_data = NodeJSCreateSessionResponse(
        session_id=result['session_id'],
        created=result['created'],
        message=result.get('message'),
        session=session_info,
    )

    return Response(
        success=True,
        message='Session created successfully' if result['created'] else 'Session already exists',
        data=response_data.model_dump(),
    )


@router.get(
    '/sessions',
    response_model=Response[NodeJSSessionListResponse],
    operation_id='list_nodejs_sessions',
    openapi_extra={
        'x-fern-sdk-group-name': 'nodejs',
        'x-fern-sdk-method-name': 'list_sessions',
    },
)
async def list_nodejs_sessions(version: str | None = NODE_VERSION_QUERY):
    """
    List all active Node.js REPL sessions

    Returns information about all active sessions including their state,
    working directory, and idle time.
    """
    nodejs_service: 'NodeJSService' = services.require('nodejs_service')
    sessions = await nodejs_service.list_sessions(version=version)

    # Convert to schema format
    session_infos = {}
    for session_id, session_data in sessions.items():
        session_infos[session_id] = NodeJSSessionInfo(
            session_id=session_data.get('session_id', session_id),
            cwd=session_data.get('cwd', '/tmp'),
            created_at=session_data.get('created_at', 0),
            last_used=session_data.get('last_used', 0),
            max_idle_time=session_data.get('max_idle_time', 1800000),
            age_seconds=session_data.get('age_seconds', 0),
            state=session_data.get('state', 'IDLE'),
        )

    response_data = NodeJSSessionListResponse(sessions=session_infos)

    return Response(
        success=True,
        message=f'Found {len(sessions)} active sessions',
        data=response_data.model_dump(),
    )


@router.get(
    '/sessions/{session_id}',
    response_model=Response[NodeJSSessionResponse],
    operation_id='get_nodejs_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'nodejs',
        'x-fern-sdk-method-name': 'get_session',
    },
)
async def get_nodejs_session(
    session_id: str,
    version: str | None = NODE_VERSION_QUERY,
):
    """
    Get information about a specific Node.js REPL session

    Returns detailed information about a session including its state,
    working directory, creation time, and idle time.
    """
    nodejs_service: 'NodeJSService' = services.require('nodejs_service')
    session_data = await nodejs_service.get_session(session_id, version=version)

    if session_data is None:
        raise ResourceNotFoundException('Session not found')

    session_info = NodeJSSessionInfo(
        session_id=session_data.get('session_id', session_id),
        cwd=session_data.get('cwd', '/tmp'),
        created_at=session_data.get('created_at', 0),
        last_used=session_data.get('last_used', 0),
        max_idle_time=session_data.get('max_idle_time', 1800000),
        age_seconds=session_data.get('age_seconds', 0),
        state=session_data.get('state', 'IDLE'),
    )

    response_data = NodeJSSessionResponse(session=session_info)

    return Response(
        success=True,
        message='Session found',
        data=response_data.model_dump(),
    )


@router.patch(
    '/sessions/{session_id}',
    response_model=Response[NodeJSUpdateSessionResponse],
    operation_id='update_nodejs_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'nodejs',
        'x-fern-sdk-method-name': 'update_session',
    },
)
async def update_nodejs_session(
    session_id: str,
    request: NodeJSUpdateSessionRequest,
    version: str | None = NODE_VERSION_QUERY,
):
    """
    Update a Node.js REPL session configuration

    Updates session properties like maximum idle time or working directory.
    """
    nodejs_service: 'NodeJSService' = services.require('nodejs_service')
    session_data = await nodejs_service.update_session(
        session_id=session_id,
        max_idle_time=request.max_idle_time,
        cwd=request.cwd,
        version=version,
    )

    if session_data is None:
        raise ResourceNotFoundException('Session not found')

    session_info = NodeJSSessionInfo(
        session_id=session_data.get('session_id', session_id),
        cwd=session_data.get('cwd', '/tmp'),
        created_at=session_data.get('created_at', 0),
        last_used=session_data.get('last_used', 0),
        max_idle_time=session_data.get('max_idle_time', 1800000),
        age_seconds=session_data.get('age_seconds', 0),
        state=session_data.get('state', 'IDLE'),
    )

    response_data = NodeJSUpdateSessionResponse(
        updated=True,
        session=session_info,
    )

    return Response(
        success=True,
        message='Session updated successfully',
        data=response_data.model_dump(),
    )


@router.delete(
    '/sessions/{session_id}',
    response_model=Response[NodeJSDeleteSessionResponse],
    operation_id='delete_nodejs_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'nodejs',
        'x-fern-sdk-method-name': 'delete_session',
    },
)
async def delete_nodejs_session(
    session_id: str,
    version: str | None = NODE_VERSION_QUERY,
):
    """
    Delete a Node.js REPL session

    Terminates the session and releases all associated resources.
    """
    nodejs_service: 'NodeJSService' = services.require('nodejs_service')
    deleted = await nodejs_service.delete_session(session_id, version=version)

    if not deleted:
        raise ResourceNotFoundException('Session not found')

    response_data = NodeJSDeleteSessionResponse(deleted=True)

    return Response(
        success=True,
        message='Session deleted successfully',
        data=response_data.model_dump(),
    )
