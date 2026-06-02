"""LocalDisplay — PyAutoGUI-based system-level GUI operations."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from typing import Any

from browser_sdk.display.base import Display
from browser_sdk.errors import E_INPUT_FAILED, E_INPUT_SCREENSHOT_FAILED
from browser_sdk.models import OperationContext
from browser_sdk.result import Result

logger = logging.getLogger(__name__)


class LocalDisplay(Display):
    """
    Display implementation that directly calls PyAutoGUI on the local machine.

    Used when the SDK runs inside the sandbox container.
    """

    def __init__(self, vnc_url: str = "") -> None:
        self._vnc_url = vnc_url
        self._gui = None  # lazy import

    def _ensure_gui(self):
        if self._gui is None:
            import pyautogui

            pyautogui.FAILSAFE = False
            self._gui = pyautogui

    @property
    def vnc_endpoint(self) -> str:
        return self._vnc_url

    async def click(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            await asyncio.to_thread(self._gui.click, x, y)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def double_click(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            await asyncio.to_thread(self._gui.doubleClick, x, y)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def right_click(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            await asyncio.to_thread(self._gui.rightClick, x, y)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def move_to(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            await asyncio.to_thread(self._gui.moveTo, x, y)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def drag(
        self, from_x: int, from_y: int, to_x: int, to_y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            await asyncio.to_thread(self._gui.moveTo, from_x, from_y)
            await asyncio.to_thread(
                self._gui.drag, to_x - from_x, to_y - from_y, duration=0.5
            )
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def scroll(
        self, dx: int = 0, dy: int = 0,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            if dy != 0:
                await asyncio.to_thread(self._gui.scroll, dy)
            if dx != 0:
                await asyncio.to_thread(self._gui.hscroll, dx)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def mouse_down(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            await asyncio.to_thread(self._gui.moveTo, x, y)
            await asyncio.to_thread(self._gui.mouseDown)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def mouse_up(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            await asyncio.to_thread(self._gui.moveTo, x, y)
            await asyncio.to_thread(self._gui.mouseUp)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def type(
        self, text: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            # Use clipboard for reliable Unicode support
            import pyperclip

            await asyncio.to_thread(pyperclip.copy, text)
            key = "command" if os.uname().sysname == "Darwin" else "ctrl"
            await asyncio.to_thread(self._gui.hotkey, key, "v")
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def press(
        self, key: str, duration_ms: int = 300,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            await asyncio.to_thread(self._gui.keyDown, key)
            await asyncio.sleep(duration_ms / 1000.0)
            await asyncio.to_thread(self._gui.keyUp, key)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def hot_key(
        self, keys: list[str],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            self._ensure_gui()
            await asyncio.to_thread(self._gui.hotkey, *keys)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def screenshot(
        self,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[str]:
        try:
            self._ensure_gui()
            img = await asyncio.to_thread(self._gui.screenshot)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return Result.ok(b64)
        except Exception as e:
            return Result.fail(E_INPUT_SCREENSHOT_FAILED, str(e))
