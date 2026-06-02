from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ObservationMode = Literal['off', 'guardrail', 'capture']


class ObservationCgroupSnapshot(BaseModel):
    cpu_usage_pct: float | None = None
    cpu_usage_usec: int = 0
    cpu_nr_periods: int | None = None
    cpu_nr_throttled: int | None = None
    cpu_throttled_usec: int | None = None
    mem_current_bytes: int = 0
    mem_max_bytes: int | None = None
    mem_usage_pct: float | None = None
    oom: int | None = None
    oom_kill: int | None = None
    memory_anon_bytes: int | None = None
    memory_file_bytes: int | None = None
    memory_shmem_bytes: int | None = None
    memory_slab_bytes: int | None = None


class ObservationDiskSnapshot(BaseModel):
    path: str
    used_bytes: int
    total_bytes: int
    free_bytes: int
    usage_pct: float
    inode_used: int | None = None
    inode_total: int | None = None
    inode_usage_pct: float | None = None


class ObservationProcessSnapshot(BaseModel):
    pid: int
    user: str
    comm: str
    rss_bytes: int
    rss_mib: float
    cpu_pct: float | None = None
    threads: int | None = None
    fds: int | None = None
    state: str | None = None
    uptime_sec: float | None = None
    ppid: int | None = None


class ObservationEvent(BaseModel):
    ts: datetime
    type: str
    message: str = ''
    data: dict[str, object] = Field(default_factory=dict)


class ObservationLiveSnapshot(BaseModel):
    captured_at: datetime
    mode: ObservationMode
    cgroup: ObservationCgroupSnapshot
    disk: list[ObservationDiskSnapshot] = Field(default_factory=list)
    top_processes: list[ObservationProcessSnapshot] = Field(default_factory=list)
    recent_events: list[ObservationEvent] = Field(default_factory=list)


class ObservationStatus(BaseModel):
    mode: ObservationMode
    running: bool
    session_id: str | None = None
    started_at: datetime | None = None
    ends_at: datetime | None = None
    interval_seconds: float | None = None
    last_sample_at: datetime | None = None
    runtime_dir: str | None = None
    report_count: int = 0


class ObservationStartResult(BaseModel):
    session_id: str
    mode: ObservationMode
    started_at: datetime
    ends_at: datetime | None = None
    interval_seconds: float
    runtime_dir: str


class ObservationStopResult(BaseModel):
    session_id: str
    stopped: bool
    report_ready: bool


class ObservationReportInfo(BaseModel):
    report_id: str
    session_id: str | None = None
    reason: str = 'manual'
    created_at: datetime
    path: str
    size_bytes: int


class ObservationExportResult(ObservationReportInfo):
    pass
