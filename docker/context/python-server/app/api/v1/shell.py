import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response as FastAPIResponse, StreamingResponse

from app.core.exceptions import BadRequestException, ResourceNotFoundException
from app.core.service_container import services
from app.logging.sanitizer import sanitize_for_logging
from app.logging.websocket import (
    bind_websocket_logid,
    log_websocket_event,
    restore_logid,
)
from app.models.shell import (
    ActiveShellSessionsResult,
    BashCommandStatus,
    ShellCommandResult,
    ShellKillResult,
    ShellViewResult,
    ShellWaitResult,
    ShellWriteResult,
)
from app.schemas.response import Response
from app.schemas.shell import (
    ShellCreateSessionRequest,
    ShellCreateSessionResponse,
    ShellExecRequest,
    ShellKillProcessRequest,
    ShellSessionStats,
    ShellUpdateSessionRequest,
    ShellViewRequest,
    ShellWaitRequest,
    ShellWriteToProcessRequest,
)
from app.services.shell import OpenHandsShellManager
from app.utils import validate_cwd


router = APIRouter()
logger = logging.getLogger(__name__)
SSE_HEADERS = {
    'Cache-Control': 'no-cache',
    'X-Accel-Buffering': 'no',
}


def _sse_comment(message: str) -> str:
    return f': {message}\n\n'


def _sse_data(payload: dict[str, object]) -> str:
    return f'data: {json.dumps(payload)}\n\n'


def _session_has_active_command(session) -> bool:
    """Best-effort check for whether a shell session still has queued/running work."""
    return bool(
        getattr(
            session,
            'has_active_command',
            getattr(session, 'status', None) == BashCommandStatus.RUNNING,
        )
    )


async def _wait_for_command_completion(
    session_id: str,
    max_wait_time: int,
):
    """等待命令完成（通过检查会话状态）

    Args:
        session_id: Shell session ID
        max_wait_time: Maximum allowed wait time as safety limit (default 300s)
    """
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')
    session = terminal_manager.get_session(session_id)
    if not session or not session.active:
        return

    # Use the smaller of timeout and hard_timeout
    poll_interval = 0.1  # 每100ms检查一次状态
    waited_time = 0

    while waited_time < max_wait_time:
        session = terminal_manager.get_session(session_id)
        if not session or not session.active:
            return
        if not _session_has_active_command(session):
            break
        await asyncio.sleep(poll_interval)
        waited_time += poll_interval


async def receive_input(websocket: WebSocket, session):
    """接收 WebSocket 输入并发送到终端（使用 terminado）"""
    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                # 尝试解析 JSON 消息
                message = json.loads(raw_data)
                if message.get('type') == 'input':
                    session.send_input(message.get('data', ''))
                elif message.get('type') == 'resize':
                    cols = message.get('data', {}).get('cols', 80)
                    rows = message.get('data', {}).get('rows', 24)
                    try:
                        session.resize(rows, cols)
                    except Exception as e:
                        logger.warning(f'Failed to resize terminal: {e}')
                elif message.get('type') == 'ping':
                    # 响应心跳ping消息
                    pong_message = {
                        'type': 'pong',
                        'timestamp': message.get('timestamp', int(time.time() * 1000)),
                    }
                    await websocket.send_text(json.dumps(pong_message))
            except json.JSONDecodeError:
                # 如果不是 JSON，直接作为输入发送（向后兼容）
                session.send_input(raw_data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f'Error receiving input: {e}')
        raise


async def send_output(websocket: WebSocket, queue: asyncio.Queue):
    """从队列读取输出并发送到 WebSocket"""
    try:
        while True:
            output = await queue.get()
            # 发送标准格式的消息给 xterm.js
            message = {'type': 'output', 'data': output}
            await websocket.send_text(json.dumps(message))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f'Error sending output: {e}')
        raise


async def send_heartbeat(websocket: WebSocket):
    """定期发送心跳ping消息以保持连接活跃"""
    try:
        while True:
            # 每30秒发送一次心跳
            await asyncio.sleep(30)
            ping_message = {'type': 'ping', 'timestamp': int(time.time() * 1000)}
            await websocket.send_text(json.dumps(ping_message))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f'Error sending heartbeat: {e}')
        raise


@router.post(
    '/exec',
    response_model=Response[ShellCommandResult],
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'exec_command',
    },
)
async def exec_command(request: ShellExecRequest, http_request: Request):
    """
    Execute command in the specified shell session
    Supports SSE streaming if Accept header contains 'text/event-stream'
    """
    # If no session ID is provided, automatically create one

    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')

    accept = http_request.headers.get('accept', '')
    try:
        working_dir = validate_cwd(request.exec_dir)
    except ValueError:
        if request.strict is True:
            working_dir = request.exec_dir
        else:
            working_dir = None

    logger.info(
        'exec_command request: %s',
        sanitize_for_logging(request.model_dump(exclude_none=True), field_name='request'),
    )

    if not request.id:
        session = await terminal_manager.create_session(
            working_dir=working_dir,
            preserve_symlinks=request.preserve_symlinks,
        )
        session_id = session.id
    else:
        session_id = request.id
        session = terminal_manager.get_session(session_id)
        if not session:
            raise ResourceNotFoundException('Session not found')

    """
    FIXME: sse output bug: output will stuck sse pipeline finished
    """
    # 支持 SSE 流式输出
    if accept and 'text/event-stream' in accept:
        if not request.async_mode:
            result = await terminal_manager.execute_command(
                session_id,
                request.command,
                async_mode=False,
                working_dir=working_dir,
                hidden=not request.truncate,
            )
            body = _sse_comment('connected')
            if result.output:
                body += _sse_data({'output': result.output})
            body += _sse_data({'end': True, 'message': 'No more output'})
            return FastAPIResponse(
                body,
                media_type='text/event-stream',
                headers=SSE_HEADERS,
            )

        async def generate():
            # 创建订阅
            queue = await terminal_manager.subscribe_char(
                session_id, f'sse_{session_id}'
            )
            if not queue:
                yield _sse_data({'error': 'Failed to subscribe'})
                return

            # Flush response headers immediately so clients can enter the SSE stream
            # before the command is scheduled or produces output.
            yield _sse_comment('connected')

            try:
                try:
                    # 异步执行命令
                    # OpenHands hidden=True 表示输出不被截断，因此 truncate=False 时需要 hidden=True
                    await terminal_manager.execute_command(
                        session_id,
                        request.command,
                        async_mode=True,
                        working_dir=working_dir,
                        hidden=not request.truncate,
                    )
                except Exception as exc:
                    yield _sse_data({'error': str(exc)})
                    return

                # 流式输出
                while True:
                    try:
                        output = await asyncio.wait_for(queue.get(), timeout=0.5)
                        yield _sse_data({'output': output})
                    except asyncio.TimeoutError:
                        current_session = terminal_manager.get_session(session_id)
                        if (
                            not current_session
                            or not current_session.active
                            or not _session_has_active_command(current_session)
                        ):
                            yield _sse_data(
                                {'end': True, 'message': 'No more output'}
                            )
                            break

            finally:
                await terminal_manager.unsubscribe_char(session_id, f'sse_{session_id}')

        return StreamingResponse(
            generate(),
            media_type='text/event-stream',
            headers=SSE_HEADERS,
        )
    else:
        # 同步执行
        command_timeout = (
            request.timeout if request.timeout and request.timeout > 0 else None
        )

        if request.async_mode:
            # 异步模式：立即返回
            exec_kwargs = {'async_mode': True}
            if command_timeout:
                exec_kwargs['timeout'] = command_timeout
            if request.strict is not None:
                exec_kwargs['strict'] = request.strict
            if request.no_change_timeout is not None:
                exec_kwargs['no_change_timeout'] = request.no_change_timeout
            if request.hard_timeout is not None:
                exec_kwargs['hard_timeout'] = request.hard_timeout
            # OpenHands hidden=True 表示输出不被截断，因此 truncate=False 时需要 hidden=True
            result = await terminal_manager.execute_command(
                session_id,
                request.command,
                working_dir=working_dir,
                hidden=not request.truncate,
                **exec_kwargs,
            )
            return Response(
                success=True,
                message='Command sent for execution',
                data=result.model_dump(),
            )
        else:
            # 同步模式：等待输出
            exec_kwargs = {'async_mode': False}
            if command_timeout:
                exec_kwargs['timeout'] = command_timeout
            if request.strict is not None:
                exec_kwargs['strict'] = request.strict
            if request.no_change_timeout is not None:
                exec_kwargs['no_change_timeout'] = request.no_change_timeout
            if request.hard_timeout is not None:
                exec_kwargs['hard_timeout'] = request.hard_timeout

            # OpenHands hidden=True 表示输出不被截断，因此 truncate=False 时需要 hidden=True
            command_coro = terminal_manager.execute_command(
                session_id,
                request.command,
                working_dir=working_dir,
                hidden=not request.truncate,
                **exec_kwargs,
            )

            if command_timeout:
                command_task = asyncio.create_task(command_coro)
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(command_task), timeout=command_timeout
                    )
                    return Response(
                        success=True,
                        message='Command executed',
                        data=result.model_dump(),
                    )
                except asyncio.TimeoutError:

                    def _command_done_callback(task: asyncio.Task):
                        if task.cancelled():
                            logger.info(
                                f'Command task cancelled after timeout for session {session_id}'
                            )
                            return
                        exc = task.exception()
                        if exc:
                            logger.error(
                                f'Command task failed after timeout for session {session_id}: {exc}'
                            )

                    command_task.add_done_callback(_command_done_callback)

                running_result = ShellCommandResult(
                    session_id=session_id,
                    command=request.command,
                    status=BashCommandStatus.RUNNING,
                    output=None,
                    console=None,
                    exit_code=None,
                )
                timeout_message = (
                    f'Command still running (timeout {command_timeout:g}s reached)'
                )
                return Response(
                    success=True,
                    message=timeout_message,
                    data=running_result.model_dump(),
                )

            result = await command_coro
            return Response(
                success=True, message='Command executed', data=result.model_dump()
            )


@router.post(
    '/view',
    response_model=Response[ShellViewResult],
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'view',
    },
)
async def view_shell(request: ShellViewRequest, http_request: Request):
    """
    View output of the specified shell session
    Supports SSE streaming if Accept header contains 'text/event-stream'
    """

    accept = http_request.headers.get('accept', '')
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')

    """
    FIXME: sse output bug: output will stuck sse pipeline finished
    """
    if accept and 'text/event-stream' in accept:
        try:
            initial_view = await terminal_manager.view_session(request.id)
        except ValueError:
            raise ResourceNotFoundException('Session not found')

        if initial_view.status != BashCommandStatus.RUNNING:
            body = _sse_comment('connected')
            if initial_view.output:
                body += _sse_data({'output': initial_view.output})
            body += _sse_data({'end': True, 'message': 'No more output'})
            return FastAPIResponse(
                body,
                media_type='text/event-stream',
                headers=SSE_HEADERS,
            )

        # SSE 流式输出
        async def generate():
            queue = await terminal_manager.subscribe_char(
                request.id, f'view_{request.id}'
            )
            if not queue:
                yield _sse_data({'error': 'Failed to subscribe'})
                return

            try:
                yield _sse_comment('connected')
                if initial_view.output:
                    yield _sse_data({'output': initial_view.output})
                timeout_count = 0
                max_timeouts = 3
                while timeout_count < max_timeouts:
                    try:
                        output = await asyncio.wait_for(queue.get(), timeout=1)
                        yield _sse_data({'output': output})
                        timeout_count = 0
                    except asyncio.TimeoutError:
                        session = terminal_manager.get_session(request.id)
                        if (
                            not session
                            or not session.active
                            or not _session_has_active_command(session)
                        ):
                            yield _sse_data(
                                {'end': True, 'message': 'No more output'}
                            )
                            break
                        timeout_count += 1
                        if timeout_count < max_timeouts:
                            yield _sse_data({'keepalive': True})
                        else:
                            yield _sse_data(
                                {'end': True, 'message': 'No more output'}
                            )
                            break

            finally:
                await terminal_manager.unsubscribe_char(
                    request.id, f'view_{request.id}'
                )

        return StreamingResponse(
            generate(),
            media_type='text/event-stream',
            headers=SSE_HEADERS,
        )
    else:
        try:
            view_result = await terminal_manager.view_session(request.id)
            return Response(
                success=True, message='Session output', data=view_result.model_dump()
            )
        except ValueError as e:
            raise ResourceNotFoundException(str(e))


@router.post(
    '/wait',
    response_model=Response[ShellWaitResult],
    operation_id='wait_for_process',
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'wait_for_process',
    },
)
async def wait_for_process(request: ShellWaitRequest):
    """
    Wait for the process in the specified shell session to return
    """
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')
    session = terminal_manager.get_session(request.id)
    if not session:
        raise ResourceNotFoundException('Session not found')

    timeout = request.seconds or 30
    max_wait_seconds = request.max_wait_seconds or 120

    def _current_status() -> Optional[BashCommandStatus]:
        current_session = terminal_manager.get_session(request.id)
        if not current_session:
            return None
        if _session_has_active_command(current_session):
            return BashCommandStatus.RUNNING
        return current_session.status

    def _build_wait_response(
        status: BashCommandStatus, wait_result: ShellWaitResult
    ) -> Response[ShellWaitResult]:
        status_messages = {
            BashCommandStatus.COMPLETED: 'Process completed successfully',
            BashCommandStatus.RUNNING: 'Process is still running',
            BashCommandStatus.NO_CHANGE_TIMEOUT: 'Process is still running (no new output yet)',
            BashCommandStatus.HARD_TIMEOUT: 'Process reached hard timeout',
            BashCommandStatus.TERMINATED: 'Process terminated',
        }
        return Response(
            success=True,
            message=status_messages.get(status, f'Process status: {status.value}'),
            data=wait_result.model_dump(),
        )

    try:
        # 等待命令完成或超时
        await asyncio.wait_for(
            asyncio.create_task(
                _wait_for_command_completion(request.id, max_wait_time=max_wait_seconds)
            ),
            timeout=timeout,
        )

        status = _current_status()
        if status is None:
            # Session lost/unavailable - this is an error
            wait_result = ShellWaitResult(status=BashCommandStatus.TERMINATED)
            return Response(
                success=False,
                message='Session unavailable',
                data=wait_result.model_dump(),
            )

        wait_result = ShellWaitResult(status=status)
        return _build_wait_response(status, wait_result)
    except asyncio.TimeoutError:
        status = _current_status() or BashCommandStatus.RUNNING
        wait_result = ShellWaitResult(status=status)
        return _build_wait_response(status, wait_result)


@router.post(
    '/write',
    response_model=Response[ShellWriteResult],
    operation_id='write_to_process',
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'write_to_process',
    },
)
async def write_to_process(request: ShellWriteToProcessRequest):
    """
    Write input to the process in the specified shell session
    """
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')
    session = terminal_manager.get_session(request.id)
    if not session:
        raise ResourceNotFoundException('Session not found')

    if not session.active:
        raise BadRequestException('Session is not active')

    # Backward compatibility: historically /write treated input as a command
    # to execute asynchronously in the target shell session.
    await terminal_manager.execute_command(request.id, request.input, async_mode=True)

    write_result = ShellWriteResult(status=BashCommandStatus.RUNNING)
    return Response(
        success=True,
        message='Input executed successfully',
        data=write_result.model_dump(),
    )


@router.post(
    '/kill',
    response_model=Response[ShellKillResult],
    operation_id='kill_shell_process',
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'kill_process',
    },
)
async def kill_process(request: ShellKillProcessRequest):
    """
    Terminate the process in the specified shell session
    """
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')
    session = terminal_manager.get_session(request.id)
    if not session:
        raise ResourceNotFoundException('Session not found')

    # Get exit_code before deleting session
    exit_code = session.exit_code

    success = await terminal_manager.delete_session(request.id)
    if not success:
        raise HTTPException(status_code=500, detail='Kill session failed')

    kill_result = ShellKillResult(
        status=BashCommandStatus.TERMINATED, exit_code=exit_code
    )
    return Response(
        success=True, message='Process terminated', data=kill_result.model_dump()
    )


@router.post(
    '/sessions/create',
    response_model=Response[ShellCreateSessionResponse],
    operation_id='create_shell_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'create_session',
    },
)
async def create_session(request: ShellCreateSessionRequest):
    """
    Create a new shell session and return its ID
    If id already exists, return the existing session
    """
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')
    try:
        working_dir = validate_cwd(request.exec_dir)
    except ValueError:
        working_dir = None

    # Check if session already exists
    existing_session = terminal_manager.get_session(request.id)
    if existing_session:
        result = ShellCreateSessionResponse(
            session_id=existing_session.id, working_dir=existing_session.working_dir
        )
        return Response(
            success=True,
            message='Session had created successfully',
            data=result.model_dump(),
        )

    # Create new session with the provided/generated id
    session = await terminal_manager.create_session(
        session_id=request.id,
        working_dir=working_dir,
        no_change_timeout=request.no_change_timeout,
        preserve_symlinks=request.preserve_symlinks,
    )

    result = ShellCreateSessionResponse(
        session_id=session.id, working_dir=session.working_dir
    )

    return Response(
        success=True,
        message='Session created successfully',
        data=result.model_dump(),
    )


@router.post(
    '/sessions/update',
    response_model=Response,
    operation_id='update_shell_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'update_session',
    },
)
async def update_session(request: ShellUpdateSessionRequest):
    """
    Update shell session configuration (e.g., no_change_timeout)
    """
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')

    session = await terminal_manager.update_session(
        session_id=request.id,
        no_change_timeout=request.no_change_timeout,
    )

    if not session:
        raise ResourceNotFoundException('Session not found')

    return Response(
        success=True,
        message='Session updated successfully',
        data={
            'session_id': session.id,
            'no_change_timeout': (
                session.bash_session.NO_CHANGE_TIMEOUT_SECONDS
                if session.bash_session
                else None
            ),
        },
    )


@router.get(
    '/terminal-url',
    response_model=Response[str],
    operation_id='get_terminal_url',
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'get_terminal_url',
    },
)
async def get_terminal_url(http_request: Request):
    """
    Create a new shell session and return the terminal URL
    """
    from app.utils import normalize_path_prefix

    terminal_ws_manager = services.require('terminal_ws_manager')

    # Create a new session
    session = await terminal_ws_manager.create_session()

    # Priority: X-Forwarded-Host > Host header > request.url.netloc
    # X-Forwarded-Host preserves the original host when behind reverse proxies
    host = (
        http_request.headers.get('x-forwarded-host')
        or http_request.headers.get('host')
        or http_request.url.netloc
    )

    # Check X-Forwarded-Proto first, fallback to request.url.scheme
    # In reverse proxy scenarios, the internal request may be http
    # but the external client connection is https
    forwarded_proto = http_request.headers.get('x-forwarded-proto', '').lower()
    if forwarded_proto in ('https', 'http'):
        is_https = forwarded_proto == 'https'
    else:
        is_https = http_request.url.scheme == 'https'

    http_scheme = 'https' if is_https else 'http'

    logger.info(
        'Protocol detection - X-Forwarded-Proto: %s, request.url.scheme: %s, final: %s',
        forwarded_proto or 'none',
        http_request.url.scheme,
        http_scheme,
    )

    # Get and normalize path prefix from X-Forwarded-Prefix header
    path_prefix = normalize_path_prefix(
        http_request.headers.get('x-forwarded-prefix')
    )

    # Construct the terminal URL
    terminal_url = (
        f'{http_scheme}://{host}{path_prefix}/terminal?session_id={session.id}'
    )

    return Response(
        success=True, message='Terminal URL created successfully', data=terminal_url
    )


@router.get(
    '/sessions/stats',
    response_model=Response[ShellSessionStats],
    operation_id='get_shell_session_stats',
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'get_session_stats',
    },
)
async def get_session_stats():
    """Return aggregate statistics for shell sessions."""
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')
    stats = terminal_manager.get_session_stats()

    return Response(
        success=True,
        message='Shell session stats retrieved successfully',
        data=stats.model_dump(),
    )


@router.get(
    '/sessions',
    response_model=Response[ActiveShellSessionsResult],
    operation_id='list_shell_sessions',
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'list_sessions',
    },
)
async def list_sessions():
    """
    List all active shell sessions
    """
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')
    sessions = terminal_manager.get_active_sessions()

    return Response(
        success=True,
        message=f'Found {len(sessions.sessions)} active sessions',
        data=sessions.model_dump(),
    )


@router.delete(
    '/sessions/{session_id}',
    response_model=Response,
    operation_id='cleanup_shell_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'cleanup_session',
    },
)
async def cleanup_session(session_id: str):
    """
    Manually cleanup a specific shell session
    """
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')
    success = terminal_manager.cleanup_session(session_id)

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
    operation_id='cleanup_all_sessions',
    openapi_extra={
        'x-fern-sdk-group-name': 'shell',
        'x-fern-sdk-method-name': 'cleanup_all_sessions',
    },
)
async def cleanup_all_sessions():
    """
    Cleanup all active shell sessions
    """
    terminal_manager: OpenHandsShellManager = services.require('terminal_manager')
    sessions_before = len(terminal_manager.get_active_sessions().sessions)
    await terminal_manager.cleanup_all_sessions()

    return Response(
        success=True,
        message=f'Cleaned up {sessions_before} sessions',
        data={'cleaned_sessions': sessions_before},
    )


@router.websocket('/ws')
async def websocket_shell_endpoint(
    websocket: WebSocket, session_id: Optional[str] = Query(None)
):
    terminal_ws_manager = services.require('terminal_ws_manager')
    """
    WebSocket endpoint for interactive shell terminal
    """
    ws_logid, previous_logid = bind_websocket_logid(websocket)
    session = None
    is_new_session = False
    subscriber_id = None
    try:
        await websocket.accept()

        # 如果没有 session_id，创建新会话
        if not session_id:
            session = await terminal_ws_manager.create_session()
            session_id = session.id
            is_new_session = True
            # 只有新会话才发送 session_id 消息
            message = {'type': 'session_id', 'data': session_id}
            await websocket.send_text(json.dumps(message))
        else:
            # 连接到现有会话
            session = terminal_ws_manager.get_session(session_id)
            if not session:
                log_websocket_event(
                    logger,
                    'reject',
                    websocket=websocket,
                    route='/v1/shell/ws',
                    session_id=session_id,
                    logid=ws_logid,
                    details={'reason': 'session_not_found'},
                )
                error_message = {'type': 'error', 'data': 'Session not found'}
                await websocket.send_text(json.dumps(error_message))
                await websocket.close()
                return

        if not session.active:
            log_websocket_event(
                logger,
                'reject',
                websocket=websocket,
                route='/v1/shell/ws',
                session_id=session_id,
                logid=ws_logid,
                details={'reason': 'session_inactive'},
            )
            error_message = {'type': 'error', 'data': 'Session is not active'}
            await websocket.send_text(json.dumps(error_message))
            await websocket.close()
            return

        log_websocket_event(
            logger,
            'connect',
            websocket=websocket,
            route='/v1/shell/ws',
            session_id=session_id,
            logid=ws_logid,
            details={'new_session': is_new_session},
        )

        # 订阅字符输出
        subscriber_id = f'ws_{session_id}_{id(websocket)}'
        queue = await terminal_ws_manager.subscribe_char(session_id, subscriber_id)

        if not queue:
            log_websocket_event(
                logger,
                'reject',
                websocket=websocket,
                route='/v1/shell/ws',
                session_id=session_id,
                logid=ws_logid,
                details={'reason': 'duplicate_websocket_connection'},
            )
            error_message = {
                'type': 'error',
                'data': 'Session already has an active WebSocket connection',
            }
            await websocket.send_text(json.dumps(error_message))
            await websocket.close()
            return

        # 如果是现有会话，恢复历史输出
        if not is_new_session:
            async with session._lock:
                # 发送历史输出
                historical_output = session.output_text or (
                    ''.join(session.output_buffer) if session.output_buffer else ''
                )
                if historical_output.strip():
                    restore_message = {
                        'type': 'restore_output',
                        'data': historical_output,
                    }
                    await websocket.send_text(json.dumps(restore_message))

            # 发送会话恢复完成消息
            restored_message = {
                'type': 'terminal_restored',
                'data': f'Session {session_id[:8]} restored',
            }
            await websocket.send_text(json.dumps(restored_message))
        else:
            # 新会话发送就绪消息
            ready_message = {
                'type': 'ready',
                'data': f'Terminal ready - Session: {session_id[:8]}',
            }
            await websocket.send_text(json.dumps(ready_message))

            # 发送初始提示
            session.send_input('\n')
            await asyncio.sleep(0.2)

        # 启动心跳任务（在后台运行，不等待完成）
        heartbeat_task = asyncio.create_task(send_heartbeat(websocket))

        # 创建主要任务：接收输入和发送输出
        receive_task = asyncio.create_task(receive_input(websocket, session))
        send_task = asyncio.create_task(send_output(websocket, queue))

        # 等待主要任务中的任一个完成（不包括心跳任务）
        done, pending = await asyncio.wait(
            [receive_task, send_task], return_when=asyncio.FIRST_COMPLETED
        )

        # 取消未完成的主要任务
        for task in pending:
            task.cancel()

        # 取消心跳任务
        heartbeat_task.cancel()

        # 等待已完成的任务以确保异常被处理
        for task in done:
            try:
                task.result()
            except Exception:
                pass  # 异常会在其他地方处理

    except WebSocketDisconnect:
        log_websocket_event(
            logger,
            'disconnect',
            websocket=websocket,
            route='/v1/shell/ws',
            session_id=session_id,
            logid=ws_logid,
            details={'new_session': is_new_session},
        )
    except Exception as e:
        log_websocket_event(
            logger,
            'error',
            websocket=websocket,
            route='/v1/shell/ws',
            session_id=session_id,
            level=logging.ERROR,
            logid=ws_logid,
            details={
                'new_session': is_new_session,
                'error': {
                    'type': type(e).__name__,
                    'message': str(e),
                },
            },
        )
    finally:
        if subscriber_id:
            await terminal_ws_manager.unsubscribe_char(session_id, subscriber_id)
        # 只有在WebSocket仍然连接时才尝试关闭
        if websocket.client_state.name != 'DISCONNECTED':
            try:
                await websocket.close()
            except RuntimeError:
                # WebSocket已经关闭，忽略错误
                pass
        restore_logid(previous_logid)
