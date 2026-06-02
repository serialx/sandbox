from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Navigation ──────────────────────────────────────────────────────

class NavigateRequest(BaseModel):
    url: str
    wait_until: Literal['load', 'domcontentloaded', 'networkidle', 'commit'] = 'load'
    timeout: float = 30.0


# ── Interaction ─────────────────────────────────────────────────────

class ClickRequest(BaseModel):
    selector: str | None = None
    index: int | None = None
    x: float | None = None
    y: float | None = None
    button: str = 'left'
    click_count: int = 1


class FillRequest(BaseModel):
    selector: str | None = None
    index: int | None = None
    text: str


class TypeTextRequest(BaseModel):
    text: str
    delay: float = 0


class KeyRequest(BaseModel):
    key: str


class HotKeyRequest(BaseModel):
    keys: list[str]


class HoverRequest(BaseModel):
    selector: str | None = None
    x: float | None = None
    y: float | None = None


class SelectOptionRequest(BaseModel):
    selector: str
    value: str | None = None
    label: str | None = None
    index: int | None = None


class CheckRequest(BaseModel):
    selector: str


class UploadFileRequest(BaseModel):
    selector: str
    files: list[str]


class FormFillRequest(BaseModel):
    items: list[dict[str, Any]]


# ── Scrolling ───────────────────────────────────────────────────────

class ScrollRequest(BaseModel):
    direction: str = 'down'
    amount: int = 300


class ScrollToRequest(BaseModel):
    x: int = 0
    y: int = 0


class ScrollToElementRequest(BaseModel):
    selector: str


# ── Content ─────────────────────────────────────────────────────────

class ScreenshotRequest(BaseModel):
    full_page: bool = False
    format: str = 'png'
    quality: int | None = None


class RecordRequest(BaseModel):
    action: Literal['once', 'start', 'pause', 'resume', 'stop', 'status'] = Field(default='once', title='Record Action')
    save_path: str | None = None
    duration: float = Field(default=10.0, gt=0)
    fps: int = Field(default=15, gt=0)
    quality: int = Field(default=80, ge=0, le=100)


class EvaluateRequest(BaseModel):
    expression: str


class FindTextRequest(BaseModel):
    keyword: str


# ── Wait (unified) ─────────────────────────────────────────────────

class WaitRequest(BaseModel):
    type: Literal['selector', 'load', 'url', 'network_idle', 'download', 'function', 'response', 'request', 'timeout']
    selector: str | None = None
    state: str | None = None
    url: str | None = None
    save_path: str | None = None
    timeout: float = 30.0
    expression: str | None = None
    polling: float | None = None
    url_pattern: str | None = None


# ── Pages ───────────────────────────────────────────────────────────

class CreatePageRequest(BaseModel):
    url: str | None = None


class ClosePageRequest(BaseModel):
    index: int | None = None


# ── Cookies ─────────────────────────────────────────────────────────

class CookieSetRequest(BaseModel):
    cookies: list[dict[str, Any]]


# ── State ───────────────────────────────────────────────────────────

class StateSaveRequest(BaseModel):
    path: str


class StateLoadRequest(BaseModel):
    path: str


# ── Network ─────────────────────────────────────────────────────────

class HeadersRequest(BaseModel):
    headers: dict[str, str]


class NetworkRequestsRequest(BaseModel):
    filter: str | None = None
    limit: int = 100


class RouteResponseModel(BaseModel):
    status: int = 200
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    content_type: str = "text/plain"


class NetworkRouteRequest(BaseModel):
    url_pattern: str
    response: RouteResponseModel | None = None
    abort: bool = False


class NetworkRouteRemoveRequest(BaseModel):
    url_pattern: str


class ScopedHeadersRequest(BaseModel):
    origin: str
    headers: dict[str, str]


# ── CAPTCHA ─────────────────────────────────────────────────────────

class CaptchaWaitRequest(BaseModel):
    timeout: float = 60.0
    poll_interval: float = 2.0


class CaptchaWaitResult(BaseModel):
    resolved: bool


# ── Session ────────────────────────────────────────────────────────

class RestartRequest(BaseModel):
    mode: Literal['soft', 'hard'] = 'hard'
    url_blocklist: list[str] | None = None
    url_allowlist: list[str] | None = None
    locale: str | None = None


# ── Export ─────────────────────────────────────────────────────────

class ExportHarRequest(BaseModel):
    save_path: str


class ExportConsoleLogsRequest(BaseModel):
    save_path: str
    clear: bool = False
