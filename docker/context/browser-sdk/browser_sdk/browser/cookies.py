"""LocalCookieManager — Playwright context-level cookie and state management."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext as PWBrowserContext
from playwright.async_api import Error as PWError

from browser_sdk.browser.base import CookieManager
from browser_sdk.errors import (
    E_COOKIE_CLEAR_FAILED,
    E_COOKIE_GET_FAILED,
    E_COOKIE_SET_FAILED,
    E_COOKIE_STATE_LOAD_FAILED,
    E_COOKIE_STATE_SAVE_FAILED,
)
from browser_sdk.models import Cookie, CookieParam, OperationContext
from browser_sdk.result import Result

logger = logging.getLogger(__name__)


class LocalCookieManager(CookieManager):
    """Cookie management via Playwright BrowserContext."""

    def __init__(self, pw_context: PWBrowserContext, state_dir: str = "~/.browser_state") -> None:
        self._context = pw_context
        self._state_dir = Path(state_dir).expanduser()

    async def get(
        self, urls: list[str] | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[Cookie]]:
        try:
            raw = await self._context.cookies(urls or [])
            cookies = [
                Cookie(
                    name=c["name"],
                    value=c["value"],
                    domain=c["domain"],
                    path=c.get("path", "/"),
                    expires=c.get("expires", -1),
                    http_only=c.get("httpOnly", False),
                    secure=c.get("secure", False),
                    same_site=c.get("sameSite", "Lax"),
                )
                for c in raw
            ]
            return Result.ok(cookies)
        except PWError as e:
            return Result.fail(E_COOKIE_GET_FAILED, str(e))

    async def set(
        self, cookies: list[CookieParam],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            raw = []
            for c in cookies:
                entry: dict[str, Any] = {"name": c.name, "value": c.value, "path": c.path}
                if c.url:
                    entry["url"] = c.url
                if c.domain:
                    entry["domain"] = c.domain
                if c.expires is not None:
                    entry["expires"] = c.expires
                entry["httpOnly"] = c.http_only
                entry["secure"] = c.secure
                entry["sameSite"] = c.same_site
                raw.append(entry)
            await self._context.add_cookies(raw)
            return Result.ok()
        except PWError as e:
            return Result.fail(E_COOKIE_SET_FAILED, str(e))

    async def clear(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._context.clear_cookies()
            return Result.ok()
        except PWError as e:
            return Result.fail(E_COOKIE_CLEAR_FAILED, str(e))

    async def save_state(
        self, path: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            state = await self._context.storage_state()
            target = Path(path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(state, indent=2), encoding="utf-8")
            logger.info("Browser state saved to %s", target)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_COOKIE_STATE_SAVE_FAILED, str(e))

    async def load_state(
        self, path: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            target = Path(path).expanduser()
            if not target.exists():
                return Result.fail(E_COOKIE_STATE_LOAD_FAILED, f"State file not found: {target}")
            state = json.loads(target.read_text(encoding="utf-8"))
            # Load cookies from state
            if "cookies" in state:
                await self._context.add_cookies(state["cookies"])
            logger.info("Browser state loaded from %s", target)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_COOKIE_STATE_LOAD_FAILED, str(e))
