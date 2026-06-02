from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DisplayRecordRequest(BaseModel):
    action: Literal['start', 'stop', 'status'] = Field(
        title='Display Record Action',
        description='Recording action: start, stop, or status'
    )
    save_path: str | None = Field(
        default=None,
        description='Output file path (default: /tmp/recordings/recording_{timestamp}.mp4)',
    )
    fps: int = Field(default=5, gt=0, le=30, description='Frames per second')
    crf: int = Field(
        default=28, ge=0, le=51, description='H.264 CRF quality (0=lossless, 51=worst)'
    )
    max_duration: float = Field(
        default=600.0, gt=0, description='Max recording duration in seconds'
    )
    width: int | None = Field(
        default=None, gt=0, description='Video width in pixels (auto-detected from X11 if omitted)'
    )
    height: int | None = Field(
        default=None, gt=0, description='Video height in pixels (auto-detected from X11 if omitted)'
    )


class DisplayRecordResult(BaseModel):
    status: Literal['idle', 'recording', 'stopped']
    save_path: str | None = None
    duration: float = 0.0
    file_size_bytes: int | None = None
