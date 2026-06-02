"""
Pipe-based Bash service data models.

Process-per-command model: each exec spawns a new process.
Env and cwd are isolated per command — use `env` param and `exec_dir` for control.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


COMMAND_STATUS_DESCRIPTION = (
    'Command execution status. Possible values: '
    '`pending` (accepted but not started yet; typically internal-only), '
    '`running` (still executing), '
    '`completed` (process exited and `exit_code` is available; a non-zero shell exit code still uses `completed`), '
    '`timed_out` (process was force-killed after `hard_timeout`), '
    '`killed` (process was terminated by kill/cleanup or an internal execution failure).'
)

SESSION_STATUS_DESCRIPTION = (
    'Session status. Possible values: '
    '`ready` (session can accept commands), '
    '`closed` (session is closed and cannot accept new commands).'
)


class CommandStatus(str, Enum):
    """Status of a bash command execution."""

    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    TIMED_OUT = 'timed_out'
    KILLED = 'killed'


class SessionStatus(str, Enum):
    """Status of a pipe bash session."""

    READY = 'ready'
    CLOSED = 'closed'


@dataclass
class BashCommand:
    """Tracks a single command execution within a session."""

    id: str
    command: str
    status: CommandStatus = CommandStatus.PENDING
    exit_code: Optional[int] = None
    proc: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Physical buffer offsets at command start (set inside _exec_lock)
    stdout_start: int = 0
    stderr_start: int = 0


class BashCommandInfo(BaseModel):
    """Serializable command status info (for API responses)."""

    command_id: str = Field(..., description='Unique command identifier')
    command: str = Field(..., description='The command string')
    status: CommandStatus = Field(..., description=COMMAND_STATUS_DESCRIPTION)
    exit_code: Optional[int] = Field(None, description='Exit code (when completed)')


class BashExecResult(BaseModel):
    """Result of exec_command — returned by POST /v1/bash/exec."""

    session_id: str = Field(..., description='Session identifier')
    command_id: str = Field(..., description='Unique command identifier')
    command: str = Field(..., description='The executed command')
    status: CommandStatus = Field(..., description=COMMAND_STATUS_DESCRIPTION)
    stdout: Optional[str] = Field(None, description='Stdout output up to this point')
    stderr: Optional[str] = Field(None, description='Stderr output up to this point')
    exit_code: Optional[int] = Field(None, description='Exit code (when completed)')
    offset: int = Field(0, description='Current stdout offset for subsequent /output calls')
    stderr_offset: int = Field(0, description='Current stderr offset')


class BashOutputResult(BaseModel):
    """Result of get_output — returned by POST /v1/bash/output."""

    session_id: str = Field(..., description='Session identifier')
    stdout: str = Field('', description='New stdout data since last offset')
    stderr: str = Field('', description='New stderr data since last offset')
    offset: int = Field(0, description='Current stdout offset (use for next request)')
    stderr_offset: int = Field(0, description='Current stderr offset (use for next request)')
    command: Optional[BashCommandInfo] = Field(
        None, description='Current or most recent command status'
    )


class BashSessionInfo(BaseModel):
    """Session info for listing — returned by GET /v1/bash/sessions."""

    session_id: str = Field(..., description='Session identifier')
    status: SessionStatus = Field(..., description=SESSION_STATUS_DESCRIPTION)
    working_dir: str = Field(..., description='Working directory')
    created_at: datetime = Field(..., description='Creation timestamp')
    last_used_at: datetime = Field(..., description='Last used timestamp')
    current_command: Optional[str] = Field(
        None, description='Currently executing command'
    )
    command_count: int = Field(0, description='Total commands executed')
