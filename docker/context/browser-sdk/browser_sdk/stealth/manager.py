"""StealthManager — injects anti-detection scripts into browser contexts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from browser_sdk.helpers.js_scripts import js_loader

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext as PWBrowserContext

logger = logging.getLogger(__name__)


class StealthManager:
    """
    Internal component that applies anti-detection patches to browser contexts.

    Not exposed in the public API — automatically applied when stealth_enabled=True.
    """

    def __init__(self, enabled: bool = True, locale: str = "en-US") -> None:
        self._enabled = enabled
        self._locale = locale

    async def apply(self, context: PWBrowserContext) -> None:
        """Inject stealth init script into the browser context."""
        if not self._enabled:
            return

        script = js_loader.get_init_script()

        # Patch locale in the script if not en-US
        if self._locale != "en-US":
            script = script.replace(
                '["en-US"]',
                f'["{self._locale}", "en-US"]',
            )

        await context.add_init_script(script)
        logger.debug("Stealth init script injected (locale=%s)", self._locale)
