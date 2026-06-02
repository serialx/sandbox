import asyncio
import logging
import os
import subprocess
import tempfile
import time
from functools import cached_property
from typing import Any, Dict, List, Optional

import httpx

from app.core.version import (
    NODE_REPL_DEFAULT_PORTS,
    canonicalize_node_version,
    resolve_node_repl_disabled,
    resolve_node_repl_port,
    resolve_node_version,
)
from app.logging.decorators import trace_nodejs
from app.logging.execution import log_execution_event
from app.models.nodejs import (
    ExecutionStatus,
    NodeJSExecuteResult,
    NodeJSOutput,
    NodeJSRuntimeInfo,
    OutputType,
)


logger = logging.getLogger(__name__)

# Node.js version configuration
NODE_VERSION_ALIASES: dict[str, str] = {
    'node': 'node22',
    'node20': 'node20',
    'node22': 'node22',
    'node24': 'node24',
    '20': 'node20',
    '22': 'node22',
    '24': 'node24',
}

NODE_BINARY_PATHS: dict[str, str] = {
    'node20': '/usr/local/bin/node20',
    'node22': '/usr/local/bin/node22',
    'node24': '/usr/local/bin/node24',
}

# REPL server ports for each Node.js version
REPL_VERSION_PORTS: dict[str, int] = NODE_REPL_DEFAULT_PORTS.copy()

# Supervisor program names for each Node.js version
REPL_SUPERVISOR_PROGRAMS: dict[str, str] = {
    'node20': 'nodejs-repl-server-20',
    'node22': 'nodejs-repl-server-22',
    'node24': 'nodejs-repl-server-24',
}

# Default version from environment (NODE_VERSION > NODE_CODE_EXEC_VERSION > node22)
DEFAULT_NODE_VERSION = resolve_node_version()


class NodeJSService:
    def __init__(self):
        self._node_runtime_dir = None
        # HTTP client for REPL server
        self._http_client: Optional[httpx.AsyncClient] = None
        # REPL server readiness state
        self._repl_server_ready = False
        # Cached version info per Node.js version (populated lazily)
        self._version_cache: Dict[str, Dict[str, str]] = {}

    @staticmethod
    def _serialize_outputs(outputs: List[NodeJSOutput]) -> list[dict[str, Any]]:
        return [output.model_dump(exclude_none=True) for output in outputs]

    @staticmethod
    def _summarize_input_files(
        files: Optional[Dict[str, str]],
    ) -> dict[str, Any] | None:
        if not files:
            return None
        return {
            'count': len(files),
            'paths': list(files.keys()),
            'contents': list(files.values()),
        }

    def _log_execute_code_event(
        self,
        *,
        operation: str,
        result: NodeJSExecuteResult,
        started_at: float,
        timeout: int,
        requested_version: Optional[str],
        resolved_version: str,
        stateful: bool,
        cwd: Optional[str],
        stdin: Optional[str] = None,
        files: Optional[Dict[str, str]] = None,
        phase: str = 'completed',
        level: int | None = None,
        error: Exception | None = None,
    ) -> None:
        log_level = level
        if log_level is None:
            if result.status == ExecutionStatus.ERROR or result.exit_code not in (None, 0):
                log_level = logging.ERROR
            elif result.status == ExecutionStatus.TIMEOUT:
                log_level = logging.WARNING
            else:
                log_level = logging.INFO

        details: dict[str, Any] = {
            'requested_version': requested_version or DEFAULT_NODE_VERSION,
            'resolved_version': resolved_version,
            'stateful': stateful,
            'timeout_seconds': timeout,
            'cwd': cwd,
            'stdin': stdin,
            'files': self._summarize_input_files(files),
            'language': result.language,
            'execution_count': result.execution_count,
            'code': result.code,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'outputs': self._serialize_outputs(result.outputs),
        }
        if error is not None:
            details['error'] = str(error)

        log_execution_event(
            logger,
            component='nodejs',
            operation=operation,
            phase=phase,
            status=result.status,
            session_id=result.session_id,
            exit_code=result.exit_code,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            details=details,
            level=log_level,
        )

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Lazy-initialized HTTP client.

        No default timeout - individual requests specify their own timeouts.
        This matches Jupyter service pattern where execution timeout is per-request.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=None)
        return self._http_client

    def _resolve_version(self, version: Optional[str] = None) -> str:
        """Resolve version alias to canonical version name.

        Args:
            version: Version string like 'node20', '22', or None for default

        Returns:
            Canonical version name like 'node22'
        """
        if version is None:
            return resolve_node_version()
        return canonicalize_node_version(version, fallback=resolve_node_version())

    def _get_node_binary(self, version: Optional[str] = None) -> str:
        """Get the node binary path for a specific version.

        Args:
            version: Version string or None for default

        Returns:
            Path to the node binary (falls back to 'node' if version-specific binary not found)
        """
        resolved = self._resolve_version(version)
        binary_path = NODE_BINARY_PATHS.get(resolved)
        if binary_path and os.path.exists(binary_path):
            return binary_path
        # Fallback to system node for local development
        return 'node'

    def _get_repl_server_url(self, version: Optional[str] = None) -> str:
        """Get the REPL server URL for a specific version.

        Args:
            version: Version string or None for default

        Returns:
            URL to the REPL server for that version
        """
        resolved = self._resolve_version(version)
        port = resolve_node_repl_port(resolved)
        return f'http://localhost:{port}'

    def _get_repl_versions(self, version: Optional[str] = None) -> tuple[str, ...]:
        """Return resolved REPL versions to target for a session management request."""
        if version is not None:
            return (self._resolve_version(version),)
        return tuple(REPL_VERSION_PORTS)

    @staticmethod
    def _is_repl_disabled() -> bool:
        """Return whether NodeJS REPL startup is disabled."""
        return resolve_node_repl_disabled()

    async def _start_repl_server(self, version: str) -> bool:
        """
        Start a REPL server for the specified version using supervisorctl.

        Args:
            version: Resolved Node.js version (e.g., 'node20', 'node22')

        Returns:
            True if started successfully, False otherwise
        """
        program_name = REPL_SUPERVISOR_PROGRAMS.get(version)
        if not program_name:
            logger.error(f'Unknown Node.js version: {version}')
            return False

        if self._is_repl_disabled():
            logger.warning(
                'Node.js REPL startup is disabled via DISABLE_NODEJS_REPL=true '
                f'(requested version: {version})'
            )
            return False

        try:
            logger.info(f'Starting Node.js REPL server ({version}) via supervisorctl...')
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ['supervisorctl', 'start', program_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                ),
            )
            if result.returncode == 0:
                logger.info(f'Node.js REPL server ({version}) started successfully')
                return True
            elif 'ALREADY_STARTED' in result.stderr or 'already started' in result.stdout.lower():
                logger.info(f'Node.js REPL server ({version}) was already running')
                return True
            else:
                logger.error(
                    f'Failed to start Node.js REPL server ({version}): '
                    f'stdout={result.stdout}, stderr={result.stderr}'
                )
                return False
        except subprocess.TimeoutExpired:
            logger.error(f'Timeout starting Node.js REPL server ({version})')
            return False
        except Exception as e:
            logger.error(f'Error starting Node.js REPL server ({version}): {e}')
            return False

    async def _ensure_repl_server_ready(
        self,
        version: Optional[str] = None,
        max_retries: int = 30,
        retry_interval: float = 1.0,
    ) -> bool:
        """
        Ensure REPL server is ready before making requests.

        This method performs health checks with retries to handle the case where
        the Python server starts before the Node.js REPL server is fully ready.
        If the server is not running, it will attempt to start it via supervisorctl.

        Args:
            version: Node.js version to check (default uses NODE_CODE_EXEC_VERSION)
            max_retries: Maximum number of health check attempts (default 30)
            retry_interval: Seconds to wait between retries (default 1.0)

        Returns:
            True if server is ready, False if failed after all retries
        """
        resolved_version = self._resolve_version(version)

        if self._is_repl_disabled():
            logger.warning(
                'Node.js REPL is disabled via DISABLE_NODEJS_REPL=true '
                f'(requested version: {resolved_version})'
            )
            return False

        # Check if this specific version's server has been confirmed ready
        ready_key = f'_repl_ready_{resolved_version}'
        if getattr(self, ready_key, False):
            return True

        server_url = self._get_repl_server_url(version)
        server_started = False

        for attempt in range(max_retries):
            try:
                response = await self.http_client.get(
                    f'{server_url}/health',
                    timeout=5.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('status') == 'ok':
                        setattr(self, ready_key, True)
                        logger.info(
                            f'Node.js REPL server ({resolved_version}) is ready (attempt {attempt + 1})'
                        )
                        return True
            except httpx.ConnectError:
                # Server not running, try to start it on first connection failure
                if not server_started:
                    logger.info(
                        f'Node.js REPL server ({resolved_version}) not running, attempting to start...'
                    )
                    started = await self._start_repl_server(resolved_version)
                    if started:
                        server_started = True
                    else:
                        logger.warning(
                            f'Failed to start Node.js REPL server ({resolved_version}), '
                            'will continue retrying health checks...'
                        )
            except Exception as e:
                logger.debug(f'Health check attempt {attempt + 1} failed: {e}')

            if attempt < max_retries - 1:
                await asyncio.sleep(retry_interval)

        logger.error(
            f'Node.js REPL server ({resolved_version}) not ready after {max_retries} attempts'
        )
        return False

    @cached_property
    def node_runtime_dir(self) -> str | None:
        """Setup Node.js runtime directory with required dependencies"""
        try:
            # Prefer absolute runtime location inside container image
            absolute_runtime_dir = '/opt/runtime/nodejs'

            # Fallback: resolve relative runtime directory from this file
            current_dir = os.path.dirname(os.path.abspath(__file__))
            relative_runtime_dir = os.path.abspath(
                os.path.join(current_dir, '../../../../runtime/nodejs')
            )

            node_runtime_dir = None

            # Choose the first existing runtime directory
            if os.path.exists(absolute_runtime_dir):
                node_runtime_dir = absolute_runtime_dir
                logger.info(f'Using Node.js runtime directory: {absolute_runtime_dir}')
            elif os.path.exists(relative_runtime_dir):
                node_runtime_dir = relative_runtime_dir
                logger.info(f'Using Node.js runtime directory: {relative_runtime_dir}')
            else:
                logger.warning(
                    f'Node.js runtime directory not found at: {absolute_runtime_dir} or {relative_runtime_dir}'
                )
                return None

            # Check if Node.js is available in system PATH
            result = subprocess.run(
                ['node', '--version'], capture_output=True, text=True
            )
            if result.returncode != 0:
                logger.error('Node.js not found in system PATH')
                return node_runtime_dir

            node_version = result.stdout.strip()
            logger.info(f'Found Node.js version: {node_version}')

            # Check if npm is available
            result = subprocess.run(
                ['npm', '--version'], capture_output=True, text=True
            )
            if result.returncode != 0:
                logger.error('npm not found in system PATH')
                return node_runtime_dir

            npm_version = result.stdout.strip()
            logger.info(f'Found npm version: {npm_version}')

            return node_runtime_dir

        except Exception as e:
            logger.error(f'Failed to setup Node.js runtime: {e}')
            return None

    @trace_nodejs()
    async def execute_code(
        self,
        code: str,
        timeout: int = 30,
        stdin: Optional[str] = None,
        files: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        version: Optional[str] = None,
    ) -> NodeJSExecuteResult:
        """
        Execute JavaScript code (stateless, one-off execution)

        Args:
            code: JavaScript code to execute
            timeout: Execution timeout in seconds
            stdin: Standard input for the process
            files: Additional files to create in the execution directory
            cwd: Current working directory for code execution
            version: Node.js version to use (e.g., 'node20', 'node22', 'node24')

        Returns:
            Execution results with outputs
        """

        started_at = time.perf_counter()
        resolved_version = self._resolve_version(version)

        try:
            logger.info(f'Executing JavaScript code with {resolved_version}')

            # Execute code in thread pool to avoid blocking
            execution_result = await asyncio.get_event_loop().run_in_executor(
                None, self._execute_code_sync, code, timeout, stdin, files, cwd, resolved_version
            )

            self._log_execute_code_event(
                operation='execute_code',
                result=execution_result,
                started_at=started_at,
                timeout=timeout,
                requested_version=version,
                resolved_version=resolved_version,
                stateful=False,
                cwd=cwd,
                stdin=stdin,
                files=files,
            )
            return execution_result

        except Exception as e:
            logger.error(f'Code execution failed: {e}', exc_info=True)

            execution_result = NodeJSExecuteResult(
                language='javascript',
                status=ExecutionStatus.ERROR,
                outputs=[
                    NodeJSOutput(
                        output_type=OutputType.ERROR,
                        ename='ExecutionError',
                        evalue=str(e),
                        traceback=[str(e)],
                    )
                ],
                code=code,
                execution_count=None,
                stdout='',
                stderr=str(e),
                exit_code=1,
            )
            self._log_execute_code_event(
                operation='execute_code',
                result=execution_result,
                started_at=started_at,
                timeout=timeout,
                requested_version=version,
                resolved_version=resolved_version,
                stateful=False,
                cwd=cwd,
                stdin=stdin,
                files=files,
                phase='failed',
                level=logging.ERROR,
                error=e,
            )
            return execution_result

    def _execute_code_sync(
        self,
        code: str,
        timeout: int,
        stdin: Optional[str] = None,
        files: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        version: Optional[str] = None,
    ) -> NodeJSExecuteResult:
        """Synchronous JavaScript code execution helper"""

        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                # Set up symlinks to runtime dependencies if available
                if self.node_runtime_dir and os.path.exists(self.node_runtime_dir):
                    node_modules_src = os.path.join(
                        self.node_runtime_dir, 'node_modules'
                    )
                    if os.path.exists(node_modules_src):
                        node_modules_dst = os.path.join(tmp_dir, 'node_modules')
                        if not os.path.exists(node_modules_dst):
                            os.symlink(node_modules_src, node_modules_dst)
                            logger.debug(
                                f'Symlinked node_modules from {node_modules_src}'
                            )

                # Create additional files if provided
                if files:
                    for filename, content in files.items():
                        file_path = os.path.join(tmp_dir, filename)
                        # Ensure directory exists
                        os.makedirs(os.path.dirname(file_path), exist_ok=True)
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(content)

                # Create JavaScript file
                with tempfile.NamedTemporaryFile(
                    mode='w', dir=tmp_dir, suffix='.js', delete=False, encoding='utf-8'
                ) as f:
                    f.write(code)
                    code_file = f.name

                # Get version-specific node binary (with fallback to system node)
                node_binary = self._get_node_binary(version)

                # Prepare command with version-specific binary
                command = [node_binary, code_file]

                # Execute the command
                logger.info(f'Running command: {" ".join(command)} in {tmp_dir}')

                # Prepare environment (set NODE_PATH to point to runtime node_modules)
                child_env = os.environ.copy()
                if self.node_runtime_dir:
                    runtime_node_modules = os.path.join(
                        self.node_runtime_dir, 'node_modules'
                    )
                    if os.path.exists(runtime_node_modules):
                        existing_node_path = child_env.get('NODE_PATH', '')
                        child_env['NODE_PATH'] = (
                            f'{runtime_node_modules}:{existing_node_path}'
                            if existing_node_path
                            else runtime_node_modules
                        )

                effective_cwd = cwd or tmp_dir
                if effective_cwd != tmp_dir and not os.path.isdir(effective_cwd):
                    return NodeJSExecuteResult(
                        language='javascript',
                        status=ExecutionStatus.ERROR,
                        execution_count=None,
                        outputs=[
                            NodeJSOutput(
                                output_type=OutputType.ERROR,
                                ename='DirectoryError',
                                evalue=f'Working directory does not exist: {effective_cwd}',
                                traceback=[],
                            )
                        ],
                        code=code,
                        stdout='',
                        stderr=f'Working directory does not exist: {effective_cwd}',
                        exit_code=1,
                    )

                process = subprocess.Popen(
                    command,
                    cwd=effective_cwd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=child_env,
                )

                try:
                    stdout, stderr = process.communicate(input=stdin, timeout=timeout)
                    exit_code = process.returncode
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                    exit_code = -1
                    stderr = f'Execution timed out after {timeout} seconds\n' + (
                        stderr or ''
                    )

                # Determine status
                if exit_code == -1:
                    status = ExecutionStatus.TIMEOUT
                elif exit_code == 0:
                    status = ExecutionStatus.OK
                else:
                    status = ExecutionStatus.ERROR

                # Create outputs based on stdout/stderr
                outputs = []
                if stdout:
                    outputs.append(
                        NodeJSOutput(
                            output_type=OutputType.STREAM, name='stdout', text=stdout
                        )
                    )

                if stderr:
                    outputs.append(
                        NodeJSOutput(
                            output_type=OutputType.STREAM, name='stderr', text=stderr
                        )
                    )

                # If there was an error, add error output
                if status == ExecutionStatus.ERROR:
                    outputs.append(
                        NodeJSOutput(
                            output_type=OutputType.ERROR,
                            ename='RuntimeError',
                            evalue=f'Process exited with code {exit_code}',
                            traceback=stderr.split('\n') if stderr else [],
                        )
                    )

                return NodeJSExecuteResult(
                    language='javascript',
                    status=status,
                    execution_count=1,
                    outputs=outputs,
                    code=code,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                )

            except Exception as e:
                logger.error(f'Error executing JavaScript code: {e}', exc_info=True)
                return NodeJSExecuteResult(
                    language='javascript',
                    status=ExecutionStatus.ERROR,
                    execution_count=None,
                    outputs=[
                        NodeJSOutput(
                            output_type=OutputType.ERROR,
                            ename='ExecutionError',
                            evalue=str(e),
                            traceback=[str(e)],
                        )
                    ],
                    code=code,
                    stdout='',
                    stderr=str(e),
                    exit_code=1,
                )

    def get_available_languages(self) -> List[str]:
        """Get list of supported languages"""
        return ['javascript']

    def get_available_versions(self) -> List[str]:
        """Get list of available Node.js versions"""
        available = []
        for version, binary_path in NODE_BINARY_PATHS.items():
            if os.path.exists(binary_path):
                available.append(version)
        return sorted(available)

    def _get_cached_versions(self, version: Optional[str] = None) -> Dict[str, str]:
        """Get cached node/npm versions, fetching once per version if not cached.

        This avoids running subprocess on every request since versions don't change.
        """
        resolved_version = self._resolve_version(version)

        if resolved_version not in self._version_cache:
            node_binary = self._get_node_binary(version)

            # Get Node.js version from specific binary
            result = subprocess.run(
                [node_binary, '--version'], capture_output=True, text=True
            )
            node_version = (
                result.stdout.strip() if result.returncode == 0 else 'Not found'
            )

            # Get npm version (use version-specific npm if available)
            npm_binary = (
                node_binary.replace('node', 'npm')
                if node_binary.startswith('/')
                else 'npm'
            )
            if npm_binary != 'npm' and not os.path.exists(npm_binary):
                npm_binary = 'npm'
            result = subprocess.run(
                [npm_binary, '--version'], capture_output=True, text=True
            )
            npm_version = (
                result.stdout.strip() if result.returncode == 0 else 'Not found'
            )

            self._version_cache[resolved_version] = {
                'node_version': node_version,
                'npm_version': npm_version,
            }
            logger.debug(
                f'Cached versions for {resolved_version}: '
                f'node={node_version}, npm={npm_version}'
            )

        return self._version_cache[resolved_version]

    def get_runtime_info(self, version: Optional[str] = None) -> NodeJSRuntimeInfo:
        """Get Node.js runtime information (basic, from local node/npm)

        Args:
            version: Specific Node.js version to check, or None for default

        Note: Version info is cached after first call since it doesn't change.
        """
        try:
            resolved_version = self._resolve_version(version)
            versions = self._get_cached_versions(version)

            return NodeJSRuntimeInfo(
                node_version=versions['node_version'],
                npm_version=versions['npm_version'],
                supported_languages=self.get_available_languages(),
                description=f'Node.js JavaScript execution service ({resolved_version})',
                runtime_directory=self.node_runtime_dir,
                available_versions=self.get_available_versions(),
                current_version=resolved_version,
            )

        except Exception as e:
            logger.error(f'Error getting runtime info: {e}')
            return NodeJSRuntimeInfo(
                node_version='Not found',
                npm_version='Not found',
                supported_languages=self.get_available_languages(),
                description='Node.js JavaScript execution service',
                runtime_directory=None,
                error=str(e),
            )

    async def get_repl_runtime_info(self, version: Optional[str] = None) -> NodeJSRuntimeInfo:
        """Get detailed runtime info from REPL server (includes packages)

        Args:
            version: Specific Node.js version to check, or None for default
        """
        try:
            resolved_version = self._resolve_version(version)

            # Ensure REPL server is ready
            if not await self._ensure_repl_server_ready(version=version):
                return self.get_runtime_info(version=version)

            server_url = self._get_repl_server_url(version)
            response = await self.http_client.get(f'{server_url}/info', timeout=10.0)

            if response.status_code != 200:
                raise RuntimeError(f'REPL server returned {response.status_code}')

            data = response.json()

            # Use cached npm version instead of subprocess
            versions = self._get_cached_versions(version)
            npm_version = versions['npm_version']

            # Parse packages
            from app.models.nodejs import NodeJSPackageInfo

            runtime_packages = [
                NodeJSPackageInfo(name=p['name'], version=p['version'])
                for p in data.get('packages', {}).get('runtime', [])
            ]
            global_packages = [
                NodeJSPackageInfo(name=p['name'], version=p['version'])
                for p in data.get('packages', {}).get('global', [])
            ]

            return NodeJSRuntimeInfo(
                node_version=data.get('version', 'Unknown'),
                npm_version=npm_version,
                supported_languages=self.get_available_languages(),
                description=f'Node.js REPL execution service ({resolved_version})',
                runtime_directory=data.get('modulePaths', {}).get('runtime'),
                global_npm_directory=data.get('modulePaths', {}).get('globalNpm'),
                runtime_packages=runtime_packages,
                global_packages=global_packages,
                available_versions=self.get_available_versions(),
                current_version=resolved_version,
            )

        except Exception as e:
            logger.error(f'Error getting REPL runtime info: {e}')
            # Fallback to basic info
            return self.get_runtime_info(version=version)

    # ==================== Stateful REPL Execution (via HTTP) ====================

    @trace_nodejs()
    async def execute_code_stateful(
        self,
        code: str,
        timeout: int = 30,
        session_id: Optional[str] = None,
        cwd: Optional[str] = None,
        version: Optional[str] = None,
    ) -> NodeJSExecuteResult:
        """
        Execute JavaScript code with persistent state (REPL mode) via HTTP server

        Args:
            code: JavaScript code to execute
            timeout: Execution timeout in seconds
            session_id: Optional session ID to maintain state across requests
            cwd: Current working directory
            version: Node.js version to use (e.g., 'node20', 'node22', 'node24')

        Returns:
            Execution results with outputs and session_id

        Note:
            The response contains both `stdout` and `result` which are different:
            - stdout: Captured output from console.log/info/debug
            - result: The evaluated value of the LAST expression (like Node.js REPL)

            Examples:
                "const x = 1 + 2; console.log(x)"
                → stdout: "3", result: "" (console.log returns undefined)

                "const x = 1 + 2; x"
                → stdout: "", result: "3" (last expression is x)

                "[1,2,3].map(n => n * 2)"
                → stdout: "", result: "[ 2, 4, 6 ]"
        """
        started_at = time.perf_counter()
        resolved_version = self._resolve_version(version)

        try:
            logger.info(f'Executing JavaScript code in REPL mode ({resolved_version})')

            # Ensure REPL server is ready before making requests
            if not await self._ensure_repl_server_ready(version=version):
                disabled_message = (
                    'Node.js REPL is disabled via DISABLE_NODEJS_REPL=true'
                    if self._is_repl_disabled()
                    else f'Node.js REPL server ({resolved_version}) is not available'
                )
                execution_result = NodeJSExecuteResult(
                    language='javascript',
                    status=ExecutionStatus.ERROR,
                    outputs=[
                        NodeJSOutput(
                            output_type=OutputType.ERROR,
                            ename='ServiceUnavailable',
                            evalue=disabled_message,
                            traceback=['REPL server failed health check after multiple retries'],
                        )
                    ],
                    code=code,
                    execution_count=None,
                    stdout='',
                    stderr=disabled_message,
                    exit_code=1,
                    session_id=session_id,
                )
                self._log_execute_code_event(
                    operation='execute_code_stateful',
                    result=execution_result,
                    started_at=started_at,
                    timeout=timeout,
                    requested_version=version,
                    resolved_version=resolved_version,
                    stateful=True,
                    cwd=cwd,
                )
                return execution_result

            server_url = self._get_repl_server_url(version)

            # Convert timeout to milliseconds for REPL server
            # Add 100ms buffer on REPL side to account for execution overhead
            repl_timeout_ms = timeout * 1000 + 100

            # Call REPL server
            # HTTP timeout should be slightly longer than REPL timeout to ensure
            # we get the timeout error from REPL rather than HTTP client
            response = await self.http_client.post(
                f'{server_url}/execute',
                json={
                    'code': code,
                    'session_id': session_id,
                    'timeout': repl_timeout_ms,
                    'cwd': cwd,
                },
                timeout=timeout + 2,  # Add 2 seconds buffer for network latency
            )

            if response.status_code != 200:
                raise RuntimeError(
                    f'REPL server returned {response.status_code}: {response.text}'
                )

            result = response.json()

            # Convert response to NodeJSExecuteResult
            outputs = []
            stdout = result.get('stdout', '')
            stderr = result.get('stderr', '')

            if stdout:
                outputs.append(
                    NodeJSOutput(
                        output_type=OutputType.STREAM, name='stdout', text=stdout
                    )
                )

            if stderr:
                outputs.append(
                    NodeJSOutput(
                        output_type=OutputType.STREAM, name='stderr', text=stderr
                    )
                )

            # Add result as execute_result if present
            result_value = result.get('result', '')
            if result_value and result_value != 'undefined':
                outputs.append(
                    NodeJSOutput(
                        output_type=OutputType.EXECUTE_RESULT,
                        data={'text/plain': result_value},
                        execution_count=1,
                    )
                )

            success = result.get('success', False)
            error = result.get('error')

            # Determine status and exit_code
            is_timeout = error and error.get('name') == 'TimeoutError'
            if success:
                status = ExecutionStatus.OK
                exit_code = 0
            elif is_timeout:
                status = ExecutionStatus.TIMEOUT
                exit_code = -1
            else:
                status = ExecutionStatus.ERROR
                exit_code = 1

            if not success and error:
                outputs.append(
                    NodeJSOutput(
                        output_type=OutputType.ERROR,
                        ename=error.get('name', 'Error'),
                        evalue=error.get('message', ''),
                        traceback=error.get('stack', '').split('\n')
                        if error.get('stack')
                        else [],
                    )
                )

            execution_result = NodeJSExecuteResult(
                language='javascript',
                status=status,
                execution_count=1,
                outputs=outputs,
                code=code,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                session_id=result.get('session_id'),
            )
            self._log_execute_code_event(
                operation='execute_code_stateful',
                result=execution_result,
                started_at=started_at,
                timeout=timeout,
                requested_version=version,
                resolved_version=resolved_version,
                stateful=True,
                cwd=cwd,
            )
            return execution_result

        except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
            logger.error(f'Failed to connect to REPL server: {e}')
            # Reset ready state so next call will retry health check
            ready_key = f'_repl_ready_{resolved_version}'
            setattr(self, ready_key, False)
            error_name = 'RemoteProtocolError' if isinstance(e, httpx.RemoteProtocolError) else 'ConnectionError'
            execution_result = NodeJSExecuteResult(
                language='javascript',
                status=ExecutionStatus.ERROR,
                outputs=[
                    NodeJSOutput(
                        output_type=OutputType.ERROR,
                        ename=error_name,
                        evalue=f'REPL server ({resolved_version}) not available at {server_url}',
                        traceback=[str(e)],
                    )
                ],
                code=code,
                execution_count=None,
                stdout='',
                stderr=f'REPL server not available: {e}',
                exit_code=1,
                session_id=session_id,
            )
            self._log_execute_code_event(
                operation='execute_code_stateful',
                result=execution_result,
                started_at=started_at,
                timeout=timeout,
                requested_version=version,
                resolved_version=resolved_version,
                stateful=True,
                cwd=cwd,
                phase='failed',
                level=logging.ERROR,
                error=e,
            )
            return execution_result

        except httpx.TimeoutException as e:
            logger.error(f'REPL server request timed out: {e}')
            execution_result = NodeJSExecuteResult(
                language='javascript',
                status=ExecutionStatus.TIMEOUT,
                outputs=[
                    NodeJSOutput(
                        output_type=OutputType.ERROR,
                        ename='TimeoutError',
                        evalue=f'REPL server request timed out after {timeout} seconds',
                        traceback=[str(e)],
                    )
                ],
                code=code,
                execution_count=None,
                stdout='',
                stderr=f'REPL server request timed out: {e}',
                exit_code=1,
                session_id=session_id,
            )
            self._log_execute_code_event(
                operation='execute_code_stateful',
                result=execution_result,
                started_at=started_at,
                timeout=timeout,
                requested_version=version,
                resolved_version=resolved_version,
                stateful=True,
                cwd=cwd,
                phase='failed',
                level=logging.ERROR,
                error=e,
            )
            return execution_result

        except httpx.HTTPStatusError as e:
            logger.error(f'REPL server returned HTTP error: {e}')
            execution_result = NodeJSExecuteResult(
                language='javascript',
                status=ExecutionStatus.ERROR,
                outputs=[
                    NodeJSOutput(
                        output_type=OutputType.ERROR,
                        ename='HTTPError',
                        evalue=f'REPL server returned HTTP {e.response.status_code}',
                        traceback=[str(e), e.response.text[:500] if e.response.text else ''],
                    )
                ],
                code=code,
                execution_count=None,
                stdout='',
                stderr=f'REPL server HTTP error: {e}',
                exit_code=1,
                session_id=session_id,
            )
            self._log_execute_code_event(
                operation='execute_code_stateful',
                result=execution_result,
                started_at=started_at,
                timeout=timeout,
                requested_version=version,
                resolved_version=resolved_version,
                stateful=True,
                cwd=cwd,
                phase='failed',
                level=logging.ERROR,
                error=e,
            )
            return execution_result

        except Exception as e:
            logger.error(f'Stateful code execution failed: {e}', exc_info=True)

            # Reset ready state for any unexpected errors that might indicate server issues
            # This ensures the next call will retry health check
            ready_key = f'_repl_ready_{resolved_version}'
            setattr(self, ready_key, False)

            execution_result = NodeJSExecuteResult(
                language='javascript',
                status=ExecutionStatus.ERROR,
                outputs=[
                    NodeJSOutput(
                        output_type=OutputType.ERROR,
                        ename='ExecutionError',
                        evalue=str(e),
                        traceback=[str(e)],
                    )
                ],
                code=code,
                execution_count=None,
                stdout='',
                stderr=str(e),
                exit_code=1,
                session_id=session_id,
            )
            self._log_execute_code_event(
                operation='execute_code_stateful',
                result=execution_result,
                started_at=started_at,
                timeout=timeout,
                requested_version=version,
                resolved_version=resolved_version,
                stateful=True,
                cwd=cwd,
                phase='failed',
                level=logging.ERROR,
                error=e,
            )
            return execution_result

    async def cleanup_session(
        self,
        session_id: str,
        timeout: float = 120.0,
        version: Optional[str] = None,
    ) -> bool:
        """Manually cleanup a specific REPL session via HTTP"""
        try:
            server_url = self._get_repl_server_url(version)
            response = await self.http_client.delete(
                f'{server_url}/sessions/{session_id}',
                timeout=timeout,
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f'Failed to cleanup session {session_id}: {e}')
            return False

    async def cleanup_all_sessions(
        self,
        timeout: float = 120.0,
        version: Optional[str] = None,
    ):
        """Cleanup all sessions - get list and delete each"""
        for resolved_version in self._get_repl_versions(version):
            try:
                sessions = await self.get_active_sessions(
                    timeout=timeout,
                    version=resolved_version,
                )
                for session_id in sessions.keys():
                    await self.cleanup_session(
                        session_id,
                        timeout=timeout,
                        version=resolved_version,
                    )
            except Exception as e:
                logger.error(
                    f'Failed to cleanup all sessions for {resolved_version}: {e}'
                )

    async def get_active_sessions(
        self,
        timeout: float = 120.0,
        version: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Get info about active REPL sessions via HTTP"""
        try:
            server_url = self._get_repl_server_url(version)
            response = await self.http_client.get(
                f'{server_url}/sessions',
                timeout=timeout,
            )
            if response.status_code == 200:
                return response.json().get('sessions', {})
            return {}
        except Exception as e:
            logger.error(f'Failed to get active sessions: {e}')
            return {}

    async def check_repl_server_health(
        self,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Check health of REPL server"""
        try:
            server_url = self._get_repl_server_url(version)
            response = await self.http_client.get(f'{server_url}/health', timeout=5.0)
            if response.status_code == 200:
                return response.json()
            return {'status': 'error', 'message': f'HTTP {response.status_code}'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    # ==================== Session CRUD Operations ====================

    async def create_session(
        self,
        session_id: Optional[str] = None,
        cwd: str = '/tmp',
        max_idle_time: int = 1800,
        timeout: float = 120.0,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new REPL session

        Args:
            session_id: Optional custom session ID (auto-generated if not provided)
            cwd: Working directory for the session
            max_idle_time: Maximum idle time in seconds (default 30 minutes)
            timeout: Request timeout

        Returns:
            Dict with session_id, created flag, and session info
        """
        try:
            # Ensure REPL server is ready
            if not await self._ensure_repl_server_ready(version):
                raise RuntimeError('Node.js REPL server is not available')

            server_url = self._get_repl_server_url(version)
            response = await self.http_client.post(
                f'{server_url}/sessions',
                json={
                    'session_id': session_id,
                    'cwd': cwd,
                    'max_idle_time': max_idle_time,
                },
                timeout=timeout,
            )
            if response.status_code == 200:
                return response.json()
            raise RuntimeError(f'REPL server returned {response.status_code}: {response.text}')
        except Exception as e:
            logger.error(f'Failed to create session: {e}')
            raise

    async def get_session(
        self,
        session_id: str,
        timeout: float = 120.0,
        version: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific session

        Args:
            session_id: Session ID to look up
            timeout: Request timeout

        Returns:
            Session info dict or None if not found
        """
        try:
            server_url = self._get_repl_server_url(version)
            response = await self.http_client.get(
                f'{server_url}/sessions/{session_id}',
                timeout=timeout,
            )
            if response.status_code == 200:
                return response.json().get('session')
            if response.status_code == 404:
                return None
            raise RuntimeError(f'REPL server returned {response.status_code}: {response.text}')
        except Exception as e:
            logger.error(f'Failed to get session {session_id}: {e}')
            raise

    async def update_session(
        self,
        session_id: str,
        max_idle_time: Optional[int] = None,
        cwd: Optional[str] = None,
        timeout: float = 120.0,
        version: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Update session configuration

        Args:
            session_id: Session ID to update
            max_idle_time: New maximum idle time in seconds
            cwd: New working directory
            timeout: Request timeout

        Returns:
            Updated session info dict or None if not found
        """
        try:
            server_url = self._get_repl_server_url(version)
            update_data: Dict[str, Any] = {}
            if max_idle_time is not None:
                update_data['max_idle_time'] = max_idle_time
            if cwd is not None:
                update_data['cwd'] = cwd

            response = await self.http_client.patch(
                f'{server_url}/sessions/{session_id}',
                json=update_data,
                timeout=timeout,
            )
            if response.status_code == 200:
                return response.json().get('session')
            if response.status_code == 404:
                return None
            raise RuntimeError(f'REPL server returned {response.status_code}: {response.text}')
        except Exception as e:
            logger.error(f'Failed to update session {session_id}: {e}')
            raise

    async def delete_session(
        self,
        session_id: str,
        timeout: float = 120.0,
        version: Optional[str] = None,
    ) -> bool:
        """
        Delete a specific session

        Args:
            session_id: Session ID to delete
            timeout: Request timeout

        Returns:
            True if deleted, False if not found
        """
        return await self.cleanup_session(
            session_id,
            timeout=timeout,
            version=version,
        )

    async def list_sessions(
        self,
        timeout: float = 120.0,
        version: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        List all active sessions

        Args:
            timeout: Request timeout

        Returns:
            Dict mapping session IDs to session info
        """
        return await self.get_active_sessions(timeout=timeout, version=version)

    async def close(self):
        """Close HTTP client"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
