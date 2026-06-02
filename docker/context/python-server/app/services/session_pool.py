"""
Generic session pool for pre-warming and fast allocation.

This module provides:
- SessionPool: A generic pool for any session type
- SessionPoolManager: Centralized manager for all pools
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Generic, TypeVar, Awaitable, Optional, Dict

logger = logging.getLogger(__name__)

T = TypeVar('T')


class SessionPool(Generic[T]):
    """
    A generic pre-warming session pool with concurrency support.

    Features:
    - Pre-warms sessions at initialization
    - Thread-safe acquire with async lock
    - Auto-refill after acquire
    - Optional wait for available session under high concurrency
    """

    def __init__(
        self,
        name: str,
        pool_size: int,
        factory: Callable[[], Awaitable[T]],
        cleanup: Callable[[T], None],
    ):
        """
        Args:
            name: Pool name for logging
            pool_size: Number of sessions to maintain in the pool
            factory: Async callable that creates a new session
            cleanup: Sync callable to cleanup/close a session
        """
        self.name = name
        self.pool_size = pool_size
        self._factory = factory
        self._cleanup = cleanup

        self._pool: list[T] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._refill_task: Optional[asyncio.Task] = None
        self._initialized = False
        self._shutdown = False

        # Event to signal when pool has available sessions
        self._available: asyncio.Event | None = None
        # Track pending waiters for metrics
        self._pending_waiters = 0

    def _ensure_async_state(self) -> tuple[asyncio.Lock, asyncio.Event]:
        """Bind loop-aware asyncio primitives to the current running loop."""
        loop = asyncio.get_running_loop()
        if (
            self._loop is loop
            and self._lock is not None
            and self._available is not None
        ):
            return self._lock, self._available

        if self._loop is not None and self._loop is not loop:
            logger.debug(f'[{self.name}] Rebinding session pool to a new event loop')

        self._loop = loop
        self._lock = asyncio.Lock()
        self._available = asyncio.Event()
        if self._pool or self._shutdown:
            self._available.set()
        self._refill_task = None
        return self._lock, self._available

    async def initialize(self) -> None:
        """Initialize the pool with pre-warmed sessions."""
        _, available = self._ensure_async_state()
        if self._initialized:
            return

        logger.info(f'[{self.name}] Initializing session pool with size {self.pool_size}')

        # Pre-warm sessions in parallel
        tasks = [self._create_session() for _ in range(self.pool_size)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.warning(f'[{self.name}] Failed to pre-warm session: {result}')
            else:
                self._pool.append(result)

        self._initialized = True
        if self._pool:
            available.set()
        logger.info(
            f'[{self.name}] Session pool initialized with {len(self._pool)} sessions'
        )

    async def _create_session(self) -> T:
        """Create a session using the factory."""
        return await self._factory()

    async def acquire(self, wait_timeout: Optional[float] = None) -> Optional[T]:
        """
        Acquire a session from the pool.

        Args:
            wait_timeout: If set, wait up to this many seconds for a session
                         to become available. If None, return immediately.

        Returns:
            A pre-warmed session if available, None otherwise.
            Caller should fallback to creating a session if None is returned.
        """
        self._ensure_async_state()
        if self._shutdown:
            return None

        # Try to get immediately first
        session = await self._try_acquire()
        if session is not None:
            return session

        # If no wait requested, return None
        if wait_timeout is None or wait_timeout <= 0:
            return None

        # Wait for a session to become available
        self._pending_waiters += 1
        try:
            logger.debug(
                f'[{self.name}] Waiting for session (timeout={wait_timeout}s, '
                f'waiters={self._pending_waiters})'
            )

            # Trigger refill immediately since we have waiters
            self._schedule_refill()

            try:
                # Wait for the available event with timeout
                await asyncio.wait_for(
                    self._wait_for_available(), timeout=wait_timeout
                )
                return await self._try_acquire()
            except asyncio.TimeoutError:
                logger.debug(f'[{self.name}] Wait timeout, no session available')
                return None
        finally:
            self._pending_waiters -= 1

    async def _wait_for_available(self) -> None:
        """Wait until a session is available in the pool."""
        _, available = self._ensure_async_state()
        while True:
            if self._shutdown:
                return
            if self._pool:
                return
            available.clear()
            await available.wait()

    async def _try_acquire(self) -> Optional[T]:
        """Try to acquire a session without waiting."""
        lock, _ = self._ensure_async_state()
        async with lock:
            if self._pool:
                session = self._pool.pop(0)
                logger.debug(
                    f'[{self.name}] Acquired session from pool, '
                    f'{len(self._pool)} remaining'
                )
                self._schedule_refill()
                return session
        return None

    def _schedule_refill(self) -> None:
        """Schedule a background task to refill the pool."""
        if self._shutdown:
            return

        if self._refill_task is None or self._refill_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._refill_task = loop.create_task(self._refill())
            except RuntimeError:
                pass  # No running loop

    async def _refill(self) -> None:
        """Refill the pool to maintain pool_size."""
        lock, available = self._ensure_async_state()
        if self._shutdown:
            return

        try:
            async with lock:
                current_size = len(self._pool)
                needed = self.pool_size - current_size

            if needed <= 0:
                return

            logger.debug(f'[{self.name}] Refilling pool, need {needed} sessions')

            for _ in range(needed):
                if self._shutdown:
                    break

                try:
                    session = await self._create_session()
                    async with lock:
                        if len(self._pool) < self.pool_size:
                            self._pool.append(session)
                            # Notify waiters that a session is available
                            available.set()
                        else:
                            # Pool is full, cleanup the extra session
                            self._cleanup(session)
                except Exception as e:
                    logger.warning(f'[{self.name}] Failed to refill pool: {e}')

            logger.debug(
                f'[{self.name}] Pool refilled, now has {len(self._pool)} sessions'
            )
        except Exception as e:
            logger.error(f'[{self.name}] Error in pool refill: {e}')

    async def shutdown(self) -> None:
        """Shutdown the pool and cleanup all sessions."""
        lock, available = self._ensure_async_state()
        self._shutdown = True
        available.set()

        # Cancel refill task if running
        if self._refill_task and not self._refill_task.done():
            self._refill_task.cancel()
            try:
                await self._refill_task
            except asyncio.CancelledError:
                pass

        # Cleanup all pooled sessions
        async with lock:
            count = len(self._pool)
            for session in self._pool:
                try:
                    self._cleanup(session)
                except Exception as e:
                    logger.warning(f'[{self.name}] Error cleaning up session: {e}')
            self._pool.clear()

        logger.info(f'[{self.name}] Pool shutdown, cleaned up {count} sessions')

    def shutdown_sync(self) -> int:
        """
        Synchronous shutdown - cleanup all sessions without async.
        Use this when called from a sync context.

        Returns:
            Number of sessions cleaned up.
        """
        self._shutdown = True
        count = len(self._pool)

        for session in self._pool:
            try:
                self._cleanup(session)
            except Exception as e:
                logger.warning(f'[{self.name}] Error cleaning up session: {e}')

        self._pool.clear()
        logger.info(f'[{self.name}] Pool shutdown (sync), cleaned up {count} sessions')
        return count

    @property
    def size(self) -> int:
        """Current number of sessions in the pool."""
        return len(self._pool)

    @property
    def is_initialized(self) -> bool:
        """Whether the pool has been initialized."""
        return self._initialized

    def stats(self) -> dict:
        """Get pool statistics."""
        return {
            'name': self.name,
            'pool_size': self.pool_size,
            'current_size': len(self._pool),
            'initialized': self._initialized,
            'pending_waiters': self._pending_waiters,
        }


class SessionPoolManager:
    """
    Centralized manager for all session pools.

    Usage:
        manager = SessionPoolManager()
        manager.register('shell', shell_pool)
        manager.register('jupyter', jupyter_pool)

        await manager.initialize_all()
        await manager.shutdown_all()
    """

    def __init__(self):
        self._pools: Dict[str, SessionPool] = {}

    def register(self, name: str, pool: SessionPool) -> None:
        """Register a pool with the manager."""
        self._pools[name] = pool
        logger.debug(f'Registered pool: {name}')

    def get(self, name: str) -> Optional[SessionPool]:
        """Get a pool by name."""
        return self._pools.get(name)

    async def initialize_all(self) -> None:
        """Initialize all registered pools in parallel."""
        if not self._pools:
            logger.info('No pools to initialize')
            return

        logger.info(f'Initializing {len(self._pools)} session pools...')

        tasks = [pool.initialize() for pool in self._pools.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for name, result in zip(self._pools.keys(), results):
            if isinstance(result, Exception):
                logger.warning(f'Failed to initialize pool {name}: {result}')

        logger.info('All session pools initialized')

    async def shutdown_all(self) -> None:
        """Shutdown all registered pools."""
        if not self._pools:
            return

        logger.info(f'Shutting down {len(self._pools)} session pools...')

        tasks = [pool.shutdown() for pool in self._pools.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info('All session pools shut down')

    def shutdown_all_sync(self) -> None:
        """Synchronously shutdown all pools."""
        for name, pool in self._pools.items():
            try:
                pool.shutdown_sync()
            except Exception as e:
                logger.warning(f'Error shutting down pool {name}: {e}')

    def stats(self) -> Dict[str, dict]:
        """Get statistics for all pools."""
        return {name: pool.stats() for name, pool in self._pools.items()}

    @property
    def pool_names(self) -> list[str]:
        """Get names of all registered pools."""
        return list(self._pools.keys())

