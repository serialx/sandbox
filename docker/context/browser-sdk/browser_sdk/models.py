"""All Pydantic data models for the BrowserFlare SDK."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ============================================================
# Operation Context
# ============================================================


class OperationContext(BaseModel):
    """Non-business context for logging and tracing."""

    log_id: str


# ============================================================
# Configuration Models
# ============================================================


class AccessConfig(BaseModel):
    """Authentication credentials for remote sandbox access."""

    aid: int
    token: str
    user_name: str


class LocalConfig(BaseModel):
    """Configuration for connecting to a local browser instance."""

    cdp_port: int = 9222
    cdp_host: str = "127.0.0.1"
    command_line: list[str] | None = None


class RemoteConfig(BaseModel):
    """Configuration for connecting to a remote sandbox instance."""

    api_endpoint: str
    command_line: list[str] | None = None


class Resolution(BaseModel):
    """Display resolution."""

    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ProxyConfig(BaseModel):
    """Proxy configuration."""

    url: str
    user: str | None = None
    password: str | None = None


class SessionConfig(BaseModel):
    """Session specification — the 'blueprint' for a session."""

    type: Literal["local", "remote"]
    session_id: str | None = None
    resolution: Resolution | None = None
    proxy: ProxyConfig | None = None
    local: LocalConfig | None = None
    remote: RemoteConfig | None = None


class BrowserFlareConfig(BaseModel):
    """SDK-level configuration with defaults."""

    # Stealth
    stealth_enabled: bool = True

    # CAPTCHA
    captcha_auto_detect: bool = True
    captcha_stuck_timeout: float = 60.0

    # Timeouts
    connect_timeout: float = 60.0
    action_timeout: float = 30.0
    navigation_timeout: float = 30.0

    # State persistence
    state_dir: str = "~/.browser_state"

    # Network
    request_tracking_enabled: bool = False
    max_tracked_requests: int = 1000

    # Console
    max_console_messages: int = 1000

    # Downloads
    accept_downloads: bool = True
    download_dir: str = "/tmp/downloads"

    # Context defaults
    default_viewport: Resolution = Resolution(width=1280, height=720)
    default_locale: str = "en-US"
    default_timezone_id: str | None = None
    default_user_agent: str | None = None


# ============================================================
# Browser Info
# ============================================================


class BrowserInfo(BaseModel):
    """Read-only snapshot of browser state."""

    session_id: str
    cdp_endpoint: str
    config: SessionConfig
    start_time: datetime
    user_agent: str


# ============================================================
# Navigation
# ============================================================


class OpenOptions(BaseModel):
    """Options for page.open()."""

    wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load"
    timeout: float = 30.0


class NavigateResult(BaseModel):
    """Result data from a navigation operation."""

    url: str
    title: str
    status: int | None = None
    load_time_ms: float


# ============================================================
# Page & Tab Info
# ============================================================


class PageInfo(BaseModel):
    """Summary info for a browser tab."""

    index: int
    url: str
    title: str
    is_active: bool


# ============================================================
# Element Interaction
# ============================================================


class BoundingBox(BaseModel):
    """Element bounding box in viewport coordinates."""

    x: float
    y: float
    width: float
    height: float


class InteractiveElement(BaseModel):
    """A visible, interactive DOM element with its index for targeting."""

    index: int
    tag: str
    role: str | None = None
    text: str = ""
    placeholder: str | None = None
    href: str | None = None
    type: str | None = None
    bounding_box: BoundingBox | None = None
    is_visible: bool = True
    is_enabled: bool = True


class FormFillItem(BaseModel):
    """Single item in a batch form fill operation."""

    selector: str | None = None
    index: int | None = None
    value: str


class TextMatch(BaseModel):
    """A text search match on the page."""

    text: str
    index: int
    bounding_box: BoundingBox | None = None


# ============================================================
# Console
# ============================================================


class ConsoleMessage(BaseModel):
    """A captured browser console message."""

    type: str  # log | warn | error | info | debug | pageerror | crash
    text: str
    timestamp: float
    url: str | None = None
    line: int | None = None
    page_url: str | None = None


# ============================================================
# Cookies
# ============================================================


class Cookie(BaseModel):
    """A browser cookie (read)."""

    name: str
    value: str
    domain: str
    path: str = "/"
    expires: float = -1
    http_only: bool = False
    secure: bool = False
    same_site: str = "Lax"


class CookieParam(BaseModel):
    """Parameters for setting a cookie."""

    name: str
    value: str
    domain: str | None = None
    path: str = "/"
    expires: float | None = None
    http_only: bool = False
    secure: bool = False
    same_site: str = "Lax"
    url: str | None = None


# ============================================================
# Network
# ============================================================


class RouteResponse(BaseModel):
    """Response used to fulfill an intercepted route request."""

    status: int = 200
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | bytes = ""
    content_type: str = "text/plain"


class TrackedRequest(BaseModel):
    """A captured network request."""

    url: str
    method: str
    status: int | None = None
    request_headers: dict[str, str] = Field(default_factory=dict)
    response_headers: dict[str, str] | None = None
    resource_type: str = ""
    timestamp: float = 0.0
    duration_ms: float | None = None


# ============================================================
# CAPTCHA
# ============================================================


class CaptchaDetectionResult(BaseModel):
    """Result from CAPTCHA detection script."""

    detected: bool
    type: str = ""
    confidence: int = 0
    indicators: list[str] = Field(default_factory=list)
    url: str = ""
    timestamp: str = ""


# ============================================================
# Download
# ============================================================


class DownloadResult(BaseModel):
    """Result from a completed download."""

    url: str
    suggested_filename: str
    saved_path: str
    size_bytes: int = 0


# ============================================================
# Recording
# ============================================================


class PageRecordResult(BaseModel):
    """Result from a page screencast recording."""

    action: Literal["once", "start", "pause", "resume", "stop", "status"]
    status: Literal["idle", "recording", "paused", "stopped"]
    save_path: str | None = None
    duration: float
    fps: int
    quality: int
    frame_count: int = 0
    is_recording: bool = False
    is_paused: bool = False
