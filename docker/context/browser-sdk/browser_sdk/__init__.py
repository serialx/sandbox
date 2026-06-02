"""BrowserFlare SDK — unified browser automation for local and remote sandboxes."""

from browser_sdk.result import ErrorDetails, Result
from browser_sdk.flare import BrowserFlare
from browser_sdk.models import (
    AccessConfig,
    BoundingBox,
    BrowserFlareConfig,
    BrowserInfo,
    CaptchaDetectionResult,
    ConsoleMessage,
    Cookie,
    CookieParam,
    DownloadResult,
    FormFillItem,
    InteractiveElement,
    LocalConfig,
    RouteResponse,
    NavigateResult,
    OpenOptions,
    OperationContext,
    PageRecordResult,
    PageInfo,
    ProxyConfig,
    RemoteConfig,
    Resolution,
    SessionConfig,
    TextMatch,
    TrackedRequest,
)

# Abstract bases (for type hints and custom implementations)
from browser_sdk.browser.base import Browser, CaptchaDetector, CookieManager, NetworkInterceptor, Page, Pages
from browser_sdk.display.base import Display
from browser_sdk.session.base import Session

__all__ = [
    # Entry point
    "BrowserFlare",
    # Result
    "Result",
    "ErrorDetails",
    # Session
    "Session",
    # Abstract bases
    "Browser",
    "Pages",
    "Page",
    "CookieManager",
    "NetworkInterceptor",
    "CaptchaDetector",
    "Display",
    # Config models
    "BrowserFlareConfig",
    "SessionConfig",
    "LocalConfig",
    "RemoteConfig",
    "AccessConfig",
    "Resolution",
    "ProxyConfig",
    "OperationContext",
    # Data models
    "BrowserInfo",
    "OpenOptions",
    "NavigateResult",
    "PageInfo",
    "PageRecordResult",
    "BoundingBox",
    "InteractiveElement",
    "FormFillItem",
    "TextMatch",
    "ConsoleMessage",
    "Cookie",
    "CookieParam",
    "RouteResponse",
    "TrackedRequest",
    "CaptchaDetectionResult",
    "DownloadResult",
]
