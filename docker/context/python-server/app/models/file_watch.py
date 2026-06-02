"""File watch models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


FileEventType = Literal['create', 'write', 'remove', 'rename', 'chmod']


class FileEvent(BaseModel):
    seq: int
    type: FileEventType
    path: str
    relative_path: str | None = None
    is_dir: bool
    timestamp: float
    old_path: str | None = None  # only for rename events
    mtime: float | None = None
    size: int | None = None
    inode: int | None = None


class WatcherCreatedResult(BaseModel):
    watcher_id: str
    path: str
    status: str = 'running'
    created_at: float
    reused: bool = False


class WatcherStoppedResult(BaseModel):
    watcher_id: str
    status: str = 'stopped'


class WatcherInfo(BaseModel):
    watcher_id: str
    path: str
    recursive: bool = True
    status: str
    subscriber_count: int = 0
    event_count: int = 0
    latest_seq: int = 0
    created_at: float


class PollResult(BaseModel):
    events: list[FileEvent]
    cursor: int
    overflow: bool = False


class WaitResult(BaseModel):
    event: FileEvent
