"""
NodeJS service model definitions
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    """Execution status"""
    OK = 'ok'
    ERROR = 'error'
    TIMEOUT = 'timeout'


class OutputType(str, Enum):
    """Output types"""
    STREAM = 'stream'
    EXECUTE_RESULT = 'execute_result'
    ERROR = 'error'


class NodeJSOutput(BaseModel):
    """Single NodeJS output entry"""
    output_type: OutputType = Field(..., description='Type of output')
    name: Optional[str] = Field(None, description='Stream name (stdout/stderr)')
    text: Optional[str] = Field(None, description='Text output for stream')
    data: Optional[dict] = Field(None, description='Output data for execute_result')
    execution_count: Optional[int] = Field(None, description='Execution count for execute_result')
    ename: Optional[str] = Field(None, description='Error name')
    evalue: Optional[str] = Field(None, description='Error value')
    traceback: Optional[List[str]] = Field(None, description='Error traceback')


class NodeJSExecuteResult(BaseModel):
    """NodeJS code execution result model"""
    language: str = Field(default='javascript', description='Programming language')
    status: ExecutionStatus = Field(..., description='Execution status')
    execution_count: Optional[int] = Field(None, description='Execution count')
    outputs: List[NodeJSOutput] = Field(default_factory=list, description='Execution outputs')
    code: str = Field(..., description='Executed code')
    stdout: str = Field(default='', description='Standard output')
    stderr: str = Field(default='', description='Standard error')
    exit_code: int = Field(..., description='Process exit code')
    session_id: Optional[str] = Field(None, description='Session ID for stateful execution')


class NodeJSPackageInfo(BaseModel):
    """Package information"""
    name: str = Field(..., description='Package name')
    version: str = Field(..., description='Package version')


class NodeJSRuntimeInfo(BaseModel):
    """NodeJS runtime information model"""
    node_version: str = Field(..., description='Node.js version')
    npm_version: str = Field(..., description='npm version')
    supported_languages: List[str] = Field(..., description='List of supported languages')
    description: str = Field(..., description='Service description')
    runtime_directory: Optional[str] = Field(None, description='Runtime directory path')
    global_npm_directory: Optional[str] = Field(None, description='Global npm directory path')
    runtime_packages: List[NodeJSPackageInfo] = Field(default=[], description='Pre-installed runtime packages')
    global_packages: List[NodeJSPackageInfo] = Field(default=[], description='Globally installed npm packages')
    error: Optional[str] = Field(None, description='Error message if runtime info retrieval failed')
    available_versions: List[str] = Field(default=[], description='Available Node.js versions (e.g., node20, node22, node24)')
    current_version: Optional[str] = Field(None, description='Currently active Node.js version')
