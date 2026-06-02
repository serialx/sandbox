"""LocalBrowser — assembles all local Playwright-backed sub-components."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from browser_sdk.browser.base import Browser, CaptchaDetector, CookieManager, NetworkInterceptor, Pages
from browser_sdk.browser.cookies import LocalCookieManager
from browser_sdk.browser.network import LocalNetworkInterceptor
from browser_sdk.browser.pages import LocalPages
from browser_sdk.captcha.detector import LocalCaptchaDetector
from browser_sdk.models import BrowserFlareConfig, BrowserInfo, SessionConfig
from browser_sdk.stealth.manager import StealthManager

if TYPE_CHECKING:
    from playwright.async_api import Browser as PWBrowser
    from playwright.async_api import BrowserContext as PWBrowserContext

logger = logging.getLogger(__name__)


class LocalBrowser(Browser):
    """
    Playwright-backed browser that owns a single BrowserContext.

    Created by LocalSession during connection; not instantiated directly.
    """

    def __init__(
        self,
        pw_browser: PWBrowser,
        pw_context: PWBrowserContext,
        session_id: str,
        session_config: SessionConfig,
        sdk_config: BrowserFlareConfig,
        cdp_endpoint: str,
        user_agent: str,
    ) -> None:
        self._pw_browser = pw_browser
        self._pw_context = pw_context
        self._session_id = session_id
        self._start_time = datetime.now()

        self._info = BrowserInfo(
            session_id=session_id,
            cdp_endpoint=cdp_endpoint,
            config=session_config,
            start_time=self._start_time,
            user_agent=user_agent,
        )

        # Sub-components
        self._captcha_detector = LocalCaptchaDetector(
            stuck_timeout=sdk_config.captcha_stuck_timeout,
        )

        self._pages = LocalPages(
            pw_context=pw_context,
            max_console=sdk_config.max_console_messages,
            captcha_auto_detect=sdk_config.captcha_auto_detect,
            captcha_detector=self._captcha_detector,
            download_dir=sdk_config.download_dir,
        )

        self._cookies = LocalCookieManager(
            pw_context=pw_context,
            state_dir=sdk_config.state_dir,
        )

        self._network = LocalNetworkInterceptor(
            pw_context=pw_context,
            max_tracked=sdk_config.max_tracked_requests,
        )

        # Auto-enable request tracking if configured
        self._request_tracking_enabled = sdk_config.request_tracking_enabled

    async def _apply_stealth(self, stealth: StealthManager) -> None:
        """Called by LocalSession after construction to inject stealth scripts."""
        await stealth.apply(self._pw_context)

    async def _enable_tracking_if_needed(self) -> None:
        if self._request_tracking_enabled:
            await self._network.enable_tracking()

    @property
    def info(self) -> BrowserInfo:
        return self._info

    @property
    def pages(self) -> Pages:
        return self._pages

    @property
    def cookies(self) -> CookieManager:
        return self._cookies

    @property
    def network(self) -> NetworkInterceptor:
        return self._network

    @property
    def captcha(self) -> CaptchaDetector:
        return self._captcha_detector

    async def close(self) -> None:
        """Close the browser context and disconnect."""
        try:
            await self._pw_context.close()
        except Exception:
            pass
        try:
            await self._pw_browser.close()
        except Exception:
            pass
        logger.info("LocalBrowser closed (session=%s)", self._session_id)
