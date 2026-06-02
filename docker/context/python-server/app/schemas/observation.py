from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ObserveStartRequest(BaseModel):
    mode: Literal['guardrail', 'capture'] = 'capture'
    idempotency_key: str | None = Field(
        None,
        description='Optional key for idempotent retries of the same start request',
        max_length=128,
    )
    duration_seconds: int | None = Field(
        None, description='Optional session duration in seconds'
    )
    interval_seconds: float | None = Field(
        None, description='Sampling interval in seconds'
    )
    include_processes: bool | None = Field(
        None, description='Whether to sample process rows'
    )
    include_disk: bool = Field(True, description='Whether to sample disk usage')


class ObserveStopRequest(BaseModel):
    session_id: str | None = Field(
        None, description='Optional active session id to stop'
    )


class ObserveExportRequest(BaseModel):
    idempotency_key: str | None = Field(
        None,
        description='Optional key for idempotent retries of the same export request',
        max_length=128,
    )
    session_id: str | None = Field(
        None, description='Optional session id to export'
    )
    reason: str = Field('manual', description='Export reason')
