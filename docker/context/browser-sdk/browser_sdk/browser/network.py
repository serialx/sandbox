"""LocalNetworkInterceptor — Playwright route-based request interception."""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import BrowserContext as PWBrowserContext
from playwright.async_api import Error as PWError
from playwright.async_api import Route

from browser_sdk.browser.base import NetworkInterceptor
from browser_sdk.errors import E_NETWORK_HEADERS_FAILED, E_NETWORK_ROUTE_FAILED, E_NETWORK_ROUTE_REMOVE_FAILED
from browser_sdk.models import RouteResponse, OperationContext, TrackedRequest
from browser_sdk.result import Result

logger = logging.getLogger(__name__)


class LocalNetworkInterceptor(NetworkInterceptor):
    """Network interception via Playwright context routing."""

    def __init__(
        self,
        pw_context: PWBrowserContext,
        max_tracked: int = 1000,
    ) -> None:
        self._context = pw_context
        self._max_tracked = max_tracked
        self._tracked: deque[TrackedRequest] = deque(maxlen=max_tracked)
        self._tracking_enabled = False
        self._routes: dict[str, Any] = {}
        self._scoped_headers: dict[str, dict[str, str]] = {}

    async def add_route(
        self, url_pattern: str,
        response: RouteResponse | None = None,
        abort: bool = False,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            async def handler(route: Route) -> None:
                if abort:
                    await route.abort()
                elif response:
                    await route.fulfill(
                        status=response.status,
                        headers={**response.headers, "content-type": response.content_type},
                        body=response.body if isinstance(response.body, bytes)
                        else response.body.encode(),
                    )
                else:
                    await route.continue_()

            await self._context.route(url_pattern, handler)
            self._routes[url_pattern] = handler
            return Result.ok()
        except PWError as e:
            return Result.fail(E_NETWORK_ROUTE_FAILED, str(e))

    async def remove_route(
        self, url_pattern: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            handler = self._routes.pop(url_pattern, None)
            if handler:
                await self._context.unroute(url_pattern, handler)
            return Result.ok()
        except PWError as e:
            return Result.fail(E_NETWORK_ROUTE_REMOVE_FAILED, str(e))

    async def set_extra_headers(
        self, headers: dict[str, str],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._context.set_extra_http_headers(headers)
            return Result.ok()
        except PWError as e:
            return Result.fail(E_NETWORK_HEADERS_FAILED, str(e))

    async def set_scoped_headers(
        self, origin: str, headers: dict[str, str],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        self._scoped_headers[origin] = headers
        # Apply via route that modifies matching requests
        pattern = f"**{origin}/**" if not origin.startswith("*") else origin
        try:
            async def scoped_handler(route: Route) -> None:
                await route.continue_(headers={**route.request.headers, **headers})

            await self._context.route(pattern, scoped_handler)
            return Result.ok()
        except PWError as e:
            return Result.fail(E_NETWORK_HEADERS_FAILED, str(e))

    async def enable_tracking(
        self, ctx: OperationContext | None = None,
    ) -> Result[None]:
        if self._tracking_enabled:
            return Result.ok()
        self._tracking_enabled = True

        for page in self._context.pages:
            self._attach_tracking(page)
        self._context.on("page", lambda p: self._attach_tracking(p))
        return Result.ok()

    def _attach_tracking(self, page) -> None:
        pending: dict[str, TrackedRequest] = {}

        def on_request(request):
            entry = TrackedRequest(
                url=request.url,
                method=request.method,
                request_headers=dict(request.headers),
                resource_type=request.resource_type,
                timestamp=time.time(),
            )
            pending[request.url + request.method] = entry
            self._tracked.append(entry)

        def on_response(response):
            key = response.url + response.request.method
            entry = pending.pop(key, None)
            if entry:
                entry.status = response.status
                entry.response_headers = dict(response.headers)
                entry.duration_ms = (time.time() - entry.timestamp) * 1000

        page.on("request", on_request)
        page.on("response", on_response)

    async def get_requests(
        self, filter: str | None = None, limit: int = 100,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[TrackedRequest]]:
        results = list(self._tracked)
        if filter:
            results = [r for r in results if filter in r.url]
        return Result.ok(results[-limit:])

    async def clear_requests(
        self, ctx: OperationContext | None = None,
    ) -> Result[None]:
        self._tracked.clear()
        return Result.ok()

    async def export_har(
        self, save_path: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        entries = []
        for req in self._tracked:
            started = datetime.fromtimestamp(req.timestamp, tz=timezone.utc).isoformat()
            entry: dict[str, Any] = {
                "startedDateTime": started,
                "time": req.duration_ms or 0,
                "request": {
                    "method": req.method,
                    "url": req.url,
                    "httpVersion": "HTTP/1.1",
                    "headers": [{"name": k, "value": v} for k, v in req.request_headers.items()],
                    "queryString": [],
                    "headersSize": -1,
                    "bodySize": -1,
                },
                "response": {
                    "status": req.status or 0,
                    "statusText": "",
                    "httpVersion": "HTTP/1.1",
                    "headers": [{"name": k, "value": v} for k, v in (req.response_headers or {}).items()],
                    "content": {"size": -1, "mimeType": ""},
                    "redirectURL": "",
                    "headersSize": -1,
                    "bodySize": -1,
                },
                "cache": {},
                "timings": {
                    "send": 0,
                    "wait": req.duration_ms or 0,
                    "receive": 0,
                },
            }
            entries.append(entry)

        har = {
            "log": {
                "version": "1.2",
                "creator": {"name": "browser-sdk", "version": "1.0"},
                "entries": entries,
            }
        }
        try:
            with open(save_path, "w") as f:
                json.dump(har, f, indent=2)
            return Result.ok()
        except OSError as e:
            return Result.fail(E_NETWORK_ROUTE_FAILED, str(e))
