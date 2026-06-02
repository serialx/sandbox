"""File watch request/response schemas."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateWatchRequest(BaseModel):
    path: str = Field(..., description='监听目录或文件路径')
    recursive: bool = Field(True, description='是否递归子目录')
    exclude: list[str] = Field(
        default=['.git', 'node_modules', '__pycache__', '.venv',
                 '*.pyc', '*.pyo', '.DS_Store', '*.swp', '*.swo'],
        description='排除的目录/glob 模式',
    )
    debounce: int = Field(300, ge=50, le=5000, description='去抖动窗口(ms)')
    include_patterns: list[str] = Field(
        default=[], description='glob 过滤，空=全部通过',
    )

    def config_hash(self) -> str:
        return hashlib.sha256(
            self.model_dump_json(exclude_none=True).encode()
        ).hexdigest()[:32]


class PollRequest(BaseModel):
    cursor: int = Field(0, ge=0, description='上次返回的游标值，只返回 seq > cursor 的事件')
    limit: int = Field(100, ge=1, le=1000, description='最多返回条数')
    timeout: int = Field(0, ge=0, le=60, description='长轮询等待秒数，0=立即返回')


class WaitRequest(BaseModel):
    model_config = ConfigDict(title='FileWatchWaitRequest')

    path: str = Field(..., description='等待的文件路径（精确匹配）')
    timeout: int = Field(30, ge=1, le=300, description='最大等待秒数')
    event_types: list[Literal['create', 'write', 'remove', 'rename', 'chmod']] = Field(
        default=['create', 'write', 'remove', 'rename', 'chmod'],
        description='关注的事件类型',
    )
