"""LocalSession — connects to a local Chromium via CDP and assembles components."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from playwright.async_api import async_playwright
from playwright.async_api import Error as PWError

from browser_sdk.browser.base import Browser
from browser_sdk.browser.local import LocalBrowser
from browser_sdk.display.base import Display
from browser_sdk.display.local import LocalDisplay
from browser_sdk.errors import (
    E_CONNECT_CDP_FAILED,
    E_CONNECT_TIMEOUT,
    E_SESSION_CLOSED,
    E_SESSION_UPDATE_PROXY_FAILED,
    E_SESSION_UPDATE_RESOLUTION_FAILED,
)
from browser_sdk.models import (
    BrowserFlareConfig,
    LocalConfig,
    OperationContext,
    ProxyConfig,
    Resolution,
    SessionConfig,
)
from browser_sdk.result import Result
from browser_sdk.session.base import Session
from browser_sdk.stealth.manager import StealthManager

logger = logging.getLogger(__name__)


class LocalSession(Session):
    """
    Session that connects to a local Chromium instance via CDP.

    Created by ``BrowserFlare.connect()`` — not instantiated directly.
    """

    def __init__(
        self,
        session_id: str,
        display: LocalDisplay,
        browser: LocalBrowser,
        pw_handle: Any,
        config: SessionConfig,
    ) -> None:
        self._id = session_id
        self._created_at = datetime.now()
        self._display = display
        self._browser = browser
        self._pw_handle = pw_handle  # Playwright context manager to keep alive
        self._config = config
        self._closed = False

    @property
    def id(self) -> str:
        return self._id

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def display(self) -> Display:
        return self._display

    @property
    def browser(self) -> Browser | None:
        return self._browser

    async def close(self, ctx: OperationContext | None = None) -> Result[None]:
        if self._closed:
            return Result.fail(E_SESSION_CLOSED, "Session already closed")
        self._closed = True
        if self._browser:
            await self._browser.close()
        if self._pw_handle:
            await self._pw_handle.__aexit__(None, None, None)
        logger.info("LocalSession closed: %s", self._id)
        return Result.ok()

    async def disconnect(self, ctx: OperationContext | None = None) -> Result[None]:
        """Disconnect Playwright without closing the remote browser context.

        Used by soft restart to drop the local Playwright/CDP session while
        keeping the underlying Chromium process and tabs alive.
        """
        if self._closed:
            return Result.fail(E_SESSION_CLOSED, "Session already closed")
        self._closed = True
        self._browser = None
        if self._pw_handle:
            await self._pw_handle.__aexit__(None, None, None)
            self._pw_handle = None
        logger.info("LocalSession disconnected: %s", self._id)
        return Result.ok()

    async def update_proxy(
        self,
        proxy: ProxyConfig,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        # Local Playwright contexts don't support dynamic proxy changes.
        # This would require restarting the browser context.
        return Result.fail(
            E_SESSION_UPDATE_PROXY_FAILED,
            "Dynamic proxy update not supported for local sessions; "
            "set proxy in SessionConfig before connecting.",
        )

    async def update_resolution(
        self,
        resolution: Resolution,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        if self._closed:
            return Result.fail(E_SESSION_CLOSED, "Session is closed")
        try:
            # Update viewport on all pages
            pages = self._browser._pw_context.pages if self._browser else []
            for page in pages:
                await page.set_viewport_size(
                    {"width": resolution.width, "height": resolution.height}
                )
            return Result.ok()
        except Exception as e:
            return Result.fail(E_SESSION_UPDATE_RESOLUTION_FAILED, str(e))

    @staticmethod
    async def create(
        config: SessionConfig,
        sdk_config: BrowserFlareConfig,
    ) -> Result["LocalSession"]:
        """
        Connect to a local Chromium via CDP and return a ready-to-use session.

        The caller (BrowserFlare) invokes this factory method.
        """
        local = config.local or LocalConfig()
        cdp_url = f"http://{local.cdp_host}:{local.cdp_port}"
        session_id = config.session_id or uuid.uuid4().hex[:12]

        pw_cm = async_playwright()
        try:
            pw = await pw_cm.__aenter__()
        except Exception as e:
            return Result.fail(E_CONNECT_CDP_FAILED, f"Playwright init failed: {e}")

        try:
            pw_browser = await asyncio.wait_for(
                pw.chromium.connect_over_cdp(cdp_url),
                timeout=sdk_config.connect_timeout,
            )
        except asyncio.TimeoutError:
            await pw_cm.__aexit__(None, None, None)
            return Result.fail(
                E_CONNECT_TIMEOUT,
                f"CDP connection to {cdp_url} timed out after {sdk_config.connect_timeout}s",
            )
        except PWError as e:
            await pw_cm.__aexit__(None, None, None)
            return Result.fail(E_CONNECT_CDP_FAILED, str(e))

        # Use the default context (first) or create a new one
        contexts = pw_browser.contexts
        if contexts:
            pw_context = contexts[0]
            # For existing contexts, configure downloads via CDP protocol
            if sdk_config.accept_downloads:
                try:
                    page_for_cdp = pw_context.pages[0] if pw_context.pages else await pw_context.new_page()
                    cdp = await pw_context.new_cdp_session(page_for_cdp)
                    await cdp.send("Page.setDownloadBehavior", {
                        "behavior": "allow",
                        "downloadPath": sdk_config.download_dir,
                    })
                    await cdp.detach()
                except Exception:
                    logger.debug("CDP setDownloadBehavior failed, downloads may use defaults")
        else:
            pw_context = await pw_browser.new_context(
                viewport={
                    "width": sdk_config.default_viewport.width,
                    "height": sdk_config.default_viewport.height,
                },
                locale=sdk_config.default_locale,
                timezone_id=sdk_config.default_timezone_id,
                user_agent=sdk_config.default_user_agent,
                accept_downloads=sdk_config.accept_downloads,
            )

        # Detect user agent
        if pw_context.pages:
            user_agent = await pw_context.pages[0].evaluate("navigator.userAgent")
        else:
            tmp = await pw_context.new_page()
            user_agent = await tmp.evaluate("navigator.userAgent")

        # Build LocalBrowser
        local_browser = LocalBrowser(
            pw_browser=pw_browser,
            pw_context=pw_context,
            session_id=session_id,
            session_config=config,
            sdk_config=sdk_config,
            cdp_endpoint=cdp_url,
            user_agent=user_agent,
        )

        # Apply stealth
        stealth = StealthManager(
            enabled=sdk_config.stealth_enabled,
            locale=sdk_config.default_locale,
        )
        await local_browser._apply_stealth(stealth)

        # Enable request tracking if configured
        await local_browser._enable_tracking_if_needed()

        # Build display
        display = LocalDisplay()

        session = LocalSession(
            session_id=session_id,
            display=display,
            browser=local_browser,
            pw_handle=pw_cm,
            config=config,
        )
        logger.info(
            "LocalSession created: id=%s cdp=%s stealth=%s",
            session_id,
            cdp_url,
            sdk_config.stealth_enabled,
        )
        return Result.ok(session)
