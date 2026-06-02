"""
Shell business model definitions
"""

from __future__ import annotations

import asyncio
from enum import Enum
from datetime import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from vendors.openhands.runtime.utils.bash import BashSession


class BashCommandStatus(Enum):
    """Shell command execution status (compatible with OpenHands)"""

    RUNNING = 'running'  # 命令执行中
    COMPLETED = 'completed'  # 命令执行完成
    NO_CHANGE_TIMEOUT = 'no_change_timeout'  # 无输出超时
    HARD_TIMEOUT = 'hard_timeout'  # 硬超时
    TERMINATED = 'terminated'  # 会话已终止


class ConsoleRecord(BaseModel):
    """Shell command console record model"""

    ps1: str = Field(..., description='Command prompt')
    command: str = Field(..., description='Executed command')
    output: str = Field(default='', description='Command output')


class ShellTask(BaseModel):
    """Shell task model"""

    id: str = Field(..., description='Task unique identifier')
    command: str = Field(..., description='Executed command')
    status: BashCommandStatus = Field(..., description='Command execution status')
    created_at: str = Field(..., description='Task creation time')
    output: Optional[str] = Field(None, description='Task output')


class ShellCommandResult(BaseModel):
    """Shell command execution result model"""

    session_id: str = Field(..., description='Shell session ID')
    command: str = Field(..., description='Executed command')
    status: BashCommandStatus = Field(..., description='Command execution status')
    output: Optional[str] = Field(
        None,
        description='Command execution output, only has value when status is completed',
    )
    console: Optional[List[ConsoleRecord]] = Field(
        None, description='Console command records'
    )
    exit_code: Optional[int] = Field(
        None,
        description='Command execution exit code, only has value when status is completed',
    )


class ShellViewResult(BaseModel):
    """Shell session content view result model"""

    output: str = Field(..., description='Shell session output content')
    session_id: str = Field(..., description='Shell session ID')
    console: Optional[List[ConsoleRecord]] = Field(
        None, description='Console command records'
    )
    status: BashCommandStatus = Field(..., description='Shell session status')
    command: Optional[str] = Field(
        None, description='Last executed or currently executing command'
    )
    exit_code: Optional[int] = Field(
        None,
        description='Command execution exit code, only has value when status is completed',
    )


class ShellWaitResult(BaseModel):
    """Process wait result model"""

    status: BashCommandStatus = Field(..., description='Process status')


class ShellWriteResult(BaseModel):
    """Process input write result model"""

    status: BashCommandStatus = Field(..., description='Write status')


class ShellKillResult(BaseModel):
    """Process termination result model"""

    status: BashCommandStatus = Field(..., description='Process status')
    exit_code: Optional[int] = Field(
        None,
        description='Process exit code before termination, None if process was still running',
    )
    # Backward compatibility alias for exit_code
    returncode: Optional[int] = Field(
        None,
        description='Deprecated: use exit_code instead. Kept for backward compatibility.',
    )

    def __init__(self, **data):
        # If only exit_code is provided, copy it to returncode for backward compatibility
        if 'exit_code' in data and 'returncode' not in data:
            data['returncode'] = data['exit_code']
        super().__init__(**data)


@dataclass
class OpenHandsShellSession:
    """OpenHands Shell Session data class"""

    id: str
    working_dir: str
    created_at: datetime = field(default_factory=datetime.now)
    last_used_at: datetime = field(default_factory=datetime.now)
    active: bool = True
    status: BashCommandStatus = BashCommandStatus.RUNNING

    # OpenHands BashSession 实例
    bash_session: Optional['BashSession'] = None

    # 当前执行的命令和最近一次命令
    current_command: Optional[str] = None
    current_command_id: Optional[str] = None
    last_command: Optional[str] = None
    last_command_id: Optional[str] = None
    exit_code: Optional[int] = None
    active_command_count: int = 0

    # 输出缓冲和订阅者 (兼容现有API)
    output_buffer: List[str] = field(default_factory=list)
    output_text: str = ''
    console_records: List[ConsoleRecord] = field(default_factory=list)
    command_outputs: Dict[str, str] = field(default_factory=dict)
    command_statuses: Dict[str, BashCommandStatus] = field(default_factory=dict)
    command_exit_codes: Dict[str, Optional[int]] = field(default_factory=dict)
    command_texts: Dict[str, str] = field(default_factory=dict)

    # WebSocket 订阅者
    char_subscribers: Dict[str, asyncio.Queue] = field(default_factory=dict)
    line_subscribers: Dict[str, Any] = field(default_factory=dict)

    # 同步锁
    _lock: Optional[asyncio.Lock] = field(default=None, repr=False, compare=False)
    _lock_loop: Optional[asyncio.AbstractEventLoop] = field(
        default=None, repr=False, compare=False
    )
    # 执行锁：防止同一 session 上的并发命令执行
    _exec_lock: Optional[asyncio.Lock] = field(default=None, repr=False, compare=False)
    _exec_lock_loop: Optional[asyncio.AbstractEventLoop] = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self):
        """初始化后处理"""
        # Asyncio primitives are bound lazily so sessions can safely move
        # between test event loops while reusing the same service singleton.

    def get_lock(self) -> asyncio.Lock:
        """Get a state lock bound to the current running loop."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def get_exec_lock(self) -> asyncio.Lock:
        """Get an execution lock bound to the current running loop."""
        loop = asyncio.get_running_loop()
        if self._exec_lock is None or self._exec_lock_loop is not loop:
            self._exec_lock = asyncio.Lock()
            self._exec_lock_loop = loop
        return self._exec_lock

    @property
    def has_active_command(self) -> bool:
        """Whether the session has an executing or queued command."""
        return self.active_command_count > 0

    async def add_output(self, output: str):
        """添加输出到缓冲区"""
        async with self.get_lock():
            self.output_buffer.append(output)
            self.output_text += output
            # 通知所有字符订阅者
            for subscriber_id, queue in self.char_subscribers.items():
                try:
                    await queue.put(output)
                except asyncio.QueueFull:
                    pass  # 忽略队列满的情况

    async def subscribe_char(self, subscriber_id: str) -> Optional[asyncio.Queue]:
        """订阅字符输出"""
        if subscriber_id in self.char_subscribers:
            return None  # 已存在

        queue = asyncio.Queue(maxsize=1000)
        self.char_subscribers[subscriber_id] = queue
        return queue

    async def unsubscribe_char(self, subscriber_id: str):
        """取消字符订阅"""
        self.char_subscribers.pop(subscriber_id, None)


class ShellSessionInfo(BaseModel):
    """Shell session information"""

    working_dir: str = Field(..., description='Working directory')
    created_at: datetime = Field(..., description='Creation timestamp')
    last_used_at: datetime = Field(..., description='Last used timestamp')
    age_seconds: int = Field(..., description='Age of session in seconds')
    status: str = Field(..., description='Session status')
    current_command: Optional[str] = Field(
        None, description='Currently executing command'
    )


class ActiveShellSessionsResult(BaseModel):
    """Active shell sessions result"""

    sessions: Dict[str, ShellSessionInfo] = Field(
        ..., description='Map of session ID to session info'
    )
