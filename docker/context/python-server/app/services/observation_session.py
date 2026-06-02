from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


CaptureMode = Literal['guardrail', 'capture']


@dataclass
class ObservationSession:
    session_id: str
    mode: CaptureMode
    started_at: datetime
    ends_at: datetime | None
    stopped_at: datetime | None
    interval_seconds: float
    include_processes: bool
    include_disk: bool
    runtime_dir: Path
    idempotency_key: str | None
    request_fingerprint: str | None
