from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class Language(str, Enum):
    """Supported programming languages for code execution"""

    PYTHON = 'python'
    JAVASCRIPT = 'javascript'


class CodeExecuteRequest(BaseModel):
    """Unified code execution request model"""

    language: Language = Field(..., description='Target runtime language')
    code: str = Field(..., description='Source code to execute')
    timeout: Optional[int] = Field(
        default=None, description='Execution timeout in seconds', ge=1, le=300
    )
    cwd: Optional[str] = Field(
        None, description='Current working directory for code execution'
    )
    stateful: bool = Field(
        default=False,
        description='Enable stateful execution using Jupyter kernel. '
        'When True, variables and state persist across requests with the same session_id.',
    )
    session_id: Optional[str] = Field(
        default=None,
        description='Session ID for stateful execution. '
        'Required when stateful=True to maintain state across requests. '
        'Auto-generated if not provided.',
    )


class CodeExecuteResponse(BaseModel):
    """Unified code execution response model"""

    language: Language = Field(
        ..., description='Runtime language that executed the code'
    )
    status: str = Field(..., description='Execution status indicator')
    outputs: List[Dict[str, object]] = Field(
        default_factory=list, description='Structured execution outputs'
    )
    code: str = Field(..., description='Echo of executed code')
    stdout: Optional[str] = Field(
        default=None, description='Captured standard output stream'
    )
    stderr: Optional[str] = Field(
        default=None, description='Captured standard error stream'
    )
    exit_code: Optional[int] = Field(
        default=None, description='Process exit code when applicable'
    )
    traceback: Optional[List[str]] = Field(
        default=None, description='Captured error traceback lines when available'
    )
    session_id: Optional[str] = Field(
        default=None,
        description='Session ID for stateful execution (only present when stateful=True)',
    )


class CodeLanguageInfo(BaseModel):
    """Metadata about a supported code runtime"""

    language: Language = Field(..., description='Supported language identifier')
    description: str = Field(..., description='Human readable runtime description')
    runtime_version: Optional[str] = Field(
        default=None, description='Primary runtime version identifier'
    )
    default_timeout: int = Field(
        30, description='Default timeout in seconds', ge=1, le=300
    )
    max_timeout: int = Field(
        300, description='Maximum allowed timeout in seconds', ge=1, le=300
    )
    details: Dict[str, object] = Field(
        default_factory=dict, description='Additional runtime specific metadata'
    )


class CodeInfoResponse(BaseModel):
    """Unified code information response"""

    languages: List[CodeLanguageInfo] = Field(
        ..., description='List of supported languages and metadata'
    )
