"""Browser, Pages, Page, CookieManager, NetworkInterceptor — abstract base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from browser_sdk.models import (
    BrowserInfo,
    CaptchaDetectionResult,
    ConsoleMessage,
    Cookie,
    CookieParam,
    DownloadResult,
    FormFillItem,
    InteractiveElement,
    RouteResponse,
    NavigateResult,
    OpenOptions,
    OperationContext,
    PageRecordResult,
    PageInfo,
    TextMatch,
    TrackedRequest,
)
from browser_sdk.result import Result


# ============================================================
# Page
# ============================================================


class Page(ABC):
    """A single browser page/tab with semantic DOM operations."""

    # --- Navigation ---

    @abstractmethod
    async def open(
        self,
        url: str,
        options: OpenOptions | None = None,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[NavigateResult]: ...

    @abstractmethod
    async def back(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[NavigateResult | None]: ...

    @abstractmethod
    async def forward(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[NavigateResult | None]: ...

    @abstractmethod
    async def reload(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def is_current(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[bool]: ...

    @abstractmethod
    async def switch_to_current(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    # --- Element Interaction ---

    @abstractmethod
    async def click(
        self,
        selector: str | None = None,
        index: int | None = None,
        x: float | None = None,
        y: float | None = None,
        button: str = "left",
        click_count: int = 1,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def fill(
        self,
        text: str,
        selector: str | None = None,
        index: int | None = None,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def type_text(
        self,
        text: str,
        delay: float = 0,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def press_key(
        self,
        key: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def hot_key(
        self,
        keys: list[str],
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def hover(
        self,
        selector: str | None = None,
        x: float | None = None,
        y: float | None = None,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def select_option(
        self,
        selector: str,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def check(
        self,
        selector: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def uncheck(
        self,
        selector: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def upload_file(
        self,
        selector: str,
        files: list[str],
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def fill_form(
        self,
        items: list[FormFillItem],
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    # --- Scrolling ---

    @abstractmethod
    async def scroll(
        self,
        direction: str = "down",
        amount: int = 300,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def scroll_to(
        self,
        x: int = 0,
        y: int = 0,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def scroll_to_element(
        self,
        selector: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    # --- Content Retrieval ---

    @abstractmethod
    async def screenshot(
        self,
        full_page: bool = False,
        format: str = "png",
        quality: int | None = None,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[bytes]: ...

    @abstractmethod
    async def record(
        self,
        action: str = "once",
        save_path: str | None = None,
        duration: float = 10.0,
        fps: int = 15,
        quality: int = 80,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[PageRecordResult]: ...

    @abstractmethod
    async def get_html(
        self,
        outer: bool = False,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[str]: ...

    @abstractmethod
    async def get_text(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[str]: ...

    @abstractmethod
    async def get_markdown(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[dict]: ...

    @abstractmethod
    async def evaluate(
        self,
        expression: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[Any]: ...

    @abstractmethod
    async def get_console_logs(
        self,
        clear: bool = False,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[list[ConsoleMessage]]: ...

    @abstractmethod
    async def get_interactive_elements(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[list[InteractiveElement]]: ...

    @abstractmethod
    async def find_text(
        self,
        keyword: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[list[TextMatch]]: ...

    # --- Wait ---

    @abstractmethod
    async def wait_for_load(
        self,
        state: str = "load",
        timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def wait_for_selector(
        self,
        selector: str,
        state: str = "visible",
        timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def wait_for_url(
        self,
        url: str,
        timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def wait_for_network_idle(
        self,
        timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def wait_for_function(
        self,
        expression: str,
        timeout: float = 30.0,
        polling: float | str = "raf",
        ctx: OperationContext | None = None,
    ) -> Result[Any]: ...

    @abstractmethod
    async def wait_for_response(
        self,
        url_pattern: str,
        timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[dict]: ...

    @abstractmethod
    async def wait_for_request(
        self,
        url_pattern: str,
        timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[dict]: ...

    # --- Download ---

    @abstractmethod
    async def wait_for_download(
        self,
        save_path: str | None = None,
        timeout: float = 30.0,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[DownloadResult]:
        """Wait for the next download to complete and optionally save it."""

    # --- Lifecycle ---

    @abstractmethod
    async def close(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...


# ============================================================
# Pages (Tab Manager)
# ============================================================


class Pages(ABC):
    """Manages browser tabs within a context."""

    @abstractmethod
    async def create(
        self,
        url: str | None = None,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[Page]: ...

    @abstractmethod
    async def get_current(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[Page]: ...

    @abstractmethod
    async def list(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[list[PageInfo]]: ...

    @abstractmethod
    async def close(
        self,
        index: int | None = None,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...


# ============================================================
# CookieManager
# ============================================================


class CookieManager(ABC):
    """Manages cookies and browser storage state."""

    @abstractmethod
    async def get(
        self,
        urls: list[str] | None = None,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[list[Cookie]]: ...

    @abstractmethod
    async def set(
        self,
        cookies: list[CookieParam],
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def clear(
        self,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def save_state(
        self,
        path: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def load_state(
        self,
        path: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...


# ============================================================
# NetworkInterceptor
# ============================================================


class NetworkInterceptor(ABC):
    """Request interception, header management, and request tracking."""

    @abstractmethod
    async def add_route(
        self,
        url_pattern: str,
        response: RouteResponse | None = None,
        abort: bool = False,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def remove_route(
        self,
        url_pattern: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def set_extra_headers(
        self,
        headers: dict[str, str],
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def set_scoped_headers(
        self,
        origin: str,
        headers: dict[str, str],
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def enable_tracking(
        self,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def get_requests(
        self,
        filter: str | None = None,
        limit: int = 100,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[list[TrackedRequest]]: ...

    @abstractmethod
    async def clear_requests(
        self,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...

    @abstractmethod
    async def export_har(
        self,
        save_path: str,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]: ...


# ============================================================
# CaptchaDetector
# ============================================================


class CaptchaDetector(ABC):
    """Detects blocking CAPTCHAs on pages."""

    @abstractmethod
    async def detect(
        self,
        page: Page | None = None,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[CaptchaDetectionResult | None]: ...

    @abstractmethod
    async def wait_for_resolution(
        self,
        timeout: float = 60.0,
        poll_interval: float = 2.0,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[bool]: ...


# ============================================================
# Browser
# ============================================================


class Browser(ABC):
    """Semantic browser controller with Playwright-level operations."""

    @property
    @abstractmethod
    def info(self) -> BrowserInfo: ...

    @property
    @abstractmethod
    def pages(self) -> Pages: ...

    @property
    @abstractmethod
    def cookies(self) -> CookieManager: ...

    @property
    @abstractmethod
    def network(self) -> NetworkInterceptor: ...

    @property
    @abstractmethod
    def captcha(self) -> CaptchaDetector: ...
