"""
API endpoints for the /v1/bash pipe-based shell service.

Provides REST and WebSocket interfaces for pipe-based bash execution.
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.core.exceptions import BadRequestException, ResourceNotFoundException
from app.logging.websocket import (
    bind_websocket_logid,
    log_websocket_event,
    restore_logid,
)
from app.utils import validate_cwd
from app.core.service_container import services
from app.models.bash import (
    BashExecResult,
    BashOutputResult,
    BashSessionInfo,
    SessionStatus,
)
from app.schemas.bash import (
    BashExecRequest,
    BashKillRequest,
    BashOutputRequest,
    BashSessionCreateRequest,
    BashWriteRequest,
)
from app.schemas.response import Response
from app.services.bash import PipeBashManager

router = APIRouter()
logger = logging.getLogger(__name__)


def get_bash_manager() -> PipeBashManager:
    """Dependency provider for PipeBashManager from ServiceContainer."""
    manager: PipeBashManager = services.get('bash_manager')
    if manager is None:
        raise HTTPException(status_code=503, detail='Bash service not available')
    return manager


@router.post(
    '/exec',
    response_model=Response[BashExecResult],
    operation_id='exec',
    openapi_extra={
        'x-fern-sdk-group-name': 'bash',
        'x-fern-sdk-method-name': 'exec',
    },
)
async def exec(
    request: BashExecRequest,
    manager: PipeBashManager = Depends(get_bash_manager),
):
    """Execute a bash command.

    Status values in `data.status`:
    - `running`: the process is still executing. Returned immediately for
      `async_mode=true`, or when sync mode hits `timeout` before completion.
    - `completed`: the process exited and `exit_code` is available. This does
      not imply success; non-zero shell exit codes still use `completed`.
    - `timed_out`: the process exceeded `hard_timeout` and was force-killed.
    - `killed`: the process was terminated by `/v1/bash/kill`, session cleanup,
      or an internal execution failure before normal completion.

    How to consume the result:
    - Treat `status` as lifecycle state, not success/failure.
    - If `status=running`, keep polling `/v1/bash/output` with the returned
      `session_id`, `command_id`, `offset`, and `stderr_offset`.
    - If `status=completed`, check `exit_code`: `0` means success, non-zero
      means the command finished but failed.
    - If `status=timed_out` or `status=killed`, show the current output as a
      partial result and surface the command as interrupted.
    - Empty `stdout` or `stderr` does not mean the command did not run.

    - async_mode=true: returns immediately with RUNNING status
    - async_mode=false + timeout: waits up to timeout, then returns RUNNING if not done
    - async_mode=false + no timeout: waits until command completes
    - hard_timeout: kills the process after this many seconds
    - env: extra environment variables for this command only
    """
    try:
        validated_dir = validate_cwd(request.exec_dir)
    except ValueError as e:
        raise BadRequestException(str(e))

    result = await manager.exec_command(
        session_id=request.session_id,
        command=request.command,
        working_dir=validated_dir,
        async_mode=request.async_mode,
        timeout=request.timeout,
        hard_timeout=request.hard_timeout,
        env=request.env,
        max_output_length=request.max_output_length,
    )
    return Response(data=result)


@router.post(
    '/output',
    response_model=Response[BashOutputResult],
    operation_id='output',
    openapi_extra={
        'x-fern-sdk-group-name': 'bash',
        'x-fern-sdk-method-name': 'output',
    },
)
async def output(
    request: BashOutputRequest,
    manager: PipeBashManager = Depends(get_bash_manager),
):
    """Read output from a bash session using offset-based streaming.

    `data.command.status` uses the same command-status values as `/v1/bash/exec`:
    `running`, `completed`, `timed_out`, or `killed`.

    Recommended consumption pattern:
    - Start with `/v1/bash/exec`.
    - If the command is still `running`, call `/v1/bash/output` repeatedly.
    - Reuse the returned `offset` and `stderr_offset` to fetch only new data.
    - Stop polling when `data.command.status` is no longer `running`.
    - Use the final `exit_code` to decide whether the command succeeded.

    - offset/stderr_offset: byte offsets to read from (use values from previous response)
    - wait=true: long-poll until new output arrives or wait_timeout
    """
    result = await manager.get_output(
        session_id=request.session_id,
        offset=request.offset,
        stderr_offset=request.stderr_offset,
        wait=request.wait,
        wait_timeout=request.wait_timeout,
    )
    return Response(data=result)


@router.post(
    '/write',
    response_model=Response,
    operation_id='write',
    openapi_extra={
        'x-fern-sdk-group-name': 'bash',
        'x-fern-sdk-method-name': 'write',
    },
)
async def write(
    request: BashWriteRequest,
    manager: PipeBashManager = Depends(get_bash_manager),
):
    """Write input to a bash session's stdin."""
    await manager.write_stdin(
        request.session_id,
        request.input,
        command_id=request.command_id,
    )
    return Response(message='Input written')


@router.post(
    '/kill',
    response_model=Response,
    operation_id='kill',
    openapi_extra={
        'x-fern-sdk-group-name': 'bash',
        'x-fern-sdk-method-name': 'kill',
    },
)
async def kill(
    request: BashKillRequest,
    manager: PipeBashManager = Depends(get_bash_manager),
):
    """Send a signal to a bash session's process."""
    await manager.kill_session(request.session_id, request.signal)
    return Response(message=f'Signal {request.signal} sent')


@router.get(
    '/sessions',
    response_model=Response[list[BashSessionInfo]],
    operation_id='sessions',
    openapi_extra={
        'x-fern-sdk-group-name': 'bash',
        'x-fern-sdk-method-name': 'sessions',
    },
)
async def sessions(
    manager: PipeBashManager = Depends(get_bash_manager),
):
    """List all active bash sessions.

    Session status values:
    - `ready`: session exists and can accept commands
    - `closed`: session has been closed and cannot be reused
    """
    return Response(data=manager.list_sessions())


@router.post(
    '/sessions/create',
    response_model=Response[BashSessionInfo],
    operation_id='create_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'bash',
        'x-fern-sdk-method-name': 'create_session',
    },
)
async def create_session(
    request: BashSessionCreateRequest,
    manager: PipeBashManager = Depends(get_bash_manager),
):
    """Create a new bash session."""
    try:
        validated_dir = validate_cwd(request.exec_dir)
    except ValueError as e:
        raise BadRequestException(str(e))

    session = await manager.create_session(
        session_id=request.session_id,
        working_dir=validated_dir,
        snapshot_path=request.snapshot_path,
    )
    return Response(data=session.to_info())


@router.post(
    '/sessions/{session_id}/close',
    response_model=Response,
    operation_id='close_session',
    openapi_extra={
        'x-fern-sdk-group-name': 'bash',
        'x-fern-sdk-method-name': 'close_session',
    },
)
async def close_session(
    session_id: str,
    manager: PipeBashManager = Depends(get_bash_manager),
):
    """Close a bash session."""
    await manager.close_session(session_id)
    return Response(message=f'Session {session_id} closed')


@router.websocket('/ws')
async def websocket(
    websocket: WebSocket,
    session_id: Optional[str] = None,
):
    """WebSocket terminal interface backed by pipe-based bash.

    Each input message spawns a new process (process-per-command model).

    Messages:
    - Client -> Server: {"type": "exec", "data": "ls -la"}
    - Client -> Server: {"type": "input", "data": "stdin content"}
    - Server -> Client: {"type": "output", "data": "file1\\n"}
    - Server -> Client: {"type": "session_id", "data": "xxx"}
    - Server -> Client: {"type": "command_done", "data": {"exit_code": 0, "command_id": "..."}}
    """
    manager = get_bash_manager()
    ws_logid, previous_logid = bind_websocket_logid(websocket)
    session = None
    created_by_ws = False

    try:
        await websocket.accept()
        # Get or create session
        if session_id:
            try:
                session = manager.get_session(session_id)
            except (ResourceNotFoundException, BadRequestException):
                session = await manager.create_session(session_id=session_id)
                created_by_ws = True
        else:
            session = await manager.create_session()
            created_by_ws = True
        session_id = session.id

        log_websocket_event(
            logger,
            'connect',
            websocket=websocket,
            route='/v1/bash/ws',
            session_id=session_id,
            logid=ws_logid,
            details={'new_session': created_by_ws},
        )

        # Send session ID to client
        await websocket.send_text(
            json.dumps({'type': 'session_id', 'data': session.id})
        )

        offset = session._stdout_trimmed + len(session.output_stream)

        async def send_output():
            """Forward new output to the WebSocket client."""
            nonlocal offset
            last_command_id_sent: Optional[str] = None
            while True:
                result = await session.wait_for_output(
                    offset=offset, timeout=30.0, streaming=True
                )
                if result.stdout:
                    await websocket.send_text(
                        json.dumps({'type': 'output', 'data': result.stdout})
                    )
                if (
                    result.command
                    and result.command.exit_code is not None
                    and result.command.command_id != last_command_id_sent
                ):
                    last_command_id_sent = result.command.command_id
                    await websocket.send_text(
                        json.dumps({
                            'type': 'command_done',
                            'data': {
                                'command_id': result.command.command_id,
                                'exit_code': result.command.exit_code,
                            },
                        })
                    )
                offset = result.offset
                if session.status == SessionStatus.CLOSED:
                    break

        async def receive_input():
            """Receive input from the WebSocket client."""
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get('type', 'exec')
                data = msg.get('data', '')

                if msg_type == 'exec':
                    # Each exec message spawns a new process
                    await session.send_command(data, async_mode=True)
                elif msg_type == 'input':
                    try:
                        await session.write_stdin(data)
                    except BadRequestException:
                        pass  # No running process
                elif msg_type == 'resize':
                    pass  # Pipe mode has no resize support

        send_task = asyncio.create_task(send_output())
        recv_task = asyncio.create_task(receive_input())

        done, pending = await asyncio.wait(
            [send_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()

    except WebSocketDisconnect:
        log_websocket_event(
            logger,
            'disconnect',
            websocket=websocket,
            route='/v1/bash/ws',
            session_id=session_id,
            logid=ws_logid,
            details={'new_session': created_by_ws},
        )
    except Exception as e:
        log_websocket_event(
            logger,
            'error',
            websocket=websocket,
            route='/v1/bash/ws',
            session_id=session_id,
            level=logging.ERROR,
            logid=ws_logid,
            details={
                'new_session': created_by_ws,
                'error': {
                    'type': type(e).__name__,
                    'message': str(e),
                },
            },
        )
        try:
            await websocket.close(code=1011, reason=str(e))
        except Exception:
            pass
    finally:
        # Clean up session created by this WebSocket connection
        if created_by_ws and session is not None:
            try:
                await manager.close_session(session.id)
            except Exception:
                pass
        restore_logid(previous_logid)
