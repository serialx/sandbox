"""Shutdown hooks service — register, persist, and execute cleanup commands on shutdown."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from itertools import groupby
from pathlib import Path

from app.core.exceptions import BadRequestException
from app.models.sandbox import SandboxHook, SandboxHookResult

logger = logging.getLogger(__name__)

STATE_DIR = Path('/var/lib/aio-sandbox')
HOOKS_FILE = STATE_DIR / 'shutdown-hooks.json'
MAX_HOOKS = 50
MAX_NAME_LEN = 64
DEFAULT_HOOK_TIMEOUT = 10  # seconds
DEFAULT_GLOBAL_TIMEOUT = 30  # seconds
_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


class ShutdownHookService:
    """Manage shutdown hooks with file persistence and ENV pre-configuration."""

    def __init__(
        self,
        hooks_file: Path = HOOKS_FILE,
        result_dir: str | None = None,
    ):
        self._hooks_file = hooks_file
        self._hooks: dict[str, SandboxHook] = {}
        self._lock = threading.Lock()
        self._global_timeout = int(
            os.getenv('SANDBOX_SHUTDOWN_HOOKS_TIMEOUT', str(DEFAULT_GLOBAL_TIMEOUT))
        )
        self._result_dir = result_dir or os.getenv('SANDBOX_SHUTDOWN_HOOKS_RESULT_DIR', '')

        # 1. ENV hooks first (deployment baseline, executes first)
        self._parse_env_hooks()

        # 2. Restore API hooks from disk (runtime additions, executes after ENV)
        self._load()

    # ── Registration ──

    @staticmethod
    def _validate_name(name: str) -> None:
        """Validate hook name: non-empty, <= 64 chars, alphanumeric/dash/underscore."""
        if not name:
            raise BadRequestException('Hook name cannot be empty')
        if len(name) > MAX_NAME_LEN:
            raise BadRequestException(f'Hook name too long: max {MAX_NAME_LEN} characters')
        if not _NAME_RE.match(name):
            raise BadRequestException('Hook name must match [a-zA-Z0-9_-]+')

    def register(
        self,
        name: str,
        command: str,
        timeout: float = DEFAULT_HOOK_TIMEOUT,
        priority: int = 100,
    ) -> SandboxHook:
        """Register or update a shutdown hook."""
        self._validate_name(name)

        with self._lock:
            # Count check (don't count overwrite)
            if name not in self._hooks and len(self._hooks) >= MAX_HOOKS:
                raise BadRequestException(f'Cannot register hook: limit of {MAX_HOOKS} reached')

            hook = SandboxHook(name=name, command=command, timeout=timeout, priority=priority, source='api')
            self._hooks[name] = hook
            self._persist()

        logger.info(f'Registered shutdown hook: {name} (priority={priority})')
        return hook

    def unregister(self, name: str) -> bool:
        """Remove a hook by name. ENV hooks cannot be removed. Returns True if removed."""
        with self._lock:
            hook = self._hooks.get(name)
            if hook is None:
                return False
            if hook.source == 'env':
                raise BadRequestException(f'Cannot remove env hook: {name}')
            del self._hooks[name]
            self._persist()

        logger.info(f'Unregistered shutdown hook: {name}')
        return True

    def list_hooks(self) -> list[SandboxHook]:
        """Return all hooks sorted by priority (lower first)."""
        with self._lock:
            return sorted(self._hooks.values(), key=lambda h: h.priority)

    # ── Execution ──

    async def run_all(self) -> list[SandboxHookResult]:
        """Execute hooks grouped by priority. Same priority runs in parallel (asyncio.gather)."""
        with self._lock:
            hooks = sorted(self._hooks.values(), key=lambda h: h.priority)
        if not hooks:
            return []

        logger.info(f'Executing {len(hooks)} shutdown hooks (global timeout={self._global_timeout}s)')
        results: list[SandboxHookResult] = []
        global_start = time.monotonic()

        # Group by priority
        for priority, group in groupby(hooks, key=lambda h: h.priority):
            group_hooks = list(group)
            elapsed = time.monotonic() - global_start
            remaining = self._global_timeout - elapsed
            if remaining <= 0:
                # Global timeout exceeded — mark all remaining as timed out
                for hook in group_hooks:
                    results.append(SandboxHookResult(
                        name=hook.name,
                        command=hook.command,
                        timed_out=True,
                        duration_ms=0,
                    ))
                continue

            if len(group_hooks) == 1:
                # Single hook — run directly
                hook = group_hooks[0]
                timeout = min(hook.timeout, remaining)
                result = await self._run_one(hook, timeout)
                results.append(result)
            else:
                # Multiple hooks with same priority — run in parallel
                logger.info(f'Running {len(group_hooks)} hooks in parallel (priority={priority})')
                tasks = []
                for hook in group_hooks:
                    timeout = min(hook.timeout, remaining)
                    tasks.append(self._run_one(hook, timeout))
                group_results = await asyncio.gather(*tasks)
                results.extend(group_results)

        self._save_results(results)
        return results

    async def _run_one(self, hook: SandboxHook, timeout: float) -> SandboxHookResult:
        """Execute a single hook with timeout."""
        start = time.monotonic()
        proc = None
        try:
            proc = await asyncio.create_subprocess_shell(
                hook.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            duration_ms = (time.monotonic() - start) * 1000
            result = SandboxHookResult(
                name=hook.name,
                command=hook.command,
                exit_code=proc.returncode,
                stdout=stdout_bytes.decode(errors='replace').strip(),
                stderr=stderr_bytes.decode(errors='replace').strip(),
                duration_ms=duration_ms,
            )
            if proc.returncode != 0:
                logger.warning(
                    f'Hook {hook.name!r} exited with code {proc.returncode}: {result.stderr}'
                )
            else:
                logger.info(f'Hook {hook.name!r} completed in {duration_ms:.0f}ms')
            return result

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            logger.warning(f'Hook {hook.name!r} timed out after {timeout}s')
            return SandboxHookResult(
                name=hook.name,
                command=hook.command,
                timed_out=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error(f'Hook {hook.name!r} failed: {e}')
            return SandboxHookResult(
                name=hook.name,
                command=hook.command,
                exit_code=-1,
                stderr=str(e),
                duration_ms=duration_ms,
            )

    # ── Results ──

    def _save_results(self, results: list[SandboxHookResult]) -> None:
        """Save execution results to result_dir if configured."""
        if not self._result_dir:
            return
        try:
            result_dir = Path(self._result_dir)
            result_dir.mkdir(parents=True, exist_ok=True)
            result_file = result_dir / 'shutdown-hooks-results.json'
            result_file.write_text(json.dumps(
                [r.model_dump() for r in results], indent=2
            ))
            logger.info(f'Shutdown hook results saved to {result_file}')
        except Exception as e:
            logger.warning(f'Failed to save shutdown hook results: {e}')

    # ── Persistence ──

    def _persist(self):
        """Write API hooks to disk (ENV hooks are never persisted). Caller must hold _lock."""
        api_hooks = [h.model_dump() for h in self._hooks.values() if h.source == 'api']
        self._hooks_file.parent.mkdir(parents=True, exist_ok=True)
        self._hooks_file.write_text(json.dumps(api_hooks, indent=2))

    def _load(self):
        """Load API hooks from disk file."""
        try:
            text = self._hooks_file.read_text().strip()
            if not text:
                return
            data = json.loads(text)
            for item in data:
                hook = SandboxHook(
                    name=item['name'],
                    command=item['command'],
                    timeout=item.get('timeout', DEFAULT_HOOK_TIMEOUT),
                    priority=item.get('priority', 100),
                    source='api',
                )
                self._hooks[hook.name] = hook
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f'Failed to load shutdown hooks from {self._hooks_file}: {e}')
        except FileNotFoundError:
            pass

    def _parse_env_hooks(self):
        """Parse SANDBOX_SHUTDOWN_HOOKS env var as a single bash script (same as RUN_HOOK_*)."""
        raw = os.getenv('SANDBOX_SHUTDOWN_HOOKS', '').strip()
        if not raw:
            return
        name = 'env-shutdown-hook'
        self._hooks[name] = SandboxHook(
            name=name,
            command=raw,
            timeout=DEFAULT_HOOK_TIMEOUT,
            priority=0,  # ENV hooks always run first
            source='env',
        )
        logger.info(f'Loaded ENV shutdown hook: {raw!r}')
