"""Browser sub-package — Page, Pages, Browser and related abstract bases."""

from browser_sdk.browser.base import Browser, CaptchaDetector, CookieManager, NetworkInterceptor, Page, Pages

__all__ = [
    "Browser",
    "Page",
    "Pages",
    "CookieManager",
    "NetworkInterceptor",
    "CaptchaDetector",
]
