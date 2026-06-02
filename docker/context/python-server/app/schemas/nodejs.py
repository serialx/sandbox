from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal


class NodeJSExecuteRequest(BaseModel):
    code: str = Field(..., description='JavaScript code to execute')
    timeout: Optional[int] = Field(
        default=30, description='Execution timeout in seconds', ge=1, le=300
    )
    stdin: Optional[str] = Field(
        default=None, description='Standard input for the process'
    )
    files: Optional[Dict[str, str]] = Field(
        default=None, description='Additional files to create in execution directory'
    )
    stateful: Optional[bool] = Field(
        default=False,
        description='Enable stateful execution with persistent REPL session',
    )
    session_id: Optional[str] = Field(
        default=None,
        description='Session ID for stateful execution (reuse existing session)',
    )
    cwd: Optional[str] = Field(
        default=None, description='Working directory for code execution'
    )
    version: Optional[str] = Field(
        default=None,
        description='Node.js version to use: "node20", "node22", "node24", or aliases "20", "22", "24"',
    )


class NodeJSOutput(BaseModel):
    output_type: str = Field(
        ..., description='Type of output: stream, error, or execute_result'
    )
    name: Optional[str] = Field(
        default=None, description='Stream name (stdout/stderr) for stream outputs'
    )
    text: Optional[str] = Field(
        default=None, description='Text content for stream outputs'
    )
    ename: Optional[str] = Field(
        default=None, description='Error name for error outputs'
    )
    evalue: Optional[str] = Field(
        default=None, description='Error value for error outputs'
    )
    traceback: Optional[List[str]] = Field(
        default=None, description='Error traceback for error outputs'
    )
    data: Optional[Dict[str, Any]] = Field(
        default=None, description='Data for execute_result outputs'
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description='Metadata for outputs'
    )


class NodeJSExecuteResponse(BaseModel):
    language: Literal['javascript'] = Field(
        ..., description="Language that was executed (always 'javascript')"
    )
    status: str = Field(..., description='Execution status: ok, error, or timeout')
    execution_count: Optional[int] = Field(default=None, description='Execution count')
    outputs: List[NodeJSOutput] = Field(
        default=[], description='List of execution outputs'
    )
    code: str = Field(..., description='Code that was executed')
    stdout: str = Field(default='', description='Standard output')
    stderr: str = Field(default='', description='Standard error')
    exit_code: int = Field(..., description='Process exit code')
    session_id: Optional[str] = Field(
        default=None,
        description='Session ID for stateful execution (use this to continue the session)',
    )


# Session CRUD schemas


class NodeJSCreateSessionRequest(BaseModel):
    session_id: Optional[str] = Field(
        default=None, description='Custom session ID (auto-generated if not provided)'
    )
    cwd: Optional[str] = Field(
        default=None, description='Working directory for the session'
    )
    max_idle_time: Optional[int] = Field(
        default=86400, description='Maximum idle time in seconds (default 24 hours)', ge=60, le=86400
    )


class NodeJSUpdateSessionRequest(BaseModel):
    max_idle_time: Optional[int] = Field(
        default=None, description='New maximum idle time in seconds', ge=60, le=86400
    )
    cwd: Optional[str] = Field(
        default=None, description='New working directory'
    )


class NodeJSSessionInfo(BaseModel):
    session_id: str = Field(..., description='Session ID')
    cwd: str = Field(..., description='Working directory')
    created_at: float = Field(..., description='Session creation timestamp (ms since epoch)')
    last_used: float = Field(..., description='Last activity timestamp (ms since epoch)')
    max_idle_time: int = Field(..., description='Maximum idle time in milliseconds')
    age_seconds: int = Field(..., description='Seconds since last activity')
    state: str = Field(..., description='Session state: IDLE or EXECUTING')


class NodeJSSessionListResponse(BaseModel):
    sessions: Dict[str, NodeJSSessionInfo] = Field(
        default={}, description='Map of session ID to session info'
    )


class NodeJSSessionResponse(BaseModel):
    session: NodeJSSessionInfo = Field(..., description='Session information')


class NodeJSCreateSessionResponse(BaseModel):
    session_id: str = Field(..., description='Session ID')
    created: bool = Field(..., description='Whether the session was newly created')
    message: Optional[str] = Field(
        default=None, description='Additional message (e.g., if session already exists)'
    )
    session: Optional[NodeJSSessionInfo] = Field(
        default=None, description='Session information (if created)'
    )


class NodeJSUpdateSessionResponse(BaseModel):
    updated: bool = Field(..., description='Whether the update was successful')
    session: NodeJSSessionInfo = Field(..., description='Updated session information')


class NodeJSDeleteSessionResponse(BaseModel):
    deleted: bool = Field(..., description='Whether the session was deleted')
