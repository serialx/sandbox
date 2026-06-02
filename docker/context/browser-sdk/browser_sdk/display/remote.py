"""RemoteDisplay — HTTP-client-based display operations against a sandbox API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from browser_sdk.display.base import Display
from browser_sdk.errors import E_INPUT_FAILED, E_INPUT_SCREENSHOT_FAILED
from browser_sdk.models import OperationContext
from browser_sdk.result import Result

logger = logging.getLogger(__name__)


class RemoteDisplay(Display):
    """
    Display implementation that forwards GUI operations to a remote sandbox API.

    Used when the SDK runs outside the sandbox container.
    """

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")

    async def _post(self, path: str, body: dict) -> dict:
        resp = await self._client.post(f"{self._base}{path}", json=body)
        resp.raise_for_status()
        return resp.json()

    @property
    def vnc_endpoint(self) -> str:
        return f"{self._base}/vnc"

    async def click(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/click", {"x": x, "y": y})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def double_click(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/double_click", {"x": x, "y": y})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def right_click(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/right_click", {"x": x, "y": y})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def move_to(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/move_to", {"x": x, "y": y})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def drag(
        self, from_x: int, from_y: int, to_x: int, to_y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/drag", {
                "from_x": from_x, "from_y": from_y,
                "to_x": to_x, "to_y": to_y,
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def scroll(
        self, dx: int = 0, dy: int = 0,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/scroll", {"dx": dx, "dy": dy})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def mouse_down(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/mouse_down", {"x": x, "y": y})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def mouse_up(
        self, x: int, y: int,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/mouse_up", {"x": x, "y": y})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def type(
        self, text: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/type", {"text": text})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def press(
        self, key: str, duration_ms: int = 300,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/press", {"key": key, "duration_ms": duration_ms})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def hot_key(
        self, keys: list[str],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/display/hot_key", {"keys": keys})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_INPUT_FAILED, str(e))

    async def screenshot(
        self,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[str]:
        try:
            data = await self._post("/v1/display/screenshot", {})
            return Result.ok(data.get("image", ""))
        except Exception as e:
            return Result.fail(E_INPUT_SCREENSHOT_FAILED, str(e))
