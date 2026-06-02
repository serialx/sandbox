"""FileWatchService — filesystem monitoring with inotify or ordered fallbacks."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from app.models.file_watch import WatcherCreatedResult, WatcherStoppedResult
from app.schemas.file_watch import CreateWatchRequest
from app.services.state_store import StateStore

logger = logging.getLogger(__name__)

# Detect backend: asyncinotify (Linux) or ordered fallback on other platforms
_USE_INOTIFY = sys.platform == "linux"
_USE_WATCHDOG = False
if _USE_INOTIFY:
    try:
        from app.services.inotify_watcher import InotifyWatcher as _InotifyWatcher

        logger.info("Using asyncinotify backend (Linux inotify)")
    except ImportError:
        _USE_INOTIFY = False

if not _USE_INOTIFY:
    try:
        from app.services.watchdog_watcher import WatchdogWatcher as _WatchdogWatcher

        _USE_WATCHDOG = True
        logger.info("Using watchdog backend (ordered fallback)")
    except ImportError:
        from watchfiles import Change, awatch

        logger.info("Using watchfiles backend (fallback)")


class WatcherLimitExceededError(RuntimeError):
    """Raised when the configured watcher limit would be exceeded."""


@dataclass
class ActiveWatcher:
    id: str
    path: str
    target_path: str
    config: CreateWatchRequest
    task: asyncio.Task[None]
    subscribers: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    lease_count: int = 1


class FileWatchService:
    """Manages filesystem watchers backed by platform-specific backends."""

    def __init__(
        self,
        store: StateStore,
        max_watchers: int | None = None,
        allowed_paths: list[str] | None = None,
        idle_ttl: int | None = None,
    ) -> None:
        self._store = store
        self._max_watchers = max_watchers or int(
            os.getenv("AIO_FILE_WATCH_MAX_WATCHERS", "10")
        )
        self._allowed_paths = self._resolve_allowed_paths(allowed_paths)
        if "AIO_FILE_WATCH_IDLE_TTL" in os.environ:
            logger.warning(
                "AIO_FILE_WATCH_IDLE_TTL is deprecated and ignored; "
                "file-watch watchers now require explicit DELETE /v1/file/watch/{watcher_id}"
            )
        # Retained for backwards-compatible construction only. File-watch watchers
        # are now explicit resources and are not auto-cleaned up by an idle TTL.
        self._idle_ttl = idle_ttl
        self._watchers: dict[str, ActiveWatcher] = {}
        self._closing_watchers: set[str] = set()
        self._lock = asyncio.Lock()

    # ── public API ──────────────────────────────────────────────────

    async def create_watcher(self, req: CreateWatchRequest) -> WatcherCreatedResult:
        requested_path = os.path.abspath(req.path)
        path_obj = Path(requested_path).resolve()
        target_path = str(path_obj)
        self._check_path_allowed(target_path)
        if not path_obj.exists():
            raise FileNotFoundError(f"path {requested_path!r} does not exist")
        if not (path_obj.is_dir() or path_obj.is_file()):
            raise ValueError(f"path {requested_path!r} must be a file or directory")
        normalized_req = self._normalize_request(req, requested_path, path_obj)

        async with self._lock:
            config_hash = normalized_req.config_hash()
            self._prune_finished_watchers_locked()

            # Reuse only a still-running in-memory watcher. Persisted stopped
            # resources remain visible until explicit DELETE and must not be
            # removed implicitly by a later create.
            for watcher in self._watchers.values():
                if watcher.config.config_hash() != config_hash:
                    continue
                if watcher.task.done():
                    continue
                watcher.lease_count += 1
                return WatcherCreatedResult(
                    watcher_id=watcher.id,
                    path=watcher.path,
                    status="running",
                    created_at=watcher.created_at,
                    reused=True,
                )

            # Check max limit
            if len(self._watchers) >= self._max_watchers:
                raise WatcherLimitExceededError(
                    f"max watcher limit reached ({self._max_watchers})"
                )

            watcher_id = uuid.uuid4().hex[:12]
            now = time.time()

            # Persist to store
            await self._store.upsert_watcher(
                watcher_id, normalized_req.path, config_hash, normalized_req.model_dump()
            )

            # Start the awatch task
            task = asyncio.create_task(
                self._run_watcher(
                    watcher_id,
                    target_path,
                    normalized_req.path,
                    normalized_req,
                ),
                name=f"fw-{watcher_id}",
            )
            watcher = ActiveWatcher(
                id=watcher_id,
                path=normalized_req.path,
                target_path=target_path,
                config=normalized_req,
                task=task,
                created_at=now,
            )
            self._watchers[watcher_id] = watcher
            task.add_done_callback(
                lambda finished_task, wid=watcher_id: asyncio.create_task(
                    self._handle_watcher_task_done(wid, finished_task)
                )
            )

            return WatcherCreatedResult(
                watcher_id=watcher_id,
                path=normalized_req.path,
                status="running",
                created_at=now,
                reused=False,
            )

    async def stop_watcher(self, watcher_id: str) -> WatcherStoppedResult:
        async with self._lock:
            watcher = self._watchers.get(watcher_id)
            if watcher is None:
                persisted = await self._store.get_watcher(watcher_id)
                if persisted is None:
                    raise KeyError(f"watcher {watcher_id!r} not found")
                self._closing_watchers.add(watcher_id)
                try:
                    self._store.notify_watcher_closed(watcher_id)
                    await self._store.delete_watcher(watcher_id)
                    return WatcherStoppedResult(watcher_id=watcher_id, status="stopped")
                finally:
                    self._closing_watchers.discard(watcher_id)
            watcher.lease_count -= 1
            if watcher.lease_count > 0:
                return WatcherStoppedResult(watcher_id=watcher_id, status="running")

            self._watchers.pop(watcher_id, None)
            self._closing_watchers.add(watcher_id)
            try:
                self._store.notify_watcher_closed(watcher_id)
                watcher.task.cancel()
                try:
                    await watcher.task
                except (asyncio.CancelledError, Exception):
                    pass
                await self._store.delete_watcher(watcher_id)
            finally:
                self._closing_watchers.discard(watcher_id)

        return WatcherStoppedResult(watcher_id=watcher_id, status="stopped")

    async def subscribe(self, watcher_id: str, subscriber_id: str) -> None:
        async with self._lock:
            watcher = self._watchers.get(watcher_id)
            if watcher is None:
                raise KeyError(f"watcher {watcher_id!r} not found")
            watcher.subscribers.add(subscriber_id)

    async def unsubscribe(self, watcher_id: str, subscriber_id: str) -> None:
        async with self._lock:
            watcher = self._watchers.get(watcher_id)
            if watcher is None:
                raise KeyError(f"watcher {watcher_id!r} not found")
            watcher.subscribers.discard(subscriber_id)

    def get_watcher(self, watcher_id: str) -> ActiveWatcher | None:
        return self._watchers.get(watcher_id)

    def is_watcher_closing(self, watcher_id: str) -> bool:
        return watcher_id in self._closing_watchers

    async def list_watchers(self) -> list[dict[str, Any]]:
        persisted_watchers = await self._store.list_watchers()
        listed_watchers: list[dict[str, Any]] = []

        for row in persisted_watchers:
            watcher = self._watchers.get(row["id"])
            is_running = watcher is not None and not watcher.task.done()
            listed_watchers.append(
                {
                    "watcher_id": row["id"],
                    "path": row["path"],
                    "status": "running" if is_running else "stopped",
                    "subscriber_count": len(watcher.subscribers) if is_running else 0,
                    "created_at": row["created_at"],
                }
            )

        return listed_watchers

    async def shutdown(self) -> None:
        async with self._lock:
            watchers = list(self._watchers.values())
            for watcher in watchers:
                self._closing_watchers.add(watcher.id)
                self._store.notify_watcher_closed(watcher.id)
                watcher.task.cancel()
            # Wait for all tasks to finish
            tasks = [w.task for w in watchers]
            self._watchers.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        persisted_watchers = await self._store.list_watchers()
        for watcher in persisted_watchers:
            await self._store.delete_watcher(watcher["id"])
        async with self._lock:
            self._closing_watchers.clear()

    # ── internal ────────────────────────────────────────────────────

    async def _run_watcher(
        self,
        watcher_id: str,
        target_path: str,
        display_path: str,
        config: CreateWatchRequest,
    ) -> None:
        try:
            if _USE_INOTIFY:
                await self._run_inotify(watcher_id, target_path, display_path, config)
            elif _USE_WATCHDOG:
                await self._run_watchdog(watcher_id, target_path, display_path, config)
            else:
                await self._run_watchfiles(watcher_id, target_path, display_path, config)
        except asyncio.CancelledError:
            logger.debug("Watcher %s cancelled", watcher_id)
        except Exception:
            logger.exception("Watcher %s crashed", watcher_id)

    async def _handle_watcher_task_done(
        self,
        watcher_id: str,
        finished_task: asyncio.Task[None],
    ) -> None:
        async with self._lock:
            watcher = self._watchers.get(watcher_id)
            if watcher is None or watcher.task is not finished_task:
                return
            self._watchers.pop(watcher_id, None)

        if not finished_task.cancelled():
            exc = finished_task.exception()
            if exc is None:
                logger.warning("Watcher %s exited unexpectedly", watcher_id)
        self._store.notify_watcher_closed(watcher_id)

    def _prune_finished_watchers_locked(self) -> None:
        finished_ids = [
            watcher_id
            for watcher_id, watcher in self._watchers.items()
            if watcher.task.done()
        ]
        for watcher_id in finished_ids:
            self._watchers.pop(watcher_id, None)

    async def _run_inotify(
        self,
        watcher_id: str,
        target_path: str,
        display_path: str,
        config: CreateWatchRequest,
    ) -> None:
        """Linux: asyncinotify with rename cookie pairing."""
        watch_root, recursive = self._watch_scope(target_path, config)
        watcher = _InotifyWatcher(  # type: ignore[misc]
            path=watch_root,
            recursive=recursive,
            debounce_s=config.debounce / 1000.0,
            exclude=config.exclude,
            include_patterns=config.include_patterns,
        )
        async for batch in watcher.watch():
            await self._persist_batch(
                watcher_id=watcher_id,
                target_path=target_path,
                display_path=display_path,
                watch_root=watch_root,
                batch=batch,
            )

    async def _run_watchdog(
        self,
        watcher_id: str,
        target_path: str,
        display_path: str,
        config: CreateWatchRequest,
    ) -> None:
        """macOS/fallback: watchdog preserves emitter order."""
        watch_root, recursive = self._watch_scope(target_path, config)
        watcher = _WatchdogWatcher(  # type: ignore[misc]
            path=target_path,
            recursive=recursive,
            debounce_s=config.debounce / 1000.0,
            exclude=config.exclude,
            include_patterns=config.include_patterns,
        )
        async for batch in watcher.watch():
            await self._persist_batch(
                watcher_id=watcher_id,
                target_path=target_path,
                display_path=display_path,
                watch_root=watch_root,
                batch=batch,
            )

    async def _run_watchfiles(
        self,
        watcher_id: str,
        target_path: str,
        display_path: str,
        config: CreateWatchRequest,
    ) -> None:
        """macOS/fallback: watchfiles (no rename detection)."""
        watch_root, recursive = self._watch_scope(target_path, config)
        async for batch in self._watchfiles_batches(
            target_path,
            watch_root,
            recursive,
            config,
        ):
            await self._persist_batch(
                watcher_id=watcher_id,
                target_path=target_path,
                display_path=display_path,
                watch_root=watch_root,
                batch=batch,
            )

    async def _persist_batch(
        self,
        watcher_id: str,
        target_path: str,
        display_path: str,
        watch_root: str,
        batch: list[dict[str, Any]],
    ) -> None:
        relevant = [event for event in batch if self._event_matches_target(target_path, event)]
        if not relevant:
            return

        now = time.time()
        events = await asyncio.gather(
            *[
                self._build_event_record(
                    watcher_id=watcher_id,
                    target_path=target_path,
                    display_path=display_path,
                    watch_root=watch_root,
                    event=event,
                    event_timestamp=now,
                )
                for event in relevant
            ]
        )
        if events:
            await self._store.insert_events(events)

    async def _watchfiles_batches(
        self,
        path: str,
        watch_root: str,
        recursive: bool,
        config: CreateWatchRequest,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        target_is_dir = os.path.isdir(path)
        change_map = {
            Change.added: "create",
            Change.modified: "write",
            Change.deleted: "remove",
        }
        watch_filter = self._make_watchfiles_filter(config)
        async for changes in awatch(
            watch_root,
            recursive=recursive,
            watch_filter=watch_filter,
            debounce=config.debounce,
            step=50,
        ):
            raw_events = []
            for change_type, change_path in changes:
                event_type = change_map.get(change_type)
                if event_type is None:
                    continue
                raw_events.append(
                    {
                        "type": event_type,
                        "path": change_path,
                        "is_dir": (
                            os.path.isdir(change_path)
                            if change_type != Change.deleted
                            else False
                        ),
                    }
                )

            raw_events = self._normalize_watchfiles_events(raw_events)

            if target_is_dir and not os.path.exists(path):
                if not any(
                    event["type"] == "remove" and event["path"] == path
                    for event in raw_events
                ):
                    raw_events.append(
                        {
                            "type": "remove",
                            "path": path,
                            "relative_path": ".",
                            "is_dir": True,
                        }
                    )
                if raw_events:
                    yield raw_events
                break

            if raw_events:
                yield raw_events

    async def _build_event_record(
        self,
        watcher_id: str,
        target_path: str,
        display_path: str,
        watch_root: str,
        event: dict[str, Any],
        event_timestamp: float,
    ) -> dict[str, Any]:
        metadata = await asyncio.to_thread(
            self._stat_metadata,
            event["path"],
            event["type"],
        )
        presented_path = self._present_event_path(
            display_path=display_path,
            target_path=target_path,
            event_path=event["path"],
        )
        presented_old_path = self._present_event_path(
            display_path=display_path,
            target_path=target_path,
            event_path=event.get("old_path"),
        )
        return {
            "watcher_id": watcher_id,
            "type": event["type"],
            "path": presented_path,
            "relative_path": self._relative_path(
                display_path,
                watch_root,
                presented_path,
            ),
            "old_path": presented_old_path,
            "is_dir": event["is_dir"],
            "timestamp": event_timestamp,
            **metadata,
        }

    @staticmethod
    def _stat_metadata(path: str, event_type: str) -> dict[str, Any]:
        if event_type == "remove":
            return {"mtime": None, "size": None, "inode": None}
        try:
            stat_result = os.stat(path)
        except OSError:
            return {"mtime": None, "size": None, "inode": None}
        inode = None
        if hasattr(stat_result, "st_ino"):
            inode = int(getattr(stat_result, "st_ino"))
        return {
            "mtime": stat_result.st_mtime,
            "size": int(stat_result.st_size),
            "inode": inode,
        }

    @staticmethod
    def _normalize_watchfiles_events(
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if len(events) < 2:
            return events

        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        ordered_paths: list[str] = []

        for event in events:
            path = event["path"]
            if path not in grouped:
                grouped[path] = {}
                ordered_paths.append(path)
            grouped[path][event["type"]] = event

        normalized: list[dict[str, Any]] = []
        for path in ordered_paths:
            path_events = grouped[path]
            event_types = set(path_events)
            if {"create", "remove"} <= event_types and os.path.exists(path):
                event_order = ("remove", "create", "write")
            else:
                event_order = ("create", "write", "remove")

            for event_type in event_order:
                event = path_events.get(event_type)
                if event:
                    normalized.append(event)

            for event_type, event in path_events.items():
                if event_type not in event_order:
                    normalized.append(event)

        return normalized

    @staticmethod
    def _make_watchfiles_filter(config: CreateWatchRequest) -> Callable:
        exclude = config.exclude
        include = config.include_patterns

        def watch_filter(change, path: str) -> bool:
            name = os.path.basename(path)
            for pattern in exclude:
                if fnmatch(name, pattern):
                    return False
            if include:
                return any(fnmatch(name, pat) for pat in include)
            return True

        return watch_filter

    @staticmethod
    def _normalize_request(
        req: CreateWatchRequest,
        display_path: str,
        path_obj: Path,
    ) -> CreateWatchRequest:
        if path_obj.is_file():
            return req.model_copy(
                update={
                    "path": display_path,
                    "recursive": False,
                    "exclude": [],
                    "include_patterns": [],
                }
            )
        return req.model_copy(update={"path": display_path})

    @staticmethod
    def _watch_scope(path: str, config: CreateWatchRequest) -> tuple[str, bool]:
        if os.path.isdir(path):
            return path, config.recursive
        return str(Path(path).parent), False

    @staticmethod
    def _event_matches_target(path: str, event: dict[str, Any]) -> bool:
        event_path = event["path"]
        old_path = event.get("old_path")
        if os.path.isdir(path):
            target_prefix = path + os.sep
            if event_path == path or event_path.startswith(target_prefix):
                return True
            return bool(
                old_path and (old_path == path or old_path.startswith(target_prefix))
            )
        return event_path == path or old_path == path

    @staticmethod
    def _relative_path(path: str, watch_root: str, event_path: str) -> str:
        if os.path.isdir(path):
            if event_path == path:
                return "."
            return os.path.relpath(event_path, path)
        return os.path.relpath(event_path, os.path.dirname(path))

    @staticmethod
    def _present_event_path(
        display_path: str,
        target_path: str,
        event_path: str | None,
    ) -> str | None:
        if event_path is None:
            return None
        if event_path == target_path:
            return display_path
        if os.path.isdir(target_path):
            target_prefix = target_path + os.sep
            if event_path.startswith(target_prefix):
                return os.path.join(display_path, os.path.relpath(event_path, target_path))
        return event_path

    def _check_path_allowed(self, path: str) -> None:
        if self._allowed_paths is None:
            return
        resolved = str(Path(path).resolve())
        for allowed in self._allowed_paths:
            if resolved == allowed or resolved.startswith(allowed + os.sep):
                return
        raise ValueError(
            f"path {path!r} not in allowed paths: {self._allowed_paths}"
        )

    @staticmethod
    def _resolve_allowed_paths(
        allowed_paths: list[str] | None,
    ) -> list[str] | None:
        if allowed_paths is None:
            raw_allowed_paths = os.getenv("AIO_FILE_WATCH_ALLOWED_PATHS", "").strip()
            if not raw_allowed_paths:
                return None
            raw_paths = [p.strip() for p in raw_allowed_paths.split(",")]
        else:
            raw_paths = [p.strip() for p in allowed_paths if p.strip()]

        resolved_paths = [str(Path(path).resolve()) for path in raw_paths]
        if not resolved_paths or os.sep in resolved_paths:
            return None
        return resolved_paths
