import asyncio
import logging
import os
import tempfile
import time
import uuid
from functools import cached_property
from typing import Any, Dict, List, Optional

from jupyter_client import BlockingKernelClient, kernelspec
from jupyter_client.manager import KernelManager, start_new_kernel

from app.core.env import get_env_int
from app.models.jupyter import (
    ActiveSessionsResult,
    ExecuteCodeResult,
    JupyterOutput,
    KernelStatus,
    OutputType,
    SessionInfo,
)
from app.logging.decorators import trace_jupyter
from app.logging.execution import log_execution_event


logger = logging.getLogger(__name__)


def _is_true_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    trimmed = value.strip()
    if not trimmed:
        return default
    return trimmed.lower() == 'true'


# Kernel name mapping: user-friendly name -> actual kernel name
# Note: JavaScript/TypeScript execution is handled by NodeJSService REPL, not Jupyter
KERNEL_ALIASES: Dict[str, str] = {
    # Python kernels
    'python': 'python3',
    'python3': 'python3',
    'python3.10': 'python3.10',
    'python3.11': 'python3.11',
    'python3.12': 'python3.12',
}

# Fallback chains for each kernel type (matching original behavior)
KERNEL_FALLBACKS: Dict[str, List[str]] = {
    'python3': ['python3', 'python', 'python3.11', 'python3.10', 'python3.12'],
    'python': ['python', 'python3', 'python3.11', 'python3.10', 'python3.12'],
    'python3.10': ['python3.10', 'python3', 'python'],
    'python3.11': ['python3.11', 'python3', 'python'],
    'python3.12': ['python3.12', 'python3', 'python'],
}


class KernelSession:
    def __init__(
        self,
        kernel_manager: KernelManager,
        kernel_client: BlockingKernelClient,
        kernel_name: str,
        session_id: Optional[str] = None,
    ):
        self.kernel_manager = kernel_manager
        self.kernel_client = kernel_client
        self.kernel_name = kernel_name
        self.last_used = time.time()
        self.session_id = session_id or str(uuid.uuid4())
        # 执行锁：防止同一 kernel 上的并发代码执行
        self._exec_lock = asyncio.Lock()

    def update_last_used(self):
        self.last_used = time.time()

    def cleanup(self):
        try:
            self.kernel_client.stop_channels()
            self.kernel_manager.shutdown_kernel(now=True)
        except Exception as e:
            logger.debug(f'Error cleaning up kernel session {self.session_id}: {e}')


class JupyterService:
    def __init__(self):
        # Session management
        self.sessions: Dict[str, KernelSession] = {}
        self.session_timeout = 1800  # 30 minutes
        self.max_sessions = 20
        self._large_code_staging_enabled = _is_true_env(
            'JUPYTER_LARGE_CODE_STAGING_ENABLED'
        )
        self._inline_code_max_bytes = get_env_int(
            'JUPYTER_INLINE_CODE_MAX_BYTES',
            128 * 1024,
            min_value=1,
        )
        staged_code_dir = os.environ.get('JUPYTER_STAGED_CODE_DIR', '').strip()
        self._staged_code_dir = staged_code_dir or '/tmp/aio-jupyter-code'

        # Default kernel to pre-warm (PYTHON_VERSION > PYTHON_CODE_EXEC_VERSION > python3.10)
        from app.core.version import resolve_python_version

        self._default_kernel = resolve_python_version()

        # Kernel pool for pre-warming (only for default kernel)
        from app.services.session_pool import SessionPool

        pool_size = get_env_int('JUPYTER_POOL_SIZE', 1)
        self._pool: SessionPool[KernelSession] = SessionPool(
            name='jupyter',
            pool_size=pool_size,
            factory=self._create_pooled_session,
            cleanup=lambda s: s.cleanup(),
        )

    @staticmethod
    def _get_shell_reply(
        kc: BlockingKernelClient,
        msg_id: str,
        timeout: float = 5.0,
    ) -> Optional[dict]:
        # Get shell reply matching msg_id; discard stale messages from prior executes.
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                reply = kc.get_shell_msg(timeout=remaining)
                if reply.get('parent_header', {}).get('msg_id') == msg_id:
                    return reply
                # Stale message from a previous execute — discard and retry
            except Exception:
                return None

    @staticmethod
    def _chdir_kernel(kc: BlockingKernelClient, target_cwd: str) -> None:
        # Change working directory of a kernel; raises RuntimeError on failure.
        chdir_code = 'import os\nos.chdir(' + repr(target_cwd) + ')'
        msg_id = kc.execute(chdir_code, silent=True, store_history=False)
        reply = JupyterService._get_shell_reply(kc, msg_id, timeout=5)
        if reply and reply['content'].get('status') == 'error':
            ename = reply['content'].get('ename', 'Error')
            evalue = reply['content'].get('evalue', 'unknown')
            raise RuntimeError(
                f'Kernel chdir to {target_cwd!r} failed: {ename}: {evalue}'
            )

    @staticmethod
    def _ensure_user_site_in_path(kc: BlockingKernelClient) -> None:
        """Ensure user site-packages is in kernel's sys.path.

        Problem: Jupyter kernel's sys.path may not include the user site-packages
        directory (e.g. /home/user/.local/lib/python3.12/site-packages), but pip
        falls back to user install when system site-packages is not writable.
        This causes "pip install foo" to succeed but "import foo" to fail.

        Fix: inject user site-packages into sys.path at kernel startup.
        """
        code = (
            'import site, sys; '
            '_user_site = site.getusersitepackages(); '
            'sys.path.insert(0, _user_site) if _user_site not in sys.path else None; '
            'del _user_site'
        )
        msg_id = kc.execute(code, silent=True, store_history=False)
        JupyterService._get_shell_reply(kc, msg_id, timeout=5)

    async def _create_pooled_session(self) -> KernelSession:
        """Factory function for creating pooled kernel sessions"""
        actual_kernel_name = self._get_best_kernel(self._default_kernel)
        from app.utils import get_workspace

        def _create():
            km, kc = start_new_kernel(kernel_name=actual_kernel_name, cwd=get_workspace())
            kc.wait_for_ready(10)
            JupyterService._ensure_user_site_in_path(kc)
            return KernelSession(km, kc, actual_kernel_name)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _create)

    async def initialize_pool(self) -> None:
        """Initialize the kernel pool with pre-warmed sessions"""
        await self._pool.initialize()

    @cached_property
    def available_kernels(self) -> list:
        try:
            kernel_specs = list(kernelspec.find_kernel_specs().keys())
            logger.info(f'Available Jupyter kernels: {kernel_specs}')
            return kernel_specs
        except Exception as e:
            logger.error(f'Failed to get kernel specs: {e}')
            return []

    def _get_best_kernel(self, requested_kernel: str) -> str:
        """Find the best available kernel name with multi-language support"""
        if not self.available_kernels:
            logger.warning('No kernels available, trying default names')
            return requested_kernel

        # Resolve alias first (e.g., 'python' -> 'python3')
        resolved_kernel = KERNEL_ALIASES.get(requested_kernel, requested_kernel)

        if resolved_kernel in self.available_kernels:
            if resolved_kernel != requested_kernel:
                logger.info(
                    f"Resolved kernel alias '{requested_kernel}' to '{resolved_kernel}'"
                )
            return resolved_kernel

        # Try fallback chain for this kernel type
        fallbacks = KERNEL_FALLBACKS.get(resolved_kernel, [resolved_kernel])
        for alt_kernel in fallbacks:
            if alt_kernel in self.available_kernels:
                logger.info(
                    f"Using kernel '{alt_kernel}' instead of '{requested_kernel}'"
                )
                return alt_kernel

        # If no alternatives found, use the first available kernel
        if self.available_kernels:
            fallback = self.available_kernels[0]
            logger.warning(
                f"Kernel '{requested_kernel}' not found, using fallback '{fallback}'"
            )
            return fallback

        # Last resort
        return requested_kernel

    def _cleanup_expired_sessions(self):
        """Remove expired sessions"""
        current_time = time.time()
        expired_sessions = []

        for session_id, session in self.sessions.items():
            if current_time - session.last_used > self.session_timeout:
                expired_sessions.append(session_id)

        for session_id in expired_sessions:
            session = self.sessions.pop(session_id)
            session.cleanup()
            logger.info(f'Cleaned up expired session {session_id}')

    def _prepare_code_for_execution(
        self, code: str
    ) -> tuple[str, bool, Optional[str]]:
        """Optionally stage oversized code to a temp file before execution."""
        if not self._large_code_staging_enabled:
            return code, True, None

        if len(code.encode('utf-8')) <= self._inline_code_max_bytes:
            return code, True, None

        os.makedirs(self._staged_code_dir, exist_ok=True)
        fd, staged_path = tempfile.mkstemp(
            prefix='exec_',
            suffix='.py',
            dir=self._staged_code_dir,
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as staged_file:
                staged_file.write(code)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(staged_path)
            except OSError:
                pass
            raise

        wrapper = (
            'exec(compile('
            f'__import__("pathlib").Path({staged_path!r}).read_text(encoding="utf-8"), '
            f'{staged_path!r}, '
            '"exec"), globals())\n'
        )
        return wrapper, False, staged_path

    def _get_or_create_session(
        self, session_id: Optional[str], kernel_name: str, **kernel_kwargs: Any
    ) -> KernelSession:
        """Get existing session or create new one (sync version, fallback)"""
        self._cleanup_expired_sessions()

        # If session_id provided, try to find existing session
        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
            session.update_last_used()
            return session

        # Create new session - always persistent for now
        # The caller decides whether to keep it or clean it up
        actual_kernel_name = self._get_best_kernel(kernel_name)
        km, kc = start_new_kernel(kernel_name=actual_kernel_name, **kernel_kwargs)

        # Wait for kernel to be ready
        kc.wait_for_ready(10)
        self._ensure_user_site_in_path(kc)

        session = KernelSession(km, kc, actual_kernel_name, session_id=session_id)

        # Enforce max sessions limit
        if len(self.sessions) >= self.max_sessions:
            # Remove oldest session
            oldest_session_id = min(
                self.sessions.keys(), key=lambda k: self.sessions[k].last_used
            )
            old_session = self.sessions.pop(oldest_session_id)
            old_session.cleanup()
            logger.info(f'Evicted oldest session {oldest_session_id} to make room')

        self.sessions[session.session_id] = session
        logger.info(
            f'Created new kernel session {session.session_id} with kernel {actual_kernel_name}'
        )

        return session

    async def _get_or_create_session_async(
        self, session_id: Optional[str], kernel_name: str, **kernel_kwargs: Any
    ) -> KernelSession:
        """Get existing session or create new one, using pool when available"""
        self._cleanup_expired_sessions()

        # If session_id provided, try to find existing session
        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
            session.update_last_used()
            # Apply cwd if requested (consistent with bash exec_dir semantics)
            from app.utils import get_workspace
            requested_cwd = kernel_kwargs.get('cwd')
            if requested_cwd and requested_cwd != get_workspace():
                kc = session.kernel_client
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, JupyterService._chdir_kernel, kc, requested_cwd
                )
            return session

        # Try to get from pool first (only for default kernel)
        actual_kernel = self._get_best_kernel(kernel_name)
        default_kernel = self._get_best_kernel(self._default_kernel)
        pooled_session = None

        if actual_kernel == default_kernel:
            pooled_session = await self._pool.acquire()

        if pooled_session:
            # Reconfigure the pooled session
            if session_id:
                pooled_session.session_id = session_id
            pooled_session.update_last_used()

            # If caller requested a cwd that differs from the pooled kernel's cwd
            # (pooled kernels start at WORKSPACE), chdir the kernel to the target dir.
            from app.utils import get_workspace
            requested_cwd = kernel_kwargs.get('cwd')
            if requested_cwd and requested_cwd != get_workspace():
                kc = pooled_session.kernel_client

                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, JupyterService._chdir_kernel, kc, requested_cwd
                )

            # Enforce max sessions limit
            if len(self.sessions) >= self.max_sessions:
                oldest_session_id = min(
                    self.sessions.keys(), key=lambda k: self.sessions[k].last_used
                )
                old_session = self.sessions.pop(oldest_session_id)
                old_session.cleanup()
                logger.info(f'Evicted oldest session {oldest_session_id} to make room')

            self.sessions[pooled_session.session_id] = pooled_session
            logger.info(
                f'Got kernel session {pooled_session.session_id} from pool '
                f'with kernel {pooled_session.kernel_name}'
            )
            return pooled_session

        # Fallback: create session synchronously
        loop = asyncio.get_event_loop()
        session = await loop.run_in_executor(
            None, lambda: self._get_or_create_session(session_id, kernel_name, **kernel_kwargs)
        )
        return session

    @staticmethod
    def _serialize_outputs(outputs: list[JupyterOutput]) -> list[dict[str, Any]]:
        return [output.model_dump(exclude_none=True) for output in outputs]

    def _log_execute_code_event(
        self,
        *,
        result: ExecuteCodeResult,
        started_at: float,
        timeout: int,
        requested_kernel: Optional[str],
        kernel_kwargs: dict[str, Any],
        phase: str,
        level: int | None = None,
        error: Exception | None = None,
    ) -> None:
        log_level = level
        if log_level is None:
            if result.status == KernelStatus.ERROR:
                log_level = logging.ERROR
            elif result.status == KernelStatus.TIMEOUT:
                log_level = logging.WARNING
            else:
                log_level = logging.INFO

        details: dict[str, Any] = {
            'requested_kernel': requested_kernel or self._default_kernel,
            'kernel_name': result.kernel_name,
            'timeout_seconds': timeout,
            'code': result.code,
            'outputs': self._serialize_outputs(result.outputs),
            'execution_count': result.execution_count,
            'msg_id': result.msg_id,
        }
        if kernel_kwargs:
            details['kernel_kwargs'] = kernel_kwargs
        if error is not None:
            details['error'] = str(error)

        log_execution_event(
            logger,
            component='jupyter',
            operation='execute_code',
            phase=phase,
            status=result.status,
            session_id=result.session_id,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            details=details,
            level=log_level,
        )

    @trace_jupyter()
    async def execute_code(
        self,
        code: str,
        timeout: int = 30,
        kernel_name: Optional[str] = None,
        session_id: Optional[str] = None,
        **kernel_kwargs: Any,
    ) -> ExecuteCodeResult:
        """
        Execute code using Jupyter kernel with session persistence

        Args:
            code: Code to execute
            timeout: Execution timeout in seconds
            kernel_name: Kernel name or alias. Defaults to the unified runtime Python
                version resolved from PYTHON_VERSION.
                Supports: 'python', 'python3', 'python3.10', 'python3.11', 'python3.12'
            session_id: Optional session ID to maintain state across requests
            **kernel_kwargs: Additional kernel configuration parameters (e.g., cwd)

        Returns:
            Execution results with outputs and session_id
        """
        started_at = time.perf_counter()
        # Use default kernel from environment if not specified
        effective_kernel = kernel_name or self._default_kernel

        try:
            # Get or create session (using pool when available)
            session = await self._get_or_create_session_async(
                session_id, effective_kernel, **kernel_kwargs
            )

            logger.info(
                f'Executing code in session {session.session_id} with kernel {session.kernel_name}'
            )

            # 获取 per-session 执行锁，防止同一 kernel 上的并发执行
            async with session._exec_lock:
                # Execute code in thread pool to avoid blocking
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self._execute_code_sync, session.kernel_client, code, timeout
                )

            # Convert dict result to ExecuteCodeResult
            outputs = [
                JupyterOutput(**output) if isinstance(output, dict) else output
                for output in result.get('outputs', [])
            ]

            execution_result = ExecuteCodeResult(
                kernel_name=session.kernel_name,
                session_id=session.session_id,
                status=KernelStatus(result['status']),
                outputs=outputs,
                code=result['code'],
                msg_id=result['msg_id'],
                execution_count=result['execution_count'],
            )
            self._log_execute_code_event(
                result=execution_result,
                started_at=started_at,
                timeout=timeout,
                requested_kernel=kernel_name,
                kernel_kwargs=kernel_kwargs,
                phase='completed',
            )
            return execution_result

        except Exception as e:
            logger.error(f'Code execution failed: {e}', exc_info=True)

            execution_result = ExecuteCodeResult(
                kernel_name=kernel_name,
                session_id=session_id or 'error',
                status=KernelStatus.ERROR,
                outputs=[
                    JupyterOutput(
                        output_type=OutputType.ERROR,
                        ename='ExecutionError',
                        evalue=str(e),
                        traceback=[str(e)],
                    )
                ],
                code=code,
                msg_id=None,
                execution_count=None,
            )
            self._log_execute_code_event(
                result=execution_result,
                started_at=started_at,
                timeout=timeout,
                requested_kernel=kernel_name,
                kernel_kwargs=kernel_kwargs,
                phase='failed',
                level=logging.ERROR,
                error=e,
            )
            return execution_result

    def _execute_code_sync(
        self, kernel_client: BlockingKernelClient, code: str, timeout: int
    ) -> Dict[str, Any]:
        """Synchronous code execution helper"""
        staged_code_path = None
        try:
            exec_code, store_history, staged_code_path = self._prepare_code_for_execution(
                code
            )
            # Execute the code
            msg_id = kernel_client.execute(
                exec_code, silent=False, store_history=store_history
            )

            # Collect all messages
            outputs = []
            execution_count = None
            status = 'unknown'

            # Get execution results
            while True:
                try:
                    msg = kernel_client.get_iopub_msg(timeout=timeout)
                    msg_type = msg['msg_type']
                    content = msg['content']
                    parent_msg_id = msg.get('parent_header', {}).get('msg_id')

                    # Only process messages from our execution
                    if parent_msg_id == msg_id:
                        if msg_type == 'stream':
                            outputs.append(
                                {
                                    'output_type': 'stream',
                                    'name': content['name'],
                                    'text': content['text'],
                                }
                            )

                        elif msg_type == 'execute_result':
                            outputs.append(
                                {
                                    'output_type': 'execute_result',
                                    'data': content['data'],
                                    'metadata': content.get('metadata', {}),
                                    'execution_count': content['execution_count'],
                                }
                            )
                            execution_count = content['execution_count']

                        elif msg_type == 'display_data':
                            outputs.append(
                                {
                                    'output_type': 'display_data',
                                    'data': content['data'],
                                    'metadata': content.get('metadata', {}),
                                }
                            )

                        elif msg_type == 'error':
                            outputs.append(
                                {
                                    'output_type': 'error',
                                    'ename': content['ename'],
                                    'evalue': content['evalue'],
                                    'traceback': content['traceback'],
                                }
                            )

                        elif (
                            msg_type == 'status'
                            and content['execution_state'] == 'idle'
                        ):
                            status = 'ok'
                            break

                except Exception as e:
                    if 'timeout' in str(e).lower():
                        status = 'timeout'
                        break
                    else:
                        status = 'error'
                        outputs.append(
                            {
                                'output_type': 'error',
                                'ename': 'KernelError',
                                'evalue': str(e),
                                'traceback': [str(e)],
                            }
                        )
                        break

            # Get the execute_reply message for final status (match msg_id to avoid stale replies)
            reply = JupyterService._get_shell_reply(kernel_client, msg_id, timeout=5)
            if reply and reply['msg_type'] == 'execute_reply':
                status = reply['content']['status']
                if execution_count is None:
                    execution_count = reply['content'].get('execution_count')

            # Override status to 'error' if outputs contain error output
            # (Jupyter returns 'ok' even when Python code raises exceptions)
            has_error_output = any(
                o.get('output_type') == 'error' for o in outputs
            )
            if has_error_output:
                status = 'error'

            return {
                'msg_id': msg_id,
                'status': status,
                'execution_count': execution_count,
                'outputs': outputs,
                'code': code,
            }

        except Exception as e:
            return {
                'msg_id': None,
                'status': 'error',
                'execution_count': None,
                'outputs': [
                    {
                        'output_type': 'error',
                        'ename': 'ExecutionError',
                        'evalue': str(e),
                        'traceback': [str(e)],
                    }
                ],
                'code': code,
            }
        finally:
            if staged_code_path:
                try:
                    os.unlink(staged_code_path)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.debug(
                        f'Failed to remove staged Jupyter code file {staged_code_path}: {e}'
                    )

    def get_available_kernels(self) -> List[str]:
        """Get list of available kernel names"""
        return self.available_kernels.copy()

    def get_active_sessions(self) -> ActiveSessionsResult:
        """Get info about active sessions"""
        self._cleanup_expired_sessions()
        sessions = {
            session_id: SessionInfo(
                kernel_name=session.kernel_name,
                last_used=session.last_used,
                age_seconds=int(time.time() - session.last_used),
            )
            for session_id, session in self.sessions.items()
        }
        return ActiveSessionsResult(sessions=sessions)

    def cleanup_session(self, session_id: str) -> bool:
        """Manually cleanup a specific session"""
        if session_id in self.sessions:
            session = self.sessions.pop(session_id)
            session.cleanup()
            logger.info(f'Manually cleaned up session {session_id}')
            return True
        return False

    def cleanup_all_sessions(self):
        """Cleanup all active sessions

        Note: 池化会话由 SessionPoolManager 统一管理，不在此处清理
        """
        for session in self.sessions.values():
            session.cleanup()
        active_count = len(self.sessions)
        self.sessions.clear()

        logger.info(f'Cleaned up {active_count} active kernel sessions')
