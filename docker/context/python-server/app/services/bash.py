"""
Pipe-based Bash service implementation — process-per-command model.

Each exec spawns a new bash process that exits on completion.
Each command runs in isolation — env and cwd changes inside a command
do not leak to subsequent commands.  Use the `env` parameter and
`exec_dir` / `working_dir` for explicit per-call control.
No idle processes, no sentinel markers.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shlex
import signal
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from app.core.env import get_env_int
from app.core.exceptions import BadRequestException, ResourceNotFoundException
from app.logging.execution import log_execution_event

from app.models.bash import (
    BashCommand,
    BashCommandInfo,
    BashExecResult,
    BashOutputResult,
    BashSessionInfo,
    CommandStatus,
    SessionStatus,
)
from app.utils import get_aio_runtime_dir, middle_truncate

logger = logging.getLogger(__name__)

# Max output buffer size (10MB)
_MAX_OUTPUT_SIZE = 10 * 1024 * 1024

# Max command history entries per session
_MAX_COMMAND_HISTORY = 100

# Grace period (seconds) for wait_for_output to let _finalize complete
# after output arrives — covers the race where output is read just before
# the process exits.
_FINALIZE_GRACE_SECONDS = 0.1

# Valid POSIX environment variable name
_ENV_KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

# Default working directory — resolved at call time to pick up runtime env vars
def _get_default_working_dir() -> str:
    from app.utils import get_workspace
    return get_workspace()


def _get_bash_snapshot_paths(session_id: str) -> tuple[str, str]:
    """Build stable snapshot paths under the runtime dir."""
    session_key = hashlib.sha256(session_id.encode('utf-8')).hexdigest()
    try:
        bash_root = get_aio_runtime_dir('bash')
    except ValueError as e:
        # Keep /v1/bash usable even when XDG_RUNTIME_DIR is misconfigured.
        bash_root = (
            Path(tempfile.gettempdir())
            / f'aio-runtime-{os.getuid()}'
            / 'aio-sandbox'
            / 'bash'
        )
        logger.warning(
            'Invalid XDG_RUNTIME_DIR for bash snapshots (%s); falling back to %s',
            e,
            bash_root,
        )
    env_dir = bash_root / 'env'
    cwd_dir = bash_root / 'cwd'
    env_dir.mkdir(parents=True, exist_ok=True)
    cwd_dir.mkdir(parents=True, exist_ok=True)
    return (
        str(env_dir / f'{session_key}.sh'),
        str(cwd_dir / f'{session_key}.cwd'),
    )


def _normalize_optional_session_id(session_id: Optional[str]) -> Optional[str]:
    """Treat an empty-string session ID as omitted."""
    if session_id == '':
        return None
    return session_id


class PipeBashSession:
    """Lightweight session — metadata + snapshot files, no persistent process.

    Each send_command() spawns a new bash process.  Env and cwd changes
    inside a command do NOT leak to subsequent commands.  Use the ``env``
    parameter for per-call env vars and ``exec_dir`` / ``working_dir``
    for directory control.
    """

    def __init__(
        self,
        session_id: str,
        working_dir: Optional[str] = None,
        snapshot_path: Optional[str] = None,
    ):
        self.id = session_id
        self.working_dir = working_dir or _get_default_working_dir()
        self.snapshot_path = snapshot_path
        self.status = SessionStatus.READY
        self.created_at = datetime.now()
        self.last_used_at = datetime.now()

        # Snapshot files (cwd managed by Python, env for initial snapshot only)
        self.snapshot_env_path, self.snapshot_cwd_path = _get_bash_snapshot_paths(
            session_id
        )

        # Session-level output buffers (append-only, for offset-based API)
        self.output_stream = bytearray()
        self.stderr_stream = bytearray()
        # Track how many bytes have been trimmed from the front of each buffer,
        # so that client offsets (which are logical/absolute) remain valid.
        self._stdout_trimmed: int = 0
        self._stderr_trimmed: int = 0

        # Command tracking
        self.commands: List[BashCommand] = []
        self.current_command: Optional[BashCommand] = None

        # Subscriber notification
        self._subscribers: Dict[str, asyncio.Event] = {}

        # Execution lock: one command at a time per session
        self._exec_lock = asyncio.Lock()

        # Initialize snapshot from provided snapshot_path
        if snapshot_path and os.path.exists(snapshot_path):
            try:
                import shutil
                shutil.copy2(snapshot_path, self.snapshot_env_path)
            except Exception as e:
                logger.warning(f'Failed to copy snapshot {snapshot_path}: {e}')

        # Initialize cwd snapshot
        self._write_cwd_snapshot(self.working_dir)

    def _write_cwd_snapshot(self, cwd: str) -> None:
        """Write cwd to snapshot file."""
        try:
            Path(self.snapshot_cwd_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.snapshot_cwd_path, 'w') as f:
                f.write(cwd)
        except Exception as e:
            logger.warning(f'Failed to write cwd snapshot: {e}')

    def _build_wrapper_script(
        self,
        command: str,
        env: Optional[Dict[str, str]] = None,
    ) -> str:
        """Build a wrapper bash script that restores env/cwd, runs command, saves state."""
        parts = ['#!/bin/bash']

        # 1. Source env snapshot (restore environment)
        parts.append(f'source {shlex.quote(self.snapshot_env_path)} 2>/dev/null || true')

        # 2. Restore cwd (use parameter expansion to fall back to working_dir
        #    when the snapshot file is missing or empty — bare `cd ""` is a
        #    no-op in bash and would silently inherit the parent's cwd)
        parts.append(
            f'__cwd="$(cat {shlex.quote(self.snapshot_cwd_path)} 2>/dev/null)"'
        )
        parts.append(
            f'cd "${{__cwd:-{shlex.quote(self.working_dir)}}}" 2>/dev/null || '
            f'cd {shlex.quote(self.working_dir)} 2>/dev/null || true'
        )

        # 3. Apply per-call env overrides
        if env:
            for key, value in env.items():
                if not _ENV_KEY_RE.match(key):
                    raise BadRequestException(f'Invalid environment variable name: {key!r}')
                parts.append(f'export {key}={shlex.quote(value)}')

        # 4. Run user command
        parts.append(command)

        # 5. Exit with command's exit code (env and cwd are managed by
        #    Python — not saved here to prevent user `cd`/`export` from
        #    polluting subsequent commands)
        parts.append('exit $?')

        return '\n'.join(parts) + '\n'

    async def send_command(
        self,
        command: str,
        async_mode: bool = False,
        timeout: Optional[float] = None,
        hard_timeout: Optional[float] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> BashCommand:
        """Spawn a new bash process to execute the command.

        Args:
            command: The shell command to execute.
            async_mode: If True, return immediately without waiting.
            timeout: HTTP timeout — return RUNNING if not done in time.
            hard_timeout: Kill the process after this many seconds.
            env: Extra environment variables for this command.

        Returns:
            BashCommand with current status.
        """
        if self.status == SessionStatus.CLOSED:
            raise BadRequestException(f'Session {self.id} is closed')

        async with self._exec_lock:
            self.last_used_at = datetime.now()

            cmd = BashCommand(
                id=str(uuid.uuid4()),
                command=command,
                status=CommandStatus.RUNNING,
                started_at=datetime.now(),
                # Record logical offsets inside _exec_lock to avoid race
                # with concurrent exec_command calls, and to remain valid
                # after buffer trimming.
                stdout_start=self._stdout_trimmed + len(self.output_stream),
                stderr_start=self._stderr_trimmed + len(self.stderr_stream),
            )
            self.current_command = cmd
            self.commands.append(cmd)

            # Build wrapper script
            wrapper = self._build_wrapper_script(command, env)

            # Build base environment
            from app.core.version import build_sandbox_env
            proc_env = build_sandbox_env()

            # Spawn process in its own process group so that kill can
            # terminate the entire tree (prevents orphan child processes
            # from holding pipe FDs open and blocking readers).
            proc = await asyncio.create_subprocess_exec(
                '/bin/bash', '-c', wrapper,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env,
                start_new_session=True,
            )
            cmd.proc = proc

            logger.debug(
                f'Command {cmd.id} started (pid={proc.pid}): {command[:80]}'
            )

            # Start reader tasks
            stdout_task = asyncio.create_task(
                self._read_stream(proc.stdout, self.output_stream),
                name=f'bash-stdout-{cmd.id}',
            )
            stderr_task = asyncio.create_task(
                self._read_stream(proc.stderr, self.stderr_stream),
                name=f'bash-stderr-{cmd.id}',
            )

            async def _finalize():
                """Wait for process exit, drain readers, then signal done.

                This is the single place that drains readers, sets done_event,
                notifies subscribers, releases the process reference, and trims
                command history.  Other code (_hard_timeout_killer) may set
                cmd.status before _finalize runs, but _finalize always handles
                the drain-and-notify lifecycle.
                """
                try:
                    exit_code = await proc.wait()
                    # Wait for readers to drain
                    await stdout_task
                    await stderr_task

                    if cmd.status == CommandStatus.RUNNING:
                        cmd.status = CommandStatus.COMPLETED
                        cmd.exit_code = exit_code
                        cmd.completed_at = datetime.now()
                        logger.debug(
                            f'Command {cmd.id} completed exit_code={exit_code}'
                        )
                except Exception as e:
                    logger.error(f'Finalize error for command {cmd.id}: {e}')
                    if cmd.status == CommandStatus.RUNNING:
                        cmd.status = CommandStatus.KILLED
                        cmd.exit_code = -1
                        cmd.completed_at = datetime.now()
                finally:
                    try:
                        self._log_command_completion(cmd)
                    except Exception as log_error:
                        logger.warning(
                            f'Failed to log completion for command {cmd.id}: {log_error}'
                        )
                    # Always signal completion and clean up, regardless of
                    # whether status was set here or by _hard_timeout_killer.
                    cmd.done_event.set()
                    self._notify_subscribers()
                    cmd.proc = None
                    if self.current_command is cmd:
                        self.current_command = None
                    self._trim_commands()

            # Hard timeout: set status and kill process, _finalize handles the rest
            if hard_timeout is not None:
                async def _hard_timeout_killer():
                    try:
                        await asyncio.wait_for(
                            cmd.done_event.wait(), timeout=hard_timeout
                        )
                    except asyncio.TimeoutError:
                        if cmd.status == CommandStatus.RUNNING:
                            cmd.status = CommandStatus.TIMED_OUT
                            cmd.exit_code = -1
                            cmd.completed_at = datetime.now()
                            try:
                                # Kill the entire process group to avoid
                                # orphan children holding pipe FDs open.
                                os.killpg(proc.pid, signal.SIGKILL)
                            except (ProcessLookupError, OSError):
                                try:
                                    proc.kill()
                                except ProcessLookupError:
                                    pass
                            # _finalize will drain readers, set done_event,
                            # notify subscribers, and clean up.

                asyncio.create_task(_hard_timeout_killer())

            if async_mode:
                # Return immediately, finalize in background
                asyncio.create_task(_finalize(), name=f'bash-finalize-{cmd.id}')
            elif timeout is not None:
                # Start finalize in background
                asyncio.create_task(
                    _finalize(), name=f'bash-finalize-{cmd.id}'
                )
                try:
                    await asyncio.wait_for(cmd.done_event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass  # Command still running, HTTP returns current state
            else:
                # Sync: run finalize and wait
                await _finalize()

            return cmd

    async def _read_stream(
        self,
        stream: asyncio.StreamReader,
        buffer: bytearray,
    ) -> None:
        """Read from a stream and append to buffer."""
        try:
            while True:
                data = await stream.read(4096)
                if not data:
                    break
                buffer.extend(data)
                self._trim_output_if_needed(buffer)
                self._notify_subscribers()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f'Stream reader error: {e}')

    def _notify_subscribers(self) -> None:
        """Wake up all subscribers waiting for new output."""
        for event in self._subscribers.values():
            event.set()

    def _trim_output_if_needed(self, buffer: bytearray) -> None:
        """Trim buffer if it exceeds max size (keep tail).

        Updates the corresponding trimmed-bytes counter so that logical
        (absolute) offsets used by clients remain valid.
        """
        if len(buffer) > _MAX_OUTPUT_SIZE:
            trim_point = len(buffer) - _MAX_OUTPUT_SIZE // 2
            if buffer is self.output_stream:
                self._stdout_trimmed += trim_point
            else:
                self._stderr_trimmed += trim_point
            del buffer[:trim_point]

    def _trim_commands(self) -> None:
        """Trim command history if it exceeds the max, keeping recent entries."""
        if len(self.commands) > _MAX_COMMAND_HISTORY:
            # Keep the last half
            keep = _MAX_COMMAND_HISTORY // 2
            del self.commands[:-keep]

    def _get_command_output_slice(
        self,
        buffer: bytearray,
        start_offset: int,
        trimmed: int,
    ) -> str:
        start_index = max(0, start_offset - trimmed)
        return bytes(buffer[start_index:]).decode('utf-8', errors='replace')

    def _log_command_completion(self, cmd: BashCommand) -> None:
        completed_at = cmd.completed_at or datetime.now()
        duration_ms = (completed_at - cmd.started_at).total_seconds() * 1000
        stdout_data = self._get_command_output_slice(
            self.output_stream, cmd.stdout_start, self._stdout_trimmed
        )
        stderr_data = self._get_command_output_slice(
            self.stderr_stream, cmd.stderr_start, self._stderr_trimmed
        )
        log_level = (
            logging.ERROR
            if cmd.status in (CommandStatus.TIMED_OUT, CommandStatus.KILLED)
            or cmd.exit_code not in (None, 0)
            else logging.INFO
        )

        log_execution_event(
            logger,
            component='bash',
            operation='send_command',
            phase='completed',
            status=cmd.status,
            session_id=self.id,
            command_id=cmd.id,
            exit_code=cmd.exit_code,
            duration_ms=duration_ms,
            details={
                'command': cmd.command,
                'working_dir': self.working_dir,
                'stdout': stdout_data,
                'stderr': stderr_data,
            },
            level=log_level,
        )

    def get_output(
        self,
        offset: int = 0,
        stderr_offset: int = 0,
    ) -> BashOutputResult:
        """Read output from the given (logical/absolute) offset."""
        # Convert logical offset to physical buffer index
        phys_out = max(0, offset - self._stdout_trimmed)
        phys_err = max(0, stderr_offset - self._stderr_trimmed)
        stdout_data = bytes(self.output_stream[phys_out:])
        stderr_data = bytes(self.stderr_stream[phys_err:])

        cmd_info = None
        if self.current_command:
            cmd_info = BashCommandInfo(
                command_id=self.current_command.id,
                command=self.current_command.command,
                status=self.current_command.status,
                exit_code=self.current_command.exit_code,
            )
        elif self.commands:
            last = self.commands[-1]
            cmd_info = BashCommandInfo(
                command_id=last.id,
                command=last.command,
                status=last.status,
                exit_code=last.exit_code,
            )

        return BashOutputResult(
            session_id=self.id,
            stdout=stdout_data.decode('utf-8', errors='replace'),
            stderr=stderr_data.decode('utf-8', errors='replace'),
            offset=self._stdout_trimmed + len(self.output_stream),
            stderr_offset=self._stderr_trimmed + len(self.stderr_stream),
            command=cmd_info,
        )

    def _is_command_done(self) -> bool:
        """Check if the current/last command has reached a terminal state."""
        if self.current_command is None:
            if self.commands:
                return self.commands[-1].status in (
                    CommandStatus.COMPLETED,
                    CommandStatus.TIMED_OUT,
                    CommandStatus.KILLED,
                )
            return True  # no commands at all
        return self.current_command.status != CommandStatus.RUNNING

    async def wait_for_output(
        self,
        offset: int = 0,
        stderr_offset: int = 0,
        timeout: float = 30.0,
        streaming: bool = False,
    ) -> BashOutputResult:
        """Long-poll: wait for new output beyond the given offset.

        Returns when any of these conditions is met:
        - New output is available beyond the given offsets.
        - The current/last command has finished (completed/timed_out/killed).
        - The timeout expires.

        When new output arrives but the command is still running, a short
        grace period is given for the command to finalize (covers the race
        where output arrives just before process exit).

        Args:
            streaming: When True (e.g. WebSocket handler), always subscribe
                and wait even after the last command has finished — prevents
                spin loops and duplicate command_done notifications while
                waiting for the next exec to arrive.  When False (default,
                REST polling), return immediately once the current command
                is done so the caller gets the completion status without
                waiting for the full timeout.
        """
        # Fast path: output already available (compare logical offsets)
        logical_stdout_end = self._stdout_trimmed + len(self.output_stream)
        logical_stderr_end = self._stderr_trimmed + len(self.stderr_stream)
        if logical_stdout_end > offset or logical_stderr_end > stderr_offset:
            # If command is already done, return immediately
            if self._is_command_done():
                return self.get_output(offset, stderr_offset)
            # Output available but command still running — give a short
            # grace period for finalize to complete (covers the common
            # race where output arrives right before process exit)
            cmd = self.current_command
            if cmd:
                try:
                    await asyncio.wait_for(cmd.done_event.wait(), timeout=_FINALIZE_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    pass  # genuinely still running, return partial output
            return self.get_output(offset, stderr_offset)

        # Non-streaming (REST) path: return immediately when the last command
        # is done so the caller gets completion status without blocking.
        # Also handles the "no commands yet" case via the streaming path below.
        # When there are no commands yet, always fall through to subscribe so
        # the caller waits for the first command (avoids spin before first exec).
        if not streaming and self.commands and self._is_command_done():
            return self.get_output(offset, stderr_offset)

        # Subscribe and wait for notification (new output or command done)
        event = asyncio.Event()
        sub_id = str(uuid.uuid4())
        self._subscribers[sub_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return self.get_output(offset, stderr_offset)
        finally:
            self._subscribers.pop(sub_id, None)

        # After waking up: if command is almost done, give it a moment
        if not self._is_command_done():
            cmd = self.current_command
            if cmd:
                try:
                    await asyncio.wait_for(cmd.done_event.wait(), timeout=_FINALIZE_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    pass  # still running, return what we have

        return self.get_output(offset, stderr_offset)

    async def write_stdin(self, data: str, command_id: Optional[str] = None) -> None:
        """Write raw data to the current command's stdin."""
        if self.status == SessionStatus.CLOSED:
            raise BadRequestException(f'Session {self.id} is closed')

        target = self.current_command
        if command_id:
            for cmd in reversed(self.commands):
                if cmd.id == command_id:
                    target = cmd
                    break

        if target and target.proc and target.proc.stdin and target.proc.returncode is None:
            target.proc.stdin.write(data.encode())
            await target.proc.stdin.drain()
            self.last_used_at = datetime.now()
        else:
            raise BadRequestException('No running process to write to')

    async def kill(self, sig: str = 'SIGTERM') -> None:
        """Send a signal to the current command's process group."""
        target = self.current_command
        if target and target.proc and target.proc.returncode is None:
            sig_num = getattr(signal, sig, signal.SIGTERM)
            try:
                # Send to the entire process group to kill child processes too.
                os.killpg(target.proc.pid, sig_num)
            except (ProcessLookupError, OSError):
                try:
                    target.proc.send_signal(sig_num)
                except ProcessLookupError:
                    pass

    async def close(self) -> None:
        """Close the session and clean up resources."""
        self.status = SessionStatus.CLOSED

        # Kill any running command process groups
        for cmd in self.commands:
            if cmd.proc and cmd.proc.returncode is None:
                try:
                    os.killpg(cmd.proc.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(cmd.proc.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        os.killpg(cmd.proc.pid, signal.SIGKILL)
                        await cmd.proc.wait()
                except (ProcessLookupError, OSError):
                    try:
                        cmd.proc.kill()
                        await cmd.proc.wait()
                    except ProcessLookupError:
                        pass

            if cmd.status == CommandStatus.RUNNING:
                cmd.status = CommandStatus.KILLED
                cmd.completed_at = datetime.now()
                cmd.done_event.set()

        self.current_command = None

        # Clean up snapshot files
        for path in (self.snapshot_env_path, self.snapshot_cwd_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as e:
                logger.warning(f'Failed to remove snapshot {path}: {e}')

        self._notify_subscribers()
        logger.info(f'Bash session {self.id} closed')

    def to_info(self) -> BashSessionInfo:
        """Convert to serializable session info."""
        return BashSessionInfo(
            session_id=self.id,
            status=self.status,
            working_dir=self.working_dir,
            created_at=self.created_at,
            last_used_at=self.last_used_at,
            current_command=(
                self.current_command.command if self.current_command else None
            ),
            command_count=len(self.commands),
        )


class PipeBashManager:
    """Manages multiple PipeBashSession instances.

    Handles session lifecycle, resource limits, and cleanup.
    """

    # Interval for background cleanup task (seconds)
    _CLEANUP_INTERVAL = 60

    def __init__(self):
        self.sessions: Dict[str, PipeBashSession] = {}
        self.max_sessions = get_env_int('MAX_BASH_SESSIONS', 50)
        self.session_timeout = get_env_int('BASH_SESSION_TIMEOUT', 3600)
        self._cleanup_task: Optional[asyncio.Task] = None

    async def create_session(
        self,
        session_id: Optional[str] = None,
        working_dir: Optional[str] = None,
        snapshot_path: Optional[str] = None,
    ) -> PipeBashSession:
        """Create a new bash session (lightweight — no process spawned)."""
        session_id = _normalize_optional_session_id(session_id)
        if session_id is None:
            session_id = str(uuid.uuid4())

        if session_id in self.sessions:
            existing = self.sessions[session_id]
            if existing.status != SessionStatus.CLOSED:
                return existing
            del self.sessions[session_id]

        await self._cleanup_expired_sessions()
        await self._enforce_session_limit()

        session = PipeBashSession(
            session_id=session_id,
            working_dir=working_dir,
            snapshot_path=snapshot_path,
        )
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> PipeBashSession:
        """Get an existing session by ID."""
        session = self.sessions.get(session_id)
        if session is None:
            raise ResourceNotFoundException(f'Session {session_id} not found')
        if session.status == SessionStatus.CLOSED:
            raise BadRequestException(f'Session {session_id} is closed')
        return session

    async def get_or_create_session(
        self,
        session_id: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> PipeBashSession:
        """Get existing session or create a new one.

        If the session already exists and working_dir is provided,
        update the session's default cwd so the next command runs
        in that directory.  Note: user ``cd`` inside commands does
        NOT update this — only API-level working_dir does.
        """
        session_id = _normalize_optional_session_id(session_id)
        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
            if session.status != SessionStatus.CLOSED:
                if working_dir:
                    session.working_dir = working_dir
                    session._write_cwd_snapshot(working_dir)
                return session

        return await self.create_session(
            session_id=session_id,
            working_dir=working_dir,
        )

    async def exec_command(
        self,
        session_id: Optional[str],
        command: str,
        working_dir: Optional[str] = None,
        async_mode: bool = False,
        timeout: Optional[float] = None,
        hard_timeout: Optional[float] = None,
        env: Optional[Dict[str, str]] = None,
        max_output_length: int = 0,
    ) -> BashExecResult:
        """Execute a command in a session. Creates session if needed."""
        session = await self.get_or_create_session(session_id, working_dir)

        cmd = await session.send_command(
            command,
            async_mode=async_mode,
            timeout=timeout,
            hard_timeout=hard_timeout,
            env=env,
        )

        # cmd.stdout_start/stderr_start are logical offsets — convert to
        # physical buffer indices so they remain valid after trimming.
        phys_out = max(0, cmd.stdout_start - session._stdout_trimmed)
        phys_err = max(0, cmd.stderr_start - session._stderr_trimmed)
        stdout_data = bytes(
            session.output_stream[phys_out:]
        ).decode('utf-8', errors='replace')
        stderr_data = bytes(
            session.stderr_stream[phys_err:]
        ).decode('utf-8', errors='replace')

        # Apply middle truncation for sync responses when limit is set
        if max_output_length > 0 and not async_mode:
            stdout_data = middle_truncate(stdout_data, max_output_length)
            stderr_data = middle_truncate(stderr_data, max_output_length)

        return BashExecResult(
            session_id=session.id,
            command_id=cmd.id,
            command=cmd.command,
            status=cmd.status,
            stdout=stdout_data if stdout_data else None,
            stderr=stderr_data if stderr_data else None,
            exit_code=cmd.exit_code,
            offset=session._stdout_trimmed + len(session.output_stream),
            stderr_offset=session._stderr_trimmed + len(session.stderr_stream),
        )

    async def get_output(
        self,
        session_id: str,
        offset: int = 0,
        stderr_offset: int = 0,
        wait: bool = False,
        wait_timeout: float = 30.0,
    ) -> BashOutputResult:
        """Get output from a session."""
        session = self.get_session(session_id)
        if wait:
            return await session.wait_for_output(offset, stderr_offset, wait_timeout)
        return session.get_output(offset, stderr_offset)

    async def write_stdin(
        self,
        session_id: str,
        input_text: str,
        command_id: Optional[str] = None,
    ) -> None:
        """Write to a session's current command stdin."""
        session = self.get_session(session_id)
        await session.write_stdin(input_text, command_id)

    async def kill_session(self, session_id: str, sig: str = 'SIGTERM') -> None:
        """Send a signal to a session's current command process."""
        session = self.get_session(session_id)
        await session.kill(sig)

    async def close_session(self, session_id: str) -> None:
        """Close and remove a session."""
        session = self.sessions.get(session_id)
        if session:
            await session.close()
            del self.sessions[session_id]

    def list_sessions(self) -> List[BashSessionInfo]:
        """List all active sessions."""
        return [
            s.to_info()
            for s in self.sessions.values()
            if s.status != SessionStatus.CLOSED
        ]

    async def _cleanup_expired_sessions(self) -> None:
        """Remove sessions that have been idle too long or already closed."""
        now = datetime.now()
        expired = [
            sid
            for sid, s in self.sessions.items()
            if (now - s.last_used_at).total_seconds() > self.session_timeout
            or s.status == SessionStatus.CLOSED
        ]
        for sid in expired:
            session = self.sessions.pop(sid, None)
            if session and session.status != SessionStatus.CLOSED:
                await session.close()
        if expired:
            logger.info(f'Cleaned up {len(expired)} expired bash sessions')

    async def _enforce_session_limit(self) -> None:
        """Evict the oldest session (by last_used_at) when at capacity."""
        while len(self.sessions) >= self.max_sessions:
            oldest_sid = min(
                self.sessions,
                key=lambda sid: self.sessions[sid].last_used_at,
            )
            session = self.sessions.pop(oldest_sid)
            if session.status != SessionStatus.CLOSED:
                await session.close()
            logger.info(
                f'Evicted oldest bash session {oldest_sid} to make room'
            )

    def start_cleanup_task(self) -> None:
        """Start the background periodic cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self._periodic_cleanup()
            )
            logger.info('Bash session cleanup task started')

    def stop_cleanup_task(self) -> None:
        """Stop the background periodic cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            self._cleanup_task = None
            logger.info('Bash session cleanup task stopped')

    async def _periodic_cleanup(self) -> None:
        """Background loop that cleans up expired sessions periodically."""
        while True:
            try:
                await asyncio.sleep(self._CLEANUP_INTERVAL)
                await self._cleanup_expired_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f'Error in bash session cleanup: {e}')

    async def cleanup_all_sessions(self) -> None:
        """Close all sessions. Called during application shutdown."""
        self.stop_cleanup_task()
        for session in list(self.sessions.values()):
            try:
                await session.close()
            except Exception as e:
                logger.warning(f'Error closing session {session.id}: {e}')
        self.sessions.clear()
        logger.info('All bash sessions cleaned up')
