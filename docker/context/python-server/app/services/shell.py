"""
OpenHands-based Shell Service Implementation
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional

from app.core.exceptions import BadRequestException
from app.core.env import get_env_int
from app.logging.decorators import trace_shell
from app.logging.sanitizer import sanitize_for_logging
from vendors.openhands.events.observation.commands import CmdOutputMetadata
from vendors.openhands.runtime.utils.bash import BashSession


if TYPE_CHECKING:
    from vendors.openhands.events.observation.commands import CmdOutputObservation


from app.models.shell import (
    ActiveShellSessionsResult,
    BashCommandStatus,
    ConsoleRecord,
    OpenHandsShellSession,
    ShellCommandResult,
    ShellSessionInfo,
    ShellViewResult,
)
from app.schemas.shell import ShellSessionStats


logger = logging.getLogger(__name__)


def _increment_active_commands(session: OpenHandsShellSession) -> None:
    session.active_command_count += 1


def _decrement_active_commands(session: OpenHandsShellSession) -> None:
    if session.active_command_count > 0:
        session.active_command_count -= 1


def _remove_command_echo(command_output: str, command: str) -> str:
    return command_output.lstrip().removeprefix(command.lstrip()).lstrip()


def _register_shell_command(
    session: OpenHandsShellSession,
    *,
    command_id: str,
    command: str,
    status: BashCommandStatus = BashCommandStatus.RUNNING,
) -> None:
    session.command_texts[command_id] = command
    session.command_statuses[command_id] = status
    session.command_exit_codes[command_id] = None
    session.command_outputs.setdefault(command_id, '')


def _set_shell_command_result(
    session: OpenHandsShellSession,
    *,
    command_id: str,
    status: BashCommandStatus,
    output: str,
    exit_code: int | None,
) -> None:
    session.command_statuses[command_id] = status
    session.command_outputs[command_id] = output
    session.command_exit_codes[command_id] = exit_code


def _cleanup_command_tracking(
    session: OpenHandsShellSession, command_id: str
) -> None:
    session.command_outputs.pop(command_id, None)
    session.command_statuses.pop(command_id, None)
    session.command_exit_codes.pop(command_id, None)
    session.command_texts.pop(command_id, None)


def _resolve_final_shell_output(
    *,
    previous_output: str,
    thread_output: str,
    live_output: str,
) -> str:
    """Prefer the most complete output snapshot when a command finishes.

    OpenHands completion output can lag behind or omit the earlier prompt/output
    that was already captured before a no-change timeout. Conversely, the final
    tmux pane snapshot can still be stale. Keep the longest complete-looking
    candidate and fall back to stitching the preserved prefix with the terminal
    completion output.
    """
    if live_output and len(live_output) >= len(thread_output):
        return live_output
    if not previous_output:
        return thread_output or live_output
    if not thread_output:
        return live_output or previous_output
    if thread_output.startswith(previous_output) or previous_output in thread_output:
        return thread_output
    return previous_output + thread_output


def _sync_session_working_dir(session: OpenHandsShellSession) -> str:
    """Refresh the session's tracked cwd from the live shell backend."""
    bash_session = session.bash_session
    if bash_session:
        current_cwd = getattr(bash_session, 'cwd', None)
        if current_cwd:
            session.working_dir = current_cwd
    return session.working_dir


def _summarize_shell_command_log(
    *,
    session_id: str,
    command: str,
    hidden: bool,
    status: BashCommandStatus | None = None,
    exit_code: int | None = None,
    output: str | None = None,
    hard_timeout: float | None = None,
    include_bulky_preview: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        'session_id': session_id,
        'command': sanitize_for_logging(
            command,
            field_name='command',
            include_bulky_preview=include_bulky_preview,
        ),
        'hidden': hidden,
    }
    if status is not None:
        payload['status'] = getattr(status, 'value', status)
    if exit_code is not None:
        payload['exit_code'] = exit_code
    if output is not None:
        payload['output'] = sanitize_for_logging(
            output,
            field_name='stdout',
            include_bulky_preview=include_bulky_preview,
        )
    if hard_timeout is not None:
        payload['hard_timeout'] = hard_timeout
    return payload


def _should_promote_shell_log(
    status: BashCommandStatus | None,
    exit_code: int | None,
) -> bool:
    normalized_status = getattr(status, 'value', status)
    return normalized_status not in (None, BashCommandStatus.COMPLETED.value) or (
        exit_code not in (None, 0)
    )


def strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape sequences from text"""
    # ANSI escape sequence pattern
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    # Remove ANSI codes
    clean_text = ansi_escape.sub('', text)
    # Remove backspace characters and handle them
    clean_text = re.sub(r'.\x08', '', clean_text)
    # Remove carriage returns that aren't followed by newlines
    clean_text = re.sub(r'\r(?!\n)', '', clean_text)
    return clean_text


def map_openhands_status(observation: 'CmdOutputObservation') -> BashCommandStatus:
    """Map OpenHands observation to our status enum

    Note: Only check metadata.suffix for timeout detection, NOT content.
    The suffix is added by OpenHands framework to indicate execution status,
    while content contains user command output which may contain "timeout"
    strings that would cause false positives.
    """
    metadata = getattr(observation, 'metadata', None)
    suffix = getattr(metadata, 'suffix', '') if metadata else ''
    suffix_lower = suffix.lower()

    # Only check suffix for timeout status - content may contain false positives
    if 'no new output' in suffix_lower:
        return BashCommandStatus.NO_CHANGE_TIMEOUT
    if 'timed out' in suffix_lower:
        return BashCommandStatus.HARD_TIMEOUT

    exit_code = getattr(observation, 'exit_code', None)

    if exit_code == 0:
        return BashCommandStatus.COMPLETED

    if exit_code == -1:
        terminated_markers = ['terminated', 'killed', 'not executed']
        if any(marker in suffix_lower for marker in terminated_markers):
            return BashCommandStatus.TERMINATED
        if getattr(observation, 'success', None) is False:
            return BashCommandStatus.TERMINATED
        return BashCommandStatus.TERMINATED

    if isinstance(exit_code, int):
        # 非零退出码仍然视为 completed (命令执行完成，只是有错误)
        return BashCommandStatus.COMPLETED

    return BashCommandStatus.COMPLETED


class OpenHandsShellManager:
    """基于 OpenHands BashSession 的 Shell 管理器"""

    # Default no_change_timeout for bash sessions (seconds)
    DEFAULT_NO_CHANGE_TIMEOUT = 120

    def __init__(self):
        self.sessions: Dict[str, OpenHandsShellSession] = {}
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

        # 会话管理配置
        self.max_sessions = get_env_int('MAX_SHELL_SESSIONS', 10)
        self.session_timeout = get_env_int('SHELL_SESSION_TIMEOUT', 3600)

        # Session pool for pre-warming
        from app.services.session_pool import SessionPool

        pool_size = get_env_int('SHELL_POOL_SIZE', 1)
        self._pool: SessionPool[OpenHandsShellSession] = SessionPool(
            name='shell',
            pool_size=pool_size,
            factory=self._create_pooled_session,
            cleanup=self._cleanup_session,
        )

        # Build version-based PATH env (PYTHON_VERSION / NODE_VERSION)
        from app.core.version import build_version_path_env

        self._version_env = build_version_path_env()
        if self._version_env:
            logger.info(
                f'Shell version PATH configured: {self._version_env.get("PATH", "")[:100]}...'
            )

        # Note: shell (tmux) injects env via `export` commands after session creation
        # because tmux sessions inherit the parent env implicitly.
        # For subprocess-based backends (bash/pipe), use build_sandbox_env() instead.

    def _get_lock(self) -> asyncio.Lock:
        """Return a manager lock bound to the current running loop."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def _create_pooled_session(self) -> OpenHandsShellSession:
        """Factory function for creating pooled sessions"""
        session_id = str(uuid.uuid4())
        working_dir = os.environ.get('WORKSPACE', '/tmp')

        bash_session = BashSession(
            work_dir=working_dir,
            username=os.environ.get('USER', 'gem'),
            no_change_timeout_seconds=self.DEFAULT_NO_CHANGE_TIMEOUT,
            preserve_symlinks=False,
        )

        # Initialize in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, bash_session.initialize)

        return OpenHandsShellSession(
            id=session_id,
            working_dir=working_dir,
            bash_session=bash_session,
        )

    def _cleanup_session(self, session: OpenHandsShellSession) -> None:
        """Cleanup function for pooled sessions"""
        if session.bash_session:
            try:
                session.bash_session.close()
            except Exception as e:
                logger.warning(f'Error closing pooled bash session: {e}')

    async def initialize_pool(self) -> None:
        """Initialize the session pool with pre-warmed sessions"""
        await self._pool.initialize()

    async def _cleanup_expired_sessions(self):
        """清理过期的会话"""
        current_time = time.time()
        expired_sessions = []
        lock = self._get_lock()

        async with lock:
            for session_id, session in self.sessions.items():
                # 检查会话是否过期 (使用 last_used_at 时间戳)
                last_used_timestamp = session.last_used_at.timestamp()
                if session.has_active_command:
                    continue
                if current_time - last_used_timestamp > self.session_timeout:
                    expired_sessions.append(session_id)

        # 在锁外删除过期会话以避免死锁
        for session_id in expired_sessions:
            await self._force_delete_session(session_id, reason='expired')
            logger.info(f'Cleaned up expired session {session_id}')

    async def _force_delete_session(self, session_id: str, reason: str = 'cleanup'):
        """强制删除会话（内部使用，不检查锁）"""
        lock = self._get_lock()
        async with lock:
            session = self.sessions.get(session_id)
            if not session:
                return False

            # 关闭 OpenHands session
            if session.bash_session:
                try:
                    session.bash_session.close()
                except Exception as e:
                    logger.warning(f'Error closing bash session during {reason}: {e}')

            # 清理订阅者
            session.char_subscribers.clear()
            session.line_subscribers.clear()

            # 从管理器中移除
            del self.sessions[session_id]
            session.active = False

            logger.info(f'Force deleted session {session_id} ({reason})')
            return True

    async def _enforce_session_limit(self):
        """强制会话数限制，移除最老的会话"""
        while True:
            oldest_session_id = None
            active_count = 0
            lock = self._get_lock()

            async with lock:
                if len(self.sessions) < self.max_sessions:
                    return

                evictable_session_ids = [
                    session_id
                    for session_id, session in self.sessions.items()
                    if not session.has_active_command
                ]
                active_count = len(self.sessions) - len(evictable_session_ids)
                if evictable_session_ids:
                    # 只淘汰没有活动命令的最老会话
                    oldest_session_id = min(
                        evictable_session_ids,
                        key=lambda k: self.sessions[k].last_used_at,
                    )

            # 在锁外删除以避免死锁
            if oldest_session_id:
                await self._force_delete_session(
                    oldest_session_id, reason='evicted for limit'
                )
                logger.info(
                    f'Evicted oldest idle session {oldest_session_id} to make room'
                )
                continue

            raise BadRequestException(
                'Cannot create shell session: session limit reached and all '
                f'{active_count} existing sessions have active commands'
            )

    async def create_session(
        self,
        session_id: Optional[str] = None,
        working_dir: Optional[str] = None,
        shell: str = '/bin/bash',
        no_change_timeout: Optional[int] = None,
        preserve_symlinks: bool = False,
    ) -> OpenHandsShellSession:
        """创建新的 shell 会话

        Args:
            session_id: Optional session ID, auto-generated if not provided
            working_dir: Working directory for the session
            shell: Shell executable path
            preserve_symlinks: If True, preserve symlinks in working directory path.
                              If False, symlinks are resolved to physical paths.
                              Defaults to False for backward compatibility.
        """

        # 清理过期会话
        await self._cleanup_expired_sessions()

        # 强制会话数限制
        await self._enforce_session_limit()

        if session_id is None:
            session_id = str(uuid.uuid4())

        if working_dir is None:
            working_dir = os.environ.get('WORKSPACE', '/tmp')

        # Version-based PATH env from PYTHON_VERSION / NODE_VERSION
        merged_env = self._version_env

        # Use provided no_change_timeout or default
        effective_no_change_timeout = (
            no_change_timeout
            if no_change_timeout is not None
            else self.DEFAULT_NO_CHANGE_TIMEOUT
        )

        # Resolve symlinks if preserve_symlinks is False
        effective_working_dir = working_dir
        if working_dir and not preserve_symlinks:
            effective_working_dir = os.path.realpath(working_dir)

        try:
            # Try to get a pre-warmed session from the pool
            # PS1 now includes both working_dir and working_dir_symlink,
            # so preserve_symlinks can be changed at runtime without modifying PS1
            pooled_wait_timeout = 0.5 if self._pool.is_initialized else None
            pooled_session = await self._pool.acquire(wait_timeout=pooled_wait_timeout)

            if pooled_session:
                # Reconfigure the pooled session with requested parameters
                # Treat pooled sessions as newly created sessions from the API user's
                # perspective: reset timestamps so age_seconds reflects time since
                # allocation, not time since pool pre-warm.
                now = datetime.now()
                pooled_session.id = session_id
                pooled_session.created_at = now
                pooled_session.last_used_at = now

                if pooled_session.bash_session:
                    pooled_session.bash_session.NO_CHANGE_TIMEOUT_SECONDS = (
                        effective_no_change_timeout
                    )
                    # preserve_symlinks is now a runtime config, no PS1 change needed
                    pooled_session.bash_session.preserve_symlinks = preserve_symlinks

                    # If working_dir is specified and different from pool's default,
                    # we need to actually cd to that directory
                    pool_cwd = pooled_session.bash_session._cwd

                    if effective_working_dir and effective_working_dir != pool_cwd:
                        # Execute cd command to change to the requested directory
                        cd_cmd = f'cd {shlex.quote(effective_working_dir)}'
                        from vendors.openhands.events.action.commands import CmdRunAction

                        action = CmdRunAction(command=cd_cmd, hidden=True)
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, pooled_session.bash_session.execute, action
                        )
                        pooled_session.working_dir = effective_working_dir
                        pooled_session.bash_session.work_dir = effective_working_dir
                    else:
                        # Keep pool's default working_dir
                        pooled_session.working_dir = pool_cwd

                    # Inject environment variables via execute() for reliable ordering
                    if merged_env:
                        export_cmd = 'export ' + ' '.join(
                            f'{k}={shlex.quote(v)}'
                            for k, v in merged_env.items()
                        )
                        from vendors.openhands.events.action.commands import (
                            CmdRunAction,
                        )

                        action = CmdRunAction(command=export_cmd, hidden=True)
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, pooled_session.bash_session.execute, action
                        )

                async with self._get_lock():
                    self.sessions[session_id] = pooled_session

                logger.info(
                    f'Created shell session from pool: {session_id} '
                    f'(total sessions: {len(self.sessions)})'
                )
                return pooled_session

            # Fallback: create session synchronously if pool is empty
            bash_session = BashSession(
                work_dir=effective_working_dir,
                username=os.environ.get('USER', 'gem'),
                no_change_timeout_seconds=effective_no_change_timeout,
                preserve_symlinks=preserve_symlinks,
            )

            # 初始化 bash session（线程池中执行，避免阻塞事件循环）
            await asyncio.to_thread(bash_session.initialize)

            # Inject version-based PATH via execute() for reliable ordering
            if merged_env:
                export_cmd = 'export ' + ' '.join(
                    f'{k}={shlex.quote(v)}' for k, v in merged_env.items()
                )
                from vendors.openhands.events.action.commands import CmdRunAction

                action = CmdRunAction(command=export_cmd, hidden=True)
                await asyncio.to_thread(bash_session.execute, action)

            # 创建我们的会话包装器
            session = OpenHandsShellSession(
                id=session_id,
                working_dir=effective_working_dir,
                bash_session=bash_session,
            )

            async with self._get_lock():
                self.sessions[session_id] = session

            logger.info(
                f'Created OpenHands shell session: {session_id} (total sessions: {len(self.sessions)}, preserve_symlinks: {preserve_symlinks})'
            )
            return session

        except Exception as e:
            logger.error(f'Failed to create session: {e}')
            raise

    @trace_shell()
    async def execute_command(
        self,
        session_id: str,
        command: str,
        timeout: int = 30,
        async_mode: bool = False,
        working_dir: Optional[str] = None,
        strict: Optional[bool] = None,
        no_change_timeout: Optional[int] = None,
        hidden: bool = False,
        hard_timeout: Optional[float] = None,
    ) -> ShellCommandResult:
        """执行命令"""

        session = self.get_session(session_id)
        if not session:
            raise ValueError(f'Session {session_id} not found')

        if not session.active:
            raise ValueError(f'Session {session_id} is not active')

        session.last_used_at = datetime.now()
        command_id = str(uuid.uuid4())
        _register_shell_command(
            session,
            command_id=command_id,
            command=command,
        )

        command_to_run = command

        if working_dir:
            # Resolve symlinks if session's preserve_symlinks is False
            preserve_symlinks = (
                session.bash_session.preserve_symlinks
                if session.bash_session
                else False
            )
            if preserve_symlinks:
                desired_dir = os.path.abspath(working_dir)
            else:
                desired_dir = os.path.realpath(working_dir)

            if os.path.isdir(desired_dir):
                # Compare using realpath for consistent comparison
                current_dir = (
                    os.path.realpath(session.working_dir)
                    if session.working_dir
                    else None
                )
                if current_dir != os.path.realpath(desired_dir):
                    command_to_run = f'cd {shlex.quote(desired_dir)} && {command}'
                session.working_dir = desired_dir
                if session.bash_session:
                    session.bash_session.work_dir = desired_dir
            else:
                # Directory does not exist
                if strict:
                    # strict mode: return error result without raising exception
                    session.status = BashCommandStatus.TERMINATED
                    session.last_command = command
                    session.last_command_id = command_id
                    session.current_command = None
                    session.current_command_id = None
                    session.exit_code = 1
                    error_msg = f'{working_dir} No such file or directory'
                    logger.warning(
                        'strict mode: working directory does not exist: %s',
                        working_dir,
                    )
                    _set_shell_command_result(
                        session,
                        command_id=command_id,
                        status=BashCommandStatus.TERMINATED,
                        output=error_msg,
                        exit_code=1,
                    )
                    console_record = ConsoleRecord(
                        ps1='$ ',  # 简化处理
                        command=command,
                        output=error_msg,
                    )
                    session.console_records.append(console_record)
                    _cleanup_command_tracking(session, command_id)
                    return ShellCommandResult(
                        session_id=session_id,
                        command=command,
                        status=BashCommandStatus.TERMINATED,
                        output=error_msg,
                        exit_code=1,
                        console=[console_record],
                    )
                else:
                    # non-strict mode: silent fallback to session's working directory
                    logger.warning(
                        'Requested working directory does not exist: %s, '
                        'falling back to session working directory: %s',
                        working_dir,
                        session.working_dir,
                    )

        # Apply per-command no_change_timeout if provided
        original_no_change_timeout = None
        if no_change_timeout is not None and session.bash_session:
            original_no_change_timeout = session.bash_session.NO_CHANGE_TIMEOUT_SECONDS
            session.bash_session.NO_CHANGE_TIMEOUT_SECONDS = no_change_timeout

        try:
            if async_mode:
                # 异步模式：立即返回，不等待结果
                # Note: For async mode, the no_change_timeout stays until
                # the command completes or update_session is called
                _increment_active_commands(session)
                try:
                    asyncio.create_task(
                        self._execute_command_async(
                            session,
                            command_to_run,
                            timeout,
                            command_id=command_id,
                            display_command=command,
                            original_no_change_timeout=original_no_change_timeout,
                            hidden=hidden,
                            hard_timeout=hard_timeout,
                        )
                    )
                except Exception:
                    _decrement_active_commands(session)
                    raise

                return ShellCommandResult(
                    session_id=session_id,
                    command=command,
                    status=BashCommandStatus.RUNNING,
                    output=None,
                    console=None,
                )
            else:
                # 同步模式：等待执行完成
                _increment_active_commands(session)
                try:
                    result = await self._execute_command_sync(
                        session,
                        command_to_run,
                        timeout,
                        command_id=command_id,
                        display_command=command,
                        hidden=hidden,
                        hard_timeout=hard_timeout,
                    )
                    if result.status == BashCommandStatus.NO_CHANGE_TIMEOUT:
                        session.status = BashCommandStatus.RUNNING
                        session.exit_code = None
                        _set_shell_command_result(
                            session,
                            command_id=command_id,
                            status=BashCommandStatus.RUNNING,
                            output=result.output or '',
                            exit_code=None,
                        )
                        _increment_active_commands(session)
                        try:
                            asyncio.create_task(
                                self._execute_command_async(
                                    session,
                                    '',
                                    timeout,
                                    command_id=command_id,
                                    display_command=command,
                                    original_no_change_timeout=(
                                        original_no_change_timeout
                                    ),
                                    hidden=hidden,
                                    hard_timeout=hard_timeout,
                                )
                            )
                            original_no_change_timeout = None
                        except Exception:
                            _decrement_active_commands(session)
                            raise
                    return result
                finally:
                    _decrement_active_commands(session)

        except Exception as e:
            session.status = BashCommandStatus.TERMINATED
            session.command_statuses[command_id] = BashCommandStatus.TERMINATED
            logger.error(f'Command execution failed: {e}')
            raise
        finally:
            # Restore original no_change_timeout for sync mode only
            # For async mode, the timeout is restored in _execute_command_async
            if not async_mode and original_no_change_timeout is not None:
                if session.bash_session:
                    session.bash_session.NO_CHANGE_TIMEOUT_SECONDS = (
                        original_no_change_timeout
                    )

    async def _execute_command_sync(
        self,
        session: OpenHandsShellSession,
        command: str,
        timeout: int,
        command_id: str,
        display_command: Optional[str] = None,
        hidden: bool = False,
        hard_timeout: Optional[float] = None,
    ) -> ShellCommandResult:
        """同步执行命令（实际上在线程池中执行以避免阻塞）"""

        async with session.get_exec_lock():
            # 在线程池中执行以避免阻塞事件循环
            from vendors.openhands.events.action.commands import CmdRunAction

            visible_command = display_command or command
            session.current_command = visible_command
            session.current_command_id = command_id
            session.last_command = visible_command
            session.last_command_id = command_id
            session.status = BashCommandStatus.RUNNING
            session.exit_code = None

            loop = asyncio.get_event_loop()
            keep_session_attached = False

            try:
                def _run_command():
                    """在线程池中同步执行命令"""
                    try:
                        # 创建 OpenHands CmdRunAction
                        action = CmdRunAction(command=command, hidden=hidden)
                        if hard_timeout is not None:
                            action.set_hard_timeout(hard_timeout)

                        # 执行命令
                        observation = session.bash_session.execute(action)
                        logger.debug(session.bash_session)
                        logger.debug(observation)

                        # 处理结果
                        status = map_openhands_status(observation)

                        # 提取输出
                        output = getattr(observation, 'content', '')
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug(
                                'Raw command output (first 500 chars): %s', output[:500]
                            )
                        # 清理输出
                        clean_output = strip_ansi_codes(output)

                        return status, clean_output, getattr(observation, 'exit_code', -1)

                    except Exception as e:
                        logger.error(
                            f'Command execution error in sync mode: {e}', exc_info=True
                        )
                        return (
                            BashCommandStatus.TERMINATED,
                            f'Command failed: {str(e)}',
                            -1,
                        )

                # 在线程池中执行命令
                status, clean_output, exit_code = await loop.run_in_executor(
                    None, _run_command
                )
                log_level = (
                    logging.ERROR
                    if _should_promote_shell_log(status, exit_code)
                    else logging.INFO
                )
                logger.log(
                    log_level,
                    'Shell command completed: %s',
                    _summarize_shell_command_log(
                        session_id=session.id,
                        command=visible_command,
                        hidden=hidden,
                        status=status,
                        exit_code=exit_code,
                        output=clean_output,
                        hard_timeout=hard_timeout,
                        include_bulky_preview=log_level >= logging.ERROR,
                    ),
                )

                # 更新会话状态
                _sync_session_working_dir(session)
                session.status = status
                session.exit_code = exit_code
                session.last_used_at = datetime.now()
                _set_shell_command_result(
                    session,
                    command_id=command_id,
                    status=status,
                    output=clean_output,
                    exit_code=exit_code,
                )

                console_record = ConsoleRecord(
                    ps1='$ ',  # 简化处理
                    command=visible_command,
                    output=clean_output,
                )
                if status == BashCommandStatus.NO_CHANGE_TIMEOUT:
                    keep_session_attached = True
                else:
                    # 添加到会话缓冲区
                    await session.add_output(clean_output)
                    # 创建控制台记录
                    session.console_records.append(console_record)
                    _cleanup_command_tracking(session, command_id)

                return ShellCommandResult(
                    session_id=session.id,
                    command=visible_command,
                    status=status,
                    output=clean_output,
                    exit_code=exit_code,
                    console=[console_record],
                )
            finally:
                if not keep_session_attached:
                    session.current_command = None
                    session.current_command_id = None

    async def _execute_command_async(
        self,
        session: OpenHandsShellSession,
        command: str,
        timeout: int,
        command_id: str,
        display_command: Optional[str] = None,
        original_no_change_timeout: Optional[int] = None,
        hidden: bool = False,
        hard_timeout: Optional[float] = None,
    ):
        """异步执行命令（后台执行）"""
        async with session.get_exec_lock():
            from vendors.openhands.events.action.commands import CmdRunAction

            visible_command = display_command or command

            logger.info(
                'Shell async command started: %s',
                _summarize_shell_command_log(
                    session_id=session.id,
                    command=visible_command,
                    hidden=hidden,
                    hard_timeout=hard_timeout,
                ),
            )

            try:
                # 检查会话是否还在管理器中
                if session.id not in self.sessions:
                    logger.error(
                        f'Session {session.id} not found in manager at start of async execution'
                    )
                    return

                session.current_command = visible_command
                session.current_command_id = command_id
                session.last_command = visible_command
                session.last_command_id = command_id
                session.status = BashCommandStatus.RUNNING
                session.exit_code = None

                # 在线程池中执行OpenHands命令，避免阻塞
                loop = asyncio.get_event_loop()

                def _run_command(
                    command_text: str,
                    hard_timeout_override: Optional[float],
                ):
                    """在线程池中同步执行命令"""
                    try:
                        # 创建 OpenHands CmdRunAction
                        action = CmdRunAction(command=command_text, hidden=hidden)
                        if hard_timeout_override is not None:
                            action.set_hard_timeout(hard_timeout_override)

                        # 执行命令
                        observation = session.bash_session.execute(action)

                        # 处理结果
                        status = map_openhands_status(observation)

                        # 提取输出
                        output = getattr(observation, 'content', '')

                        # 清理输出
                        clean_output = strip_ansi_codes(output)
                        return status, clean_output, getattr(observation, 'exit_code', -1)

                    except Exception as e:
                        logger.error(
                            f'Command execution error in thread: {e}', exc_info=True
                        )
                        return BashCommandStatus.TERMINATED, f'Command failed: {str(e)}', -1

                started_at = time.monotonic()
                next_command = command
                while True:
                    remaining_hard_timeout = None
                    if hard_timeout is not None:
                        elapsed = time.monotonic() - started_at
                        remaining_hard_timeout = max(hard_timeout - elapsed, 0.001)

                    # 在线程池中执行命令
                    status, output, exit_code = await loop.run_in_executor(
                        None,
                        _run_command,
                        next_command,
                        remaining_hard_timeout,
                    )

                    # 检查会话是否还在管理器中
                    if session.id not in self.sessions:
                        logger.error(
                            f'Session {session.id} disappeared after thread execution!'
                        )
                        return

                    if status == BashCommandStatus.NO_CHANGE_TIMEOUT:
                        await self._refresh_live_output(session, command_id)
                        async with session.get_lock():
                            live_output = session.command_outputs.get(command_id, '')
                            session.status = BashCommandStatus.RUNNING
                            session.exit_code = None
                            session.last_used_at = datetime.now()
                            _set_shell_command_result(
                                session,
                                command_id=command_id,
                                status=BashCommandStatus.RUNNING,
                                output=live_output,
                                exit_code=None,
                            )
                        next_command = ''
                        continue
                    break

                async with session.get_lock():
                    previous_output = session.command_outputs.get(command_id, '')
                await self._refresh_live_output(session, command_id)
                async with session.get_lock():
                    live_output = session.command_outputs.get(command_id, '')
                final_output = (
                    _resolve_final_shell_output(
                        previous_output=previous_output,
                        thread_output=output,
                        live_output=live_output,
                    )
                    if status == BashCommandStatus.COMPLETED
                    else output
                )
                log_level = (
                    logging.ERROR
                    if _should_promote_shell_log(status, exit_code)
                    else logging.INFO
                )
                logger.log(
                    log_level,
                    'Shell async command completed: %s',
                    _summarize_shell_command_log(
                        session_id=session.id,
                        command=visible_command,
                        hidden=hidden,
                        status=status,
                        exit_code=exit_code,
                        output=final_output,
                        hard_timeout=hard_timeout,
                        include_bulky_preview=log_level >= logging.ERROR,
                    ),
                )

                # 更新会话状态
                _sync_session_working_dir(session)
                session.status = status
                session.exit_code = exit_code
                session.last_used_at = datetime.now()
                _set_shell_command_result(
                    session,
                    command_id=command_id,
                    status=status,
                    output=final_output,
                    exit_code=exit_code,
                )

                # 添加输出到缓冲区（这会通知订阅者）
                await session.add_output(final_output)

                # 创建控制台记录
                console_record = ConsoleRecord(
                    ps1='$ ',
                    command=visible_command,
                    output=final_output,
                )
                session.console_records.append(console_record)
                _cleanup_command_tracking(session, command_id)

            except asyncio.CancelledError:
                logger.warning(f'Async execution was cancelled for session {session.id}')
                # Don't set status or add output for cancelled tasks
                raise
            except Exception as e:
                logger.error(
                    f'Exception in async execution for session {session.id}: {e}',
                    exc_info=True,
                )
                # Check if session still exists before updating it
                if session.id in self.sessions:
                    session.status = BashCommandStatus.TERMINATED
                    error_msg = f'Command failed: {str(e)}'
                    _set_shell_command_result(
                        session,
                        command_id=command_id,
                        status=BashCommandStatus.TERMINATED,
                        output=error_msg,
                        exit_code=-1,
                    )
                    await session.add_output(error_msg)
                    _cleanup_command_tracking(session, command_id)
                logger.error(f'Async command failed: {e}')
            finally:
                session.current_command = None
                session.current_command_id = None
                # Restore original no_change_timeout after async command completes
                if original_no_change_timeout is not None and session.bash_session:
                    session.bash_session.NO_CHANGE_TIMEOUT_SECONDS = (
                        original_no_change_timeout
                    )
                _decrement_active_commands(session)

    async def view_session(
        self,
        session_id: str,
        limit: int = 1000,
    ) -> ShellViewResult:
        """查看会话输出"""

        session = self.get_session(session_id)
        if not session:
            raise ValueError(f'Session {session_id} not found')

        async with session.get_lock():
            recent_output = (
                session.output_buffer[-limit:] if session.output_buffer else []
            )
            output = ''.join(recent_output)

        return ShellViewResult(
            output=output,
            session_id=session_id,
            console=session.console_records,
            status=session.status,
            command=session.current_command or session.last_command,
            exit_code=session.exit_code,
        )

    def _extract_live_output_from_pane(
        self,
        session: OpenHandsShellSession,
        command_id: str,
    ) -> str:
        if not session.bash_session or session.current_command_id != command_id:
            return session.command_outputs.get(command_id, '')

        pane_content = session.bash_session._get_pane_content()
        ps1_matches = CmdOutputMetadata.matches_ps1_metadata(pane_content)
        raw_output = session.bash_session._combine_outputs_between_matches(
            pane_content,
            ps1_matches,
        )
        command = session.command_texts.get(command_id, '')
        live_output = strip_ansi_codes(_remove_command_echo(raw_output, command))
        previous_output = session.command_outputs.get(command_id, '')
        if previous_output and len(live_output) < len(previous_output):
            return previous_output
        return live_output

    async def _refresh_live_output(
        self,
        session: OpenHandsShellSession,
        command_id: str,
    ) -> None:
        live_output = await asyncio.to_thread(
            self._extract_live_output_from_pane,
            session,
            command_id,
        )
        async with session.get_lock():
            previous_output = session.command_outputs.get(command_id, '')
            if len(live_output) >= len(previous_output):
                session.command_outputs[command_id] = live_output

    async def subscribe_char(
        self, session_id: str, subscriber_id: str
    ) -> Optional[asyncio.Queue]:
        """订阅字符输出"""

        session = self.get_session(session_id)
        if not session:
            return None

        return await session.subscribe_char(subscriber_id)

    async def unsubscribe_char(self, session_id: str, subscriber_id: str):
        """取消字符订阅"""

        session = self.get_session(session_id)
        if session:
            await session.unsubscribe_char(subscriber_id)

    async def delete_session(self, session_id: str) -> bool:
        """删除会话"""

        async with self._get_lock():
            session = self.sessions.get(session_id)
            if not session:
                return False

            # 关闭 OpenHands session
            if session.bash_session:
                try:
                    session.bash_session.close()
                except Exception as e:
                    logger.warning(f'Error closing bash session: {e}')

            # 清理订阅者
            session.char_subscribers.clear()
            session.line_subscribers.clear()

            # 从管理器中移除
            del self.sessions[session_id]
            session.active = False

            logger.info(f'Deleted session: {session_id}')
            return True

    async def update_session(
        self,
        session_id: str,
        no_change_timeout: Optional[int] = None,
    ) -> Optional[OpenHandsShellSession]:
        """Update session configuration"""
        session = self.get_session(session_id)
        if not session:
            return None

        if no_change_timeout is not None and session.bash_session:
            session.bash_session.NO_CHANGE_TIMEOUT_SECONDS = no_change_timeout

        session.last_used_at = datetime.now()
        logger.info(
            f'Updated session {session_id}: no_change_timeout={no_change_timeout}'
        )
        return session

    def get_session(self, session_id: str) -> Optional[OpenHandsShellSession]:
        """获取会话"""
        return self.sessions.get(session_id)

    def list_sessions(self) -> List[OpenHandsShellSession]:
        """列出所有会话"""
        return list(self.sessions.values())

    def get_session_stats(self) -> ShellSessionStats:
        """获取会话统计信息"""
        current_time = time.time()
        active_count = 0
        idle_count = 0

        for session in self.sessions.values():
            last_used_timestamp = session.last_used_at.timestamp()
            if session.has_active_command or current_time - last_used_timestamp < 300:
                # 活动命令中的 session 始终视为活跃
                active_count += 1
            else:
                idle_count += 1

        return ShellSessionStats(
            total_sessions=len(self.sessions),
            active_sessions=active_count,
            idle_sessions=idle_count,
            max_sessions=self.max_sessions,
            session_timeout=self.session_timeout,
            usage_ratio=(
                len(self.sessions) / self.max_sessions if self.max_sessions > 0 else 0.0
            ),
        )

    async def cleanup_all_sessions(self):
        """清理所有活跃会话（用于关闭或重启）

        Note: 池化会话由 SessionPoolManager 统一管理，不在此处清理
        """
        session_ids = list(self.sessions.keys())
        for session_id in session_ids:
            await self._force_delete_session(session_id, reason='cleanup_all')

        logger.info(f'Cleaned up all {len(session_ids)} active sessions')

    def get_active_sessions(self) -> ActiveShellSessionsResult:
        """Get info about active shell sessions"""
        # 清理过期会话 (如果有事件循环的话)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._cleanup_expired_sessions())
        except RuntimeError:
            # 没有运行的事件循环，跳过清理
            pass

        sessions = {}
        current_time = time.time()

        for session_id, session in self.sessions.items():
            sessions[session_id] = ShellSessionInfo(
                working_dir=session.working_dir,
                created_at=session.created_at,
                last_used_at=session.last_used_at,
                age_seconds=int(current_time - session.last_used_at.timestamp()),
                status=session.status.value,
                current_command=session.current_command,
            )

        return ActiveShellSessionsResult(sessions=sessions)

    def cleanup_session(self, session_id: str) -> bool:
        """Manually cleanup a specific shell session"""
        if session_id in self.sessions:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._force_delete_session(session_id, reason='manual_cleanup')
                )
            except RuntimeError:
                pass
            return True
        return False
