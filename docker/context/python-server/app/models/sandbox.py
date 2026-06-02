"""
Sandbox service model definitions
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class SystemEnv(BaseModel):
    os: str
    os_version: str
    arch: str
    user: str
    home_dir: str
    workspace: Optional[str] = None
    timezone: str
    occupied_ports: List[str]


class ToolSpec(BaseModel):
    """Tool specification"""

    ver: Optional[str] = None  # Version string (e.g. "Python 3.12.6")
    bin: Optional[str] = None  # Main executable path (real executable)
    alias: List[str] = Field(
        default_factory=list
    )  # Alternative executables (aliases/symlinks)


class RuntimeEnv(BaseModel):
    """version, path, etc. (python3, python3.11, python3.12, pip3, pip, uv, jupyter)"""

    python: List[ToolSpec]
    nodejs: List[ToolSpec]


class ToolsEnv(BaseModel):
    editors: List[str]
    files: List[str]
    net: List[str]
    text: List[str]
    monitor: List[str]
    image: List[str]
    media: List[str]


class AvailableTool(BaseModel):
    """
    Describe an available command-line tool.
    """

    name: str = Field(..., description='Tool’s command / binary name')
    description: Optional[str] = Field(
        '', description='Tool’s functionality description'
    )


class ToolCategory(BaseModel):
    """
    将可用工具按功能分类
    """

    category: str = Field(..., description='Name of tool category')
    tools: List[AvailableTool] = Field(
        ..., description='List of tools under this category'
    )


class SandboxDetail(BaseModel):
    """system environment info"""

    system: SystemEnv
    """language or interpreter"""
    runtime: RuntimeEnv
    """available utils"""
    utils: List[ToolCategory]


class SandboxInfo(BaseModel):
    home_dir: str
    workspace: Optional[str] = None
    version: str
    info: str
    detail: SandboxDetail


class SandboxHook(BaseModel):
    """A registered lifecycle hook."""

    name: str = Field(..., description='Unique name for this hook')
    event: str = Field('shutdown', description='Lifecycle event: "shutdown"')
    command: str = Field(..., description='Shell command to execute', max_length=4096)
    timeout: float = Field(10, description='Per-hook timeout in seconds')
    priority: int = Field(100, description='Execution priority (lower = earlier). Same priority hooks run in parallel')
    source: str = Field('api', description='Registration source: "env" or "api"')


class SandboxHookResult(BaseModel):
    """Result of executing a single lifecycle hook."""

    name: str
    command: str
    exit_code: Optional[int] = None
    stdout: str = ''
    stderr: str = ''
    duration_ms: float = 0
    timed_out: bool = False
