"""Display — abstract base class for system-level GUI operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from browser_sdk.models import OperationContext
from browser_sdk.result import Result


class Display(ABC):
    """
    System-level GUI controller operating on the entire screen.

    Coordinates are in absolute screen pixels. Use this for:
    - Non-browser UI interactions (native dialogs, taskbar)
    - CAPTCHA manual solving via VNC
    - Full-screen screenshots (including browser chrome)
    """

    @property
    @abstractmethod
    def vnc_endpoint(self) -> str:
        """VNC/WebRTC live preview URL."""

    # --- Mouse ---

    @abstractmethod
    async def click(
        self,
        x: int,
        y: int,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def double_click(
        self,
        x: int,
        y: int,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def right_click(
        self,
        x: int,
        y: int,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def move_to(
        self,
        x: int,
        y: int,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def drag(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def scroll(
        self,
        dx: int = 0,
        dy: int = 0,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def mouse_down(
        self,
        x: int,
        y: int,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def mouse_up(
        self,
        x: int,
        y: int,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    # --- Keyboard ---

    @abstractmethod
    async def type(
        self,
        text: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        """Simulate keyboard text input at system level."""

    @abstractmethod
    async def press(
        self,
        key: str,
        duration_ms: int = 300,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        """Simulate key press: keyDown → delay → keyUp."""

    @abstractmethod
    async def hot_key(
        self,
        keys: list[str],
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        """Simulate hotkey combination."""

    # --- Screenshot ---

    @abstractmethod
    async def screenshot(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[str]:
        """Full-screen screenshot, returns base64 encoded image."""
