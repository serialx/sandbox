"""LocalCaptchaDetector — JavaScript-based CAPTCHA detection on Playwright pages."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from browser_sdk.browser.base import CaptchaDetector, Page
from browser_sdk.errors import E_CAPTCHA_DETECTION_FAILED, E_CAPTCHA_STUCK
from browser_sdk.helpers.js_scripts import js_loader
from browser_sdk.models import CaptchaDetectionResult, OperationContext
from browser_sdk.result import Result

logger = logging.getLogger(__name__)


class LocalCaptchaDetector(CaptchaDetector):
    """Detects blocking CAPTCHAs by evaluating JS in the current page."""

    def __init__(self, stuck_timeout: float = 60.0) -> None:
        self._stuck_timeout = stuck_timeout

    async def detect(
        self,
        page: Page | None = None,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[CaptchaDetectionResult | None]:
        if page is None:
            return Result.ok(None)
        try:
            # Access the underlying Playwright page for evaluate
            pw_page = getattr(page, "_page", None)
            if pw_page is None:
                return Result.ok(None)

            raw = await pw_page.evaluate(js_loader.get_captcha_detection_script())
            if not raw or not raw.get("detected"):
                return Result.ok(None)

            result = CaptchaDetectionResult(
                detected=raw["detected"],
                type=raw.get("type", ""),
                confidence=raw.get("confidence", 0),
                indicators=raw.get("indicators", []),
                url=raw.get("url", ""),
                timestamp=raw.get("timestamp", ""),
            )
            logger.info(
                "CAPTCHA detected: type=%s confidence=%d url=%s",
                result.type,
                result.confidence,
                result.url,
            )
            return Result.ok(result)
        except Exception as e:
            return Result.fail(E_CAPTCHA_DETECTION_FAILED, str(e))

    async def wait_for_resolution(
        self,
        timeout: float = 60.0,
        poll_interval: float = 2.0,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[bool]:
        """Poll until CAPTCHA is no longer detected or timeout is reached."""
        elapsed = 0.0
        effective_timeout = min(timeout, self._stuck_timeout)
        while elapsed < effective_timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            # Re-detect — caller should pass the same page via extra
            page = extra if extra is not None else None
            detection = await self.detect(page=page)
            if detection.success and detection.data is None:
                logger.info("CAPTCHA resolved after %.1fs", elapsed)
                return Result.ok(True)
        return Result.fail(
            E_CAPTCHA_STUCK,
            f"CAPTCHA still detected after {effective_timeout:.0f}s",
        )
