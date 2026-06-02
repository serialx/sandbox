"""
API request/response schemas for the /v1/bash pipe-based shell service.
"""

import uuid
from typing import Dict, Optional

from pydantic import BaseModel, Field, field_validator


def _normalize_optional_session_id(value: Optional[str]) -> Optional[str]:
    """Treat an empty-string session ID as omitted."""
    if value == '':
        return None
    return value


class BashExecRequest(BaseModel):
    """Request for POST /v1/bash/exec — execute a command."""

    session_id: Optional[str] = Field(
        None,
        description='Target session ID. If not provided or empty, a new session is created automatically. '
        'Reuse the same session_id to continue the same bash session. '
        'Only API-level session state is preserved across calls. '
        'Note: `cd` or `export` inside a command do NOT affect subsequent calls.',
    )
    command: str = Field(..., description='Shell command to execute')
    exec_dir: Optional[str] = Field(
        None,
        description='Working directory (absolute path). '
        'Takes effect on every call — if the session already exists, '
        'its default working directory is updated for subsequent calls. '
        'Use this instead of `cd` when later commands should run in a different directory.',
    )
    env: Optional[Dict[str, str]] = Field(
        None,
        description='Extra environment variables to inject for this command only. '
        'Variables exported inside the command do not persist to later calls.',
    )
    async_mode: bool = Field(
        False,
        description='If true, return immediately with running status. Use /output to poll results.',
    )
    timeout: Optional[float] = Field(
        None,
        description='HTTP timeout (seconds). Only effective when async_mode=false. '
        'If the command does not complete within this time, HTTP returns running status '
        'and the command continues in the background. Use /output to get results.',
    )
    hard_timeout: Optional[float] = Field(
        None,
        description='Hard execution timeout (seconds). When reached, the process is killed '
        'and status becomes timed_out. None means no limit.',
    )
    max_output_length: int = Field(
        50000,
        description='Maximum character length for stdout/stderr in the response. '
        'When output exceeds this limit, middle truncation is applied '
        '(head and tail preserved, middle replaced with a marker). '
        'Only effective in sync mode (async_mode=false). Set to 0 to disable truncation.',
    )

    @field_validator('session_id', mode='before')
    @classmethod
    def normalize_session_id(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_session_id(value)


class BashOutputRequest(BaseModel):
    """Request for POST /v1/bash/output — read output with offset."""

    session_id: str = Field(..., description='Target session ID')
    command_id: Optional[str] = Field(
        None,
        description='Target a specific async command. If not set, uses session-level output.',
    )
    offset: int = Field(0, description='Stdout byte offset to read from')
    stderr_offset: int = Field(0, description='Stderr byte offset to read from')
    wait: bool = Field(
        False,
        description='If true, long-poll until new output is available or wait_timeout is reached.',
    )
    wait_timeout: float = Field(
        30.0,
        description='Max seconds to wait for new output when wait=true.',
    )


class BashWriteRequest(BaseModel):
    """Request for POST /v1/bash/write — write to stdin."""

    session_id: str = Field(..., description='Target session ID')
    command_id: Optional[str] = Field(
        None,
        description='Target a specific async command. If not set, writes to current command.',
    )
    input: str = Field(..., description='Content to write to the process stdin')


class BashKillRequest(BaseModel):
    """Request for POST /v1/bash/kill — kill a session or its current command."""

    session_id: str = Field(..., description='Target session ID')
    signal: str = Field(
        'SIGTERM',
        description='Signal to send: SIGTERM, SIGKILL, or SIGINT',
    )


class BashSessionCreateRequest(BaseModel):
    """Request for POST /v1/bash/sessions/create — create a new session."""

    session_id: Optional[str] = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description='Session ID. Auto-generated if not provided or empty.',
    )
    exec_dir: Optional[str] = Field(
        None,
        description='Initial default working directory for the new session (absolute path). '
        'Later /exec calls may update it with exec_dir.',
    )
    snapshot_path: Optional[str] = Field(
        None,
        description='Path to a shell snapshot script to source on session init. '
        'This is an initialization snapshot only; command-side env changes are not written back after each exec.',
    )

    @field_validator('session_id', mode='before')
    @classmethod
    def normalize_session_id(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_session_id(value)
