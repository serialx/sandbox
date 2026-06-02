"""Recursive inotify watcher with debounce and rename cookie pairing.

Linux-only. Uses asyncinotify for native asyncio integration with inotify.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from fnmatch import fnmatch
from typing import Any, AsyncIterator

from asyncinotify import Event, Inotify, Mask

logger = logging.getLogger(__name__)

WATCH_MASK = (
    Mask.CREATE | Mask.MODIFY | Mask.DELETE
    | Mask.MOVED_FROM | Mask.MOVED_TO | Mask.ATTRIB
    | Mask.DELETE_SELF | Mask.MOVE_SELF | Mask.IGNORED
)


class InotifyWatcher:
    """Recursive inotify watcher yielding batches of event dicts.

    Event dict keys: type, path, old_path (renamed only), is_dir.
    Types: created, modified, deleted, renamed, chmod.
    """

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
        self._inotify: Inotify | None = None
        self._root_is_dir = os.path.isdir(path)
        self._pending_moves: dict[int, tuple[float, str, bool]] = {}
        self._move_timeout = 0.05  # 50ms to pair MOVED_FROM with MOVED_TO
        self._stop_requested = False
        self._root_remove_emitted = False

    async def watch(self) -> AsyncIterator[list[dict[str, Any]]]:
        """Async generator yielding batches of events after debounce."""
        self._inotify = Inotify()
        try:
            self._add_watches(self._path)
            batch: list[dict[str, Any]] = []

            async for event in self._inotify:
                processed = self._process_event(event)
                if processed:
                    batch.extend(processed)

                timed_out = self._flush_pending_moves()
                if timed_out:
                    batch.extend(timed_out)

                # Debounce: try to read more events within the window
                if batch:
                    try:
                        while True:
                            event2 = await asyncio.wait_for(
                                self._inotify.__anext__(), timeout=self._debounce
                            )
                            processed = self._process_event(event2)
                            if processed:
                                batch.extend(processed)
                            timed_out = self._flush_pending_moves()
                            if timed_out:
                                batch.extend(timed_out)
                    except (asyncio.TimeoutError, StopAsyncIteration):
                        pass
                    # Flush any remaining pending moves
                    timed_out = self._flush_pending_moves()
                    if timed_out:
                        batch.extend(timed_out)
                    if batch:
                        batch = self._dedupe_create_events(batch)
                        yield batch
                        batch = []
                    if self._stop_requested:
                        break
        finally:
            if self._inotify:
                self._inotify.close()

    def _add_watches(
        self,
        root: str,
        *,
        snapshot_existing: bool = False,
    ) -> list[dict[str, Any]]:
        snapshot_events: list[dict[str, Any]] = []
        pending_dirs = [root]
        seen_dirs: set[tuple[int, int]] = set()

        assert self._inotify is not None

        while pending_dirs:
            current = pending_dirs.pop()
            if self._is_excluded(os.path.basename(current)) and current != self._path:
                continue

            try:
                stat_result = os.stat(current, follow_symlinks=False)
            except OSError:
                continue
            dir_key = (stat_result.st_dev, stat_result.st_ino)
            if dir_key in seen_dirs:
                continue
            seen_dirs.add(dir_key)

            try:
                self._inotify.add_watch(current, WATCH_MASK)
            except OSError as e:
                logger.warning("Cannot watch %s: %s", current, e)
                continue
            if not self._recursive:
                continue

            try:
                with os.scandir(current) as entries:
                    dir_entries = sorted(entries, key=lambda entry: entry.name)
            except OSError:
                continue

            child_dirs: list[str] = []
            for entry in dir_entries:
                name = entry.name
                if self._is_excluded(name):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue

                if snapshot_existing and self._is_included(name):
                    snapshot_events.append(
                        {
                            "type": "create",
                            "path": entry.path,
                            "is_dir": is_dir,
                        }
                    )
                if is_dir:
                    child_dirs.append(entry.path)

            pending_dirs.extend(reversed(child_dirs))

        return snapshot_events

    def _process_event(self, event: Event) -> list[dict[str, Any]]:
        name = event.name
        watch_path = str(event.watch.path) if event.watch else ""
        full_path = watch_path if name is None else os.path.join(watch_path, str(name))
        is_dir = bool(event.mask & Mask.ISDIR) or (
            name is None and watch_path == self._path and self._root_is_dir
        )
        basename = os.path.basename(full_path)

        if name is None:
            if watch_path == self._path and event.mask & (
                Mask.DELETE_SELF | Mask.MOVE_SELF | Mask.IGNORED
            ):
                self._stop_requested = True
                if self._should_emit_root_remove(event.mask):
                    self._root_remove_emitted = True
                    return [{"type": "remove", "path": self._path, "is_dir": is_dir}]
                return []
            if watch_path != self._path:
                return []
            return []

        if self._is_excluded(basename):
            return []
        if not self._is_included(basename):
            return []

        results: list[dict[str, Any]] = []

        if event.mask & Mask.CREATE:
            results.append({"type": "create", "path": full_path, "is_dir": is_dir})
            if is_dir and self._recursive:
                results.extend(
                    self._add_watches(full_path, snapshot_existing=True)
                )

        if event.mask & Mask.MODIFY:
            results.append({"type": "write", "path": full_path, "is_dir": is_dir})

        if event.mask & Mask.DELETE:
            results.append({"type": "remove", "path": full_path, "is_dir": is_dir})

        if event.mask & Mask.MOVED_FROM:
            self._pending_moves[event.cookie] = (time.monotonic(), full_path, is_dir)

        if event.mask & Mask.MOVED_TO:
            snapshot_children = False
            if event.cookie in self._pending_moves:
                _, old_path, _ = self._pending_moves.pop(event.cookie)
                results.append({
                    "type": "rename", "path": full_path,
                    "old_path": old_path, "is_dir": is_dir,
                })
            else:
                results.append({"type": "create", "path": full_path, "is_dir": is_dir})
                snapshot_children = True
            if is_dir and self._recursive:
                results.extend(
                    self._add_watches(
                        full_path,
                        snapshot_existing=snapshot_children,
                    )
                )

        if event.mask & Mask.ATTRIB:
            if not (event.mask & (Mask.CREATE | Mask.MODIFY | Mask.DELETE | Mask.MOVED_FROM | Mask.MOVED_TO)):
                results.append({"type": "chmod", "path": full_path, "is_dir": is_dir})

        return results

    def _flush_pending_moves(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        expired = [c for c, (ts, _, _) in self._pending_moves.items() if now - ts > self._move_timeout]
        results = []
        for cookie in expired:
            _, path, is_dir = self._pending_moves.pop(cookie)
            results.append({"type": "remove", "path": path, "is_dir": is_dir})
        return results

    def _is_excluded(self, name: str) -> bool:
        return any(fnmatch(name, pat) for pat in self._exclude)

    def _is_included(self, name: str) -> bool:
        return not self._include or any(fnmatch(name, pat) for pat in self._include)

    @staticmethod
    def _dedupe_create_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen_create_paths: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for event in events:
            if event["type"] == "create":
                path = event["path"]
                if path in seen_create_paths:
                    continue
                seen_create_paths.add(path)
            deduped.append(event)
        return deduped

    def _should_emit_root_remove(self, mask: Mask) -> bool:
        if self._root_remove_emitted:
            return False
        if mask & (Mask.DELETE_SELF | Mask.MOVE_SELF):
            return True
        return bool(mask & Mask.IGNORED and not os.path.exists(self._path))
