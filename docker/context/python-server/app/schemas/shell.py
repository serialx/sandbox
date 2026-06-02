import uuid
from typing import Optional

from pydantic import BaseModel, Field


class ShellExecRequest(BaseModel):
    """Shell command execution request model"""

    id: Optional[str] = Field(
        None,
        description='Unique identifier of the target shell session, if not provided, one will be automatically created',
    )
    exec_dir: Optional[str] = Field(
        None,
        description='Working directory for command execution (must use absolute path)',
    )
    command: str = Field(..., description='Shell command to execute')
    async_mode: bool = Field(
        False,
        description='Whether to execute command asynchronously (default: False for async, False for synchronous execution)',
    )
    timeout: Optional[float] = Field(
        None,
        description='Maximum time (seconds) to wait for command completion before returning running status',
    )
    strict: Optional[bool] = Field(
        None,
        description='Strict mode for working directory validation. If True, returns error when working directory does not exist. If False or None, silently falls back to session working directory.',
    )
    no_change_timeout: Optional[int] = Field(
        None,
        description='Timeout (seconds) for detecting no new output from a command. If no output change is detected within this time, command returns with NO_CHANGE_TIMEOUT status. Overrides session-level setting for this command only.',
    )
    hard_timeout: Optional[float] = Field(
        None,
        description='Hard timeout (seconds) for command execution. When reached, the command is forcefully stopped and current console output is returned with HARD_TIMEOUT status. Unlike timeout (which only affects HTTP response timing), this actually terminates the command.',
    )
    preserve_symlinks: Optional[bool] = Field(
        default=False,
        description='If True, preserve symlinks in working directory path (pwd shows symlink path). If False, symlinks are resolved to physical paths. Defaults to False for backward compatibility.',
    )
    truncate: bool = Field(
        default=True,
        description='If True, truncate output when it exceeds 30000 characters (default: True)',
    )


class ShellViewRequest(BaseModel):
    """Shell session content view request model"""

    id: str = Field(..., description='Unique identifier of the target shell session')


class ShellWaitRequest(BaseModel):
    """Shell process wait request model"""

    id: str = Field(..., description='Unique identifier of the target shell session')
    seconds: Optional[int] = Field(None, description='Wait time (seconds)')
    max_wait_seconds: Optional[int] = Field(
        None, description='Maximum wait time (seconds) for the command to complete'
    )


class ShellWriteToProcessRequest(BaseModel):
    """Request model for writing input to a running process"""

    id: str = Field(..., description='Unique identifier of the target shell session')
    input: str = Field(..., description='Input content to write to the process')
    press_enter: bool = Field(..., description='Whether to press enter key after input')


class ShellKillProcessRequest(BaseModel):
    """Request model for terminating a running process"""

    id: str = Field(..., description='Unique identifier of the target shell session')


class ShellSessionStats(BaseModel):
    """Shell session statistics model"""

    total_sessions: int = Field(..., description='Total number of sessions')
    active_sessions: int = Field(
        ..., description='Number of active sessions (used within last 5 minutes)'
    )
    idle_sessions: int = Field(..., description='Number of idle sessions')
    max_sessions: int = Field(..., description='Maximum allowed sessions')
    session_timeout: int = Field(..., description='Session timeout in seconds')
    usage_ratio: float = Field(..., description='Session usage ratio (0.0 to 1.0)')


class ShellCreateSessionRequest(BaseModel):
    """Shell session creation request model"""

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description='Unique identifier for the shell session, auto-generated if not provided',
    )
    exec_dir: Optional[str] = Field(
        None,
        description='Working directory for the new session (must use absolute path)',
    )
    no_change_timeout: Optional[int] = Field(
        None,
        description='Timeout (seconds) for detecting no new output from commands in this session. Default is 120 seconds. If no output change is detected within this time, command returns with NO_CHANGE_TIMEOUT status.',
    )

    preserve_symlinks: Optional[bool] = Field(
        default=False,
        description='If True, preserve symlinks in working directory path (pwd shows symlink path). If False, symlinks are resolved to physical paths. Defaults to False for backward compatibility.',
    )


class ShellCreateSessionResponse(BaseModel):
    """Shell session creation response model"""

    session_id: str = Field(
        ..., description='Unique identifier of the created shell session'
    )
    working_dir: str = Field(
        ..., description='Working directory of the created session'
    )


class ShellUpdateSessionRequest(BaseModel):
    """Shell session update request model"""

    id: str = Field(..., description='Unique identifier of the target shell session')
    no_change_timeout: Optional[int] = Field(
        None,
        description='New timeout (seconds) for detecting no new output from commands. If no output change is detected within this time, command returns with NO_CHANGE_TIMEOUT status.',
    )
