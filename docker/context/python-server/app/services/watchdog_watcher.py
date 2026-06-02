"""Ordered watchdog watcher for non-Linux fallback platforms."""

from __future__ import annotations

import asyncio
import logging
import os
from fnmatch import fnmatch
from typing import Any, AsyncIterator

from watchdog.events import FileSystemEventHandler, FileSystemMovedEvent
from watchdog.observers import Observer


logger = logging.getLogger(__name__)


class _QueueingEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[dict[str, Any]],
        path: str,
        exclude: list[str],
        include_patterns: list[str],
    ) -> None:
        self._loop = loop
        self._queue = queue
        self._path = path
        self._exclude = exclude
        self._include = include_patterns
        self._root_is_dir = os.path.isdir(path)

    def on_created(self, event) -> None:
        self._emit_event(
            {
                'type': 'create',
                'path': os.path.realpath(event.src_path),
                'is_dir': event.is_directory,
            }
        )

    def on_modified(self, event) -> None:
        self._emit_event(
            {
                'type': 'write',
                'path': os.path.realpath(event.src_path),
                'is_dir': event.is_directory,
            }
        )

    def on_deleted(self, event) -> None:
        if self._root_is_dir and os.path.realpath(event.src_path) == self._path:
            self._emit_event(
                {
                    'type': 'remove',
                    'path': self._path,
                    'is_dir': True,
                    'stop_after': True,
                    'force_emit': True,
                }
            )
            return
        self._emit_event(
            {
                'type': 'remove',
                'path': os.path.realpath(event.src_path),
                'is_dir': event.is_directory,
            }
        )

    def on_moved(self, event: FileSystemMovedEvent) -> None:
        old_path = os.path.realpath(event.src_path)
        if self._root_is_dir and old_path == self._path:
            self._emit_event(
                {
                    'type': 'remove',
                    'path': self._path,
                    'is_dir': True,
                    'stop_after': True,
                    'force_emit': True,
                }
            )
            return
        self._emit_event(
            {
                'type': 'rename',
                'path': os.path.realpath(event.dest_path),
                'old_path': old_path,
                'is_dir': event.is_directory,
            }
        )

    def _emit_event(self, event: dict[str, Any]) -> None:
        if not event.pop('force_emit', False) and self._is_excluded(event):
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def _is_excluded(self, event: dict[str, Any]) -> bool:
        names = [
            os.path.basename(path)
            for path in (event.get('path'), event.get('old_path'))
            if path
        ]
        for name in names:
            if any(fnmatch(name, pattern) for pattern in self._exclude):
                return True
        if self._include and not any(
            fnmatch(name, pattern)
            for name in names
            for pattern in self._include
        ):
            return True
        return False


class WatchdogWatcher:
    """Async iterator wrapper around watchdog's ordered event queue."""

    def __init__(
        self,
        path: str,
        recursive: bool = True,
        debounce_s: float = 0.3,
        exclude: list[str] | None = None,
        include_patterns: list[str] | None = None,
    ) -> None:
        self._path = path
        self._recursive = recursive
        self._debounce = debounce_s
        self._exclude = exclude or []
        self._include = include_patterns or []

    async def watch(self) -> AsyncIterator[list[dict[str, Any]]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        watch_root = self._path if os.path.isdir(self._path) else os.path.dirname(self._path)

        handler = _QueueingEventHandler(
            loop=loop,
            queue=queue,
            path=self._path,
            exclude=self._exclude,
            include_patterns=self._include,
        )
        observer = Observer()
        observer.schedule(handler, watch_root, recursive=self._recursive)
        observer.start()

        try:
            should_stop = False
            while True:
                event = await queue.get()
                should_stop = bool(event.pop('stop_after', False))
                batch = [event]

                while True:
                    try:
                        next_event = await asyncio.wait_for(
                            queue.get(),
                            timeout=self._debounce,
                        )
                    except asyncio.TimeoutError:
                        break

                    should_stop = should_stop or bool(next_event.pop('stop_after', False))
                    batch.append(next_event)

                if batch:
                    yield batch
                if should_stop:
                    break
        finally:
            observer.stop()
            await asyncio.to_thread(observer.join)
