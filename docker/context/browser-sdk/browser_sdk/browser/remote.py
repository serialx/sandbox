"""RemoteBrowser — HTTP-client-based browser operations against a sandbox API."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from browser_sdk.browser.base import (
    Browser,
    CaptchaDetector,
    CookieManager,
    NetworkInterceptor,
    Page,
    Pages,
)
from browser_sdk.errors import (
    E_CAPTCHA_DETECTION_FAILED,
    E_CAPTCHA_STUCK,
    E_COOKIE_CLEAR_FAILED,
    E_COOKIE_GET_FAILED,
    E_COOKIE_SET_FAILED,
    E_COOKIE_STATE_LOAD_FAILED,
    E_COOKIE_STATE_SAVE_FAILED,
    E_DOWNLOAD_FAILED,
    E_EVAL_SCRIPT_ERROR,
    E_NETWORK_HEADERS_FAILED,
    E_NETWORK_ROUTE_FAILED,
    E_NETWORK_ROUTE_REMOVE_FAILED,
    E_PAGE_ACTION_FAILED,
    E_PAGE_CURRENT_NOT_FOUND,
    E_PAGE_NAVIGATE_FAILED,
    E_PAGE_NOT_FOUND,
)
from browser_sdk.models import (
    BoundingBox,
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
    SessionConfig,
    TextMatch,
    TrackedRequest,
)
from browser_sdk.result import Result

logger = logging.getLogger(__name__)


# ============================================================
# RemotePage
# ============================================================


class RemotePage(Page):
    """Page that proxies all operations to the remote sandbox API."""

    def __init__(self, client: httpx.AsyncClient, base: str, page_index: int) -> None:
        self._client = client
        self._base = base
        self._index = page_index

    async def _post(self, path: str, body: dict | None = None) -> dict:
        resp = await self._client.post(
            f"{self._base}{path}",
            json={**(body or {}), "page_index": self._index},
        )
        resp.raise_for_status()
        return resp.json()

    # --- Navigation ---

    async def open(
        self, url: str, options: OpenOptions | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[NavigateResult]:
        try:
            opts = options or OpenOptions()
            data = await self._post("/v1/browser/page/navigate", {
                "url": url, "wait_until": opts.wait_until, "timeout": opts.timeout,
            })
            return Result.ok(NavigateResult(**data))
        except Exception as e:
            return Result.fail(E_PAGE_NAVIGATE_FAILED, str(e))

    async def back(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[NavigateResult | None]:
        try:
            data = await self._post("/v1/browser/page/back")
            return Result.ok(NavigateResult(**data) if data else None)
        except Exception as e:
            return Result.fail(E_PAGE_NAVIGATE_FAILED, str(e))

    async def forward(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[NavigateResult | None]:
        try:
            data = await self._post("/v1/browser/page/forward")
            return Result.ok(NavigateResult(**data) if data else None)
        except Exception as e:
            return Result.fail(E_PAGE_NAVIGATE_FAILED, str(e))

    async def reload(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/reload")
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_NAVIGATE_FAILED, str(e))

    async def is_current(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[bool]:
        try:
            data = await self._post("/v1/browser/page/is_current")
            return Result.ok(data.get("is_current", False))
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def switch_to_current(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/switch_to_current")
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    # --- Element Interaction ---

    async def click(
        self, selector: str | None = None, index: int | None = None,
        x: float | None = None, y: float | None = None,
        button: str = "left", click_count: int = 1,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/click", {
                "selector": selector, "index": index,
                "x": x, "y": y, "button": button, "click_count": click_count,
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def fill(
        self, text: str, selector: str | None = None, index: int | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/fill", {
                "text": text, "selector": selector, "index": index,
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def type_text(
        self, text: str, delay: float = 0,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/type_text", {"text": text, "delay": delay})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def press_key(
        self, key: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/press_key", {"key": key})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def hot_key(
        self, keys: list[str],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/hot_key", {"keys": keys})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def hover(
        self, selector: str | None = None, x: float | None = None, y: float | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/hover", {"selector": selector, "x": x, "y": y})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def select_option(
        self, selector: str, value: str | None = None,
        label: str | None = None, index: int | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/select_option", {
                "selector": selector, "value": value, "label": label, "index": index,
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def check(
        self, selector: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/check", {"selector": selector})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def uncheck(
        self, selector: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/uncheck", {"selector": selector})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def upload_file(
        self, selector: str, files: list[str],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/upload_file", {
                "selector": selector, "files": files,
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def fill_form(
        self, items: list[FormFillItem],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/fill_form", {
                "items": [i.model_dump() for i in items],
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    # --- Scrolling ---

    async def scroll(
        self, direction: str = "down", amount: int = 300,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/scroll", {
                "direction": direction, "amount": amount,
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def scroll_to(
        self, x: int = 0, y: int = 0,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/scroll_to", {"x": x, "y": y})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def scroll_to_element(
        self, selector: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/scroll_to_element", {"selector": selector})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    # --- Content Retrieval ---

    async def screenshot(
        self, full_page: bool = False, format: str = "png", quality: int | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[bytes]:
        try:
            resp = await self._client.post(
                f"{self._base}/v1/browser/page/screenshot",
                json={
                    "page_index": self._index,
                    "full_page": full_page,
                    "format": format,
                    "quality": quality,
                },
            )
            resp.raise_for_status()
            return Result.ok(resp.content)
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def record(
        self,
        action: str = "once",
        save_path: str | None = None,
        duration: float = 10.0,
        fps: int = 15,
        quality: int = 80,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[PageRecordResult]:
        try:
            data = await self._post(
                "/v1/browser/page/record",
                {
                    "action": action,
                    "save_path": save_path,
                    "duration": duration,
                    "fps": fps,
                    "quality": quality,
                },
            )
            payload = data.get("data", data)
            return Result.ok(PageRecordResult(**payload))
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def get_html(
        self, outer: bool = False,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[str]:
        try:
            data = await self._post("/v1/browser/page/get_html", {"outer": outer})
            return Result.ok(data.get("html", ""))
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def get_text(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[str]:
        try:
            data = await self._post("/v1/browser/page/get_text")
            return Result.ok(data.get("text", ""))
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def get_markdown(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[dict]:
        try:
            data = await self._post("/v1/browser/page/get_markdown")
            return Result.ok(data.get("data", {}))
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def evaluate(
        self, expression: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[Any]:
        try:
            data = await self._post("/v1/browser/page/evaluate", {"expression": expression})
            return Result.ok(data.get("result"))
        except Exception as e:
            return Result.fail(E_EVAL_SCRIPT_ERROR, str(e))

    async def get_console_logs(
        self, clear: bool = False,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[ConsoleMessage]]:
        try:
            data = await self._post("/v1/browser/page/console_logs", {"clear": clear})
            logs = [ConsoleMessage(**item) for item in data.get("logs", [])]
            return Result.ok(logs)
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def get_interactive_elements(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[InteractiveElement]]:
        try:
            data = await self._post("/v1/browser/page/interactive_elements")
            elements = []
            for item in data.get("elements", []):
                bb = item.get("bounding_box")
                elements.append(InteractiveElement(
                    index=item["index"],
                    tag=item["tag"],
                    role=item.get("role"),
                    text=item.get("text", ""),
                    placeholder=item.get("placeholder"),
                    href=item.get("href"),
                    type=item.get("type"),
                    bounding_box=BoundingBox(**bb) if bb else None,
                    is_visible=item.get("is_visible", True),
                    is_enabled=item.get("is_enabled", True),
                ))
            return Result.ok(elements)
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def find_text(
        self, keyword: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[TextMatch]]:
        try:
            data = await self._post("/v1/browser/page/find_text", {"keyword": keyword})
            matches = []
            for item in data.get("matches", []):
                bb = item.get("bounding_box")
                matches.append(TextMatch(
                    text=item["text"],
                    index=item["index"],
                    bounding_box=BoundingBox(**bb) if bb else None,
                ))
            return Result.ok(matches)
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    # --- Wait ---

    async def wait_for_load(
        self, state: str = "load", timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/wait_for_load", {
                "state": state, "timeout": timeout,
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def wait_for_selector(
        self, selector: str, state: str = "visible", timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/wait_for_selector", {
                "selector": selector, "state": state, "timeout": timeout,
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def wait_for_url(
        self, url: str, timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/wait_for_url", {
                "url": url, "timeout": timeout,
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def wait_for_network_idle(
        self, timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/wait_for_network_idle", {"timeout": timeout})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    # --- Download ---

    async def wait_for_download(
        self, save_path: str | None = None, timeout: float = 30.0,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[DownloadResult]:
        try:
            data = await self._post("/v1/browser/page/wait_for_download", {
                "save_path": save_path, "timeout": timeout,
            })
            return Result.ok(DownloadResult(**data))
        except Exception as e:
            return Result.fail(E_DOWNLOAD_FAILED, str(e))

    # --- Lifecycle ---

    async def close(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/page/close")
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))


# ============================================================
# RemotePages
# ============================================================


class RemotePages(Pages):
    """Pages manager that proxies to the remote sandbox API."""

    def __init__(self, client: httpx.AsyncClient, base: str) -> None:
        self._client = client
        self._base = base

    async def _post(self, path: str, body: dict | None = None) -> dict:
        resp = await self._client.post(f"{self._base}{path}", json=body or {})
        resp.raise_for_status()
        return resp.json()

    async def create(
        self, url: str | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[Page]:
        try:
            data = await self._post("/v1/browser/tabs", {"url": url})
            index = (data.get("data") or {}).get("index")
            if index is None:
                return Result.fail(E_PAGE_ACTION_FAILED, "Server did not return tab index")
            return Result.ok(RemotePage(self._client, self._base, index))
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def get_current(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[Page]:
        try:
            data = await self._post("/v1/browser/pages/current")
            return Result.ok(RemotePage(self._client, self._base, data["index"]))
        except Exception as e:
            return Result.fail(E_PAGE_CURRENT_NOT_FOUND, str(e))

    async def list(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[PageInfo]]:
        try:
            data = await self._post("/v1/browser/pages/list")
            return Result.ok([PageInfo(**p) for p in data.get("pages", [])])
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def close(
        self, index: int | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/pages/close", {"index": index})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_PAGE_NOT_FOUND, str(e))


# ============================================================
# RemoteCookieManager
# ============================================================


class RemoteCookieManager(CookieManager):
    """Cookie manager that proxies to the remote sandbox API."""

    def __init__(self, client: httpx.AsyncClient, base: str) -> None:
        self._client = client
        self._base = base

    async def _post(self, path: str, body: dict | None = None) -> dict:
        resp = await self._client.post(f"{self._base}{path}", json=body or {})
        resp.raise_for_status()
        return resp.json()

    async def get(
        self, urls: list[str] | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[Cookie]]:
        try:
            data = await self._post("/v1/browser/cookies/get", {"urls": urls})
            return Result.ok([Cookie(**c) for c in data.get("cookies", [])])
        except Exception as e:
            return Result.fail(E_COOKIE_GET_FAILED, str(e))

    async def set(
        self, cookies: list[CookieParam],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/cookies/set", {
                "cookies": [c.model_dump() for c in cookies],
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_COOKIE_SET_FAILED, str(e))

    async def clear(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/cookies/clear")
            return Result.ok()
        except Exception as e:
            return Result.fail(E_COOKIE_CLEAR_FAILED, str(e))

    async def save_state(
        self, path: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/cookies/save_state", {"path": path})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_COOKIE_STATE_SAVE_FAILED, str(e))

    async def load_state(
        self, path: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/cookies/load_state", {"path": path})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_COOKIE_STATE_LOAD_FAILED, str(e))


# ============================================================
# RemoteNetworkInterceptor
# ============================================================


class RemoteNetworkInterceptor(NetworkInterceptor):
    """Network interceptor that proxies to the remote sandbox API."""

    def __init__(self, client: httpx.AsyncClient, base: str) -> None:
        self._client = client
        self._base = base

    async def _post(self, path: str, body: dict | None = None) -> dict:
        resp = await self._client.post(f"{self._base}{path}", json=body or {})
        resp.raise_for_status()
        return resp.json()

    async def add_route(
        self, url_pattern: str, response: RouteResponse | None = None,
        abort: bool = False,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            body: dict[str, Any] = {"url_pattern": url_pattern, "abort": abort}
            if response:
                body["response"] = response.model_dump()
            await self._post("/v1/browser/network/add_route", body)
            return Result.ok()
        except Exception as e:
            return Result.fail(E_NETWORK_ROUTE_FAILED, str(e))

    async def remove_route(
        self, url_pattern: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/network/remove_route", {"url_pattern": url_pattern})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_NETWORK_ROUTE_REMOVE_FAILED, str(e))

    async def set_extra_headers(
        self, headers: dict[str, str],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/network/set_extra_headers", {"headers": headers})
            return Result.ok()
        except Exception as e:
            return Result.fail(E_NETWORK_HEADERS_FAILED, str(e))

    async def set_scoped_headers(
        self, origin: str, headers: dict[str, str],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/network/set_scoped_headers", {
                "origin": origin, "headers": headers,
            })
            return Result.ok()
        except Exception as e:
            return Result.fail(E_NETWORK_HEADERS_FAILED, str(e))

    async def enable_tracking(
        self, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/network/enable_tracking")
            return Result.ok()
        except Exception as e:
            return Result.fail(E_NETWORK_ROUTE_FAILED, str(e))

    async def get_requests(
        self, filter: str | None = None, limit: int = 100,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[TrackedRequest]]:
        try:
            data = await self._post("/v1/browser/network/get_requests", {
                "filter": filter, "limit": limit,
            })
            return Result.ok([TrackedRequest(**r) for r in data.get("requests", [])])
        except Exception as e:
            return Result.fail(E_NETWORK_ROUTE_FAILED, str(e))

    async def clear_requests(
        self, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._post("/v1/browser/network/clear_requests")
            return Result.ok()
        except Exception as e:
            return Result.fail(E_NETWORK_ROUTE_FAILED, str(e))


# ============================================================
# RemoteCaptchaDetector
# ============================================================


class RemoteCaptchaDetector(CaptchaDetector):
    """CAPTCHA detector that proxies to the remote sandbox API."""

    def __init__(self, client: httpx.AsyncClient, base: str) -> None:
        self._client = client
        self._base = base

    async def _post(self, path: str, body: dict | None = None) -> dict:
        resp = await self._client.post(f"{self._base}{path}", json=body or {})
        resp.raise_for_status()
        return resp.json()

    async def detect(
        self, page: Page | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[CaptchaDetectionResult | None]:
        try:
            data = await self._post("/v1/browser/captcha/detect")
            if not data.get("detected"):
                return Result.ok(None)
            return Result.ok(CaptchaDetectionResult(**data))
        except Exception as e:
            return Result.fail(E_CAPTCHA_DETECTION_FAILED, str(e))

    async def wait_for_resolution(
        self, timeout: float = 60.0, poll_interval: float = 2.0,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[bool]:
        try:
            data = await self._post("/v1/browser/captcha/wait_for_resolution", {
                "timeout": timeout, "poll_interval": poll_interval,
            })
            return Result.ok(data.get("resolved", False))
        except Exception as e:
            return Result.fail(E_CAPTCHA_STUCK, str(e))


# ============================================================
# RemoteBrowser
# ============================================================


class RemoteBrowser(Browser):
    """Browser that proxies all operations to a remote sandbox API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base: str,
        browser_info: BrowserInfo,
    ) -> None:
        self._client = client
        self._base = base
        self._info = browser_info
        self._pages = RemotePages(client, base)
        self._cookies = RemoteCookieManager(client, base)
        self._network = RemoteNetworkInterceptor(client, base)
        self._captcha = RemoteCaptchaDetector(client, base)

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
        return self._captcha
