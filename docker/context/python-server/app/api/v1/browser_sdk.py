from __future__ import annotations

import io
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from app.core.service_container import services
from app.models.browser_sdk import (
    CaptchaWaitResult,
    CaptchaWaitRequest,
    CheckRequest,
    ClickRequest,
    CookieSetRequest,
    CreatePageRequest,
    EvaluateRequest,
    ExportConsoleLogsRequest,
    ExportHarRequest,
    FillRequest,
    FindTextRequest,
    FormFillRequest,
    HeadersRequest,
    HotKeyRequest,
    HoverRequest,
    KeyRequest,
    NavigateRequest,
    NetworkRouteRemoveRequest,
    NetworkRouteRequest,
    RecordRequest,
    RestartRequest,
    ScopedHeadersRequest,
    ScrollRequest,
    ScrollToElementRequest,
    ScrollToRequest,
    SelectOptionRequest,
    StateLoadRequest,
    StateSaveRequest,
    TypeTextRequest,
    UploadFileRequest,
    WaitRequest,
)
from app.schemas.response import Response
from app.services.browser_sdk import BrowserSDKService


logger = logging.getLogger(__name__)


class _BrowserSDKRoute(APIRoute):
    """Custom route class that catches RuntimeError from browser SDK service
    and returns a proper JSON error response instead of HTTP 500."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RuntimeError as e:
                logger.warning('Browser SDK error: %s', e)
                body = Response(success=False, message=str(e))
                return JSONResponse(content=body.model_dump())

        return handler


router = APIRouter(route_class=_BrowserSDKRoute)


def _get_svc() -> BrowserSDKService:
    svc: BrowserSDKService = services.get('browser_sdk_service')
    if svc is None:
        raise HTTPException(status_code=503, detail='BrowserSDK service not available')
    return svc


def _ok(svc: BrowserSDKService, data=None) -> Response:
    """Build a success Response with optional tab-change hint.

    If a new tab was opened during the operation, the agent sees
    a human-readable ``hint`` string describing the change.
    """
    hint = svc.collect_hints()
    return Response(success=True, data=data, hint=hint)



# ══════════════════════════════════════════════════════════════════════
# Page operations  →  /page/*
# ══════════════════════════════════════════════════════════════════════

# ── Navigation ──────────────────────────────────────────────────────

@router.post(
    '/page/navigate',
    response_model=Response[dict],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'navigate',
    },
)
async def navigate(req: NavigateRequest):
    """Navigate the current page to a URL."""
    svc = _get_svc()
    result = await svc.navigate(req.url, req.wait_until, req.timeout)
    data = result.model_dump() if result else None
    return _ok(svc, data=data)


@router.post(
    '/page/back',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'back',
    },
)
async def go_back():
    """Go back in browser history."""
    svc = _get_svc()
    result = await svc.go_back()
    data = result.model_dump() if result else None
    return _ok(svc, data=data)


@router.post(
    '/page/forward',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'forward',
    },
)
async def go_forward():
    """Go forward in browser history."""
    svc = _get_svc()
    result = await svc.go_forward()
    data = result.model_dump() if result else None
    return _ok(svc, data=data)


@router.post(
    '/page/reload',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'reload',
    },
)
async def reload():
    """Reload the current page."""
    svc = _get_svc()
    await svc.reload()
    return _ok(svc)


# ── Interaction ─────────────────────────────────────────────────────

@router.post(
    '/page/click',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'click',
    },
)
async def click(req: ClickRequest):
    """Click an element by selector, index, or coordinates."""
    svc = _get_svc()
    await svc.click(
        selector=req.selector,
        index=req.index,
        x=req.x,
        y=req.y,
        button=req.button,
        click_count=req.click_count,
    )
    return _ok(svc)


@router.post(
    '/page/fill',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'fill',
    },
)
async def fill(req: FillRequest):
    """Fill an input field."""
    svc = _get_svc()
    await svc.fill(text=req.text, selector=req.selector, index=req.index)
    return _ok(svc)


@router.post(
    '/page/type',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'type_text',
    },
)
async def type_text(req: TypeTextRequest):
    """Type text with optional delay between keystrokes."""
    svc = _get_svc()
    await svc.type_text(text=req.text, delay=req.delay)
    return _ok(svc)


@router.post(
    '/page/press_key',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'press_key',
    },
)
async def press_key(req: KeyRequest):
    """Press a single key."""
    svc = _get_svc()
    await svc.press_key(key=req.key)
    return _ok(svc)


@router.post(
    '/page/hot_key',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'hot_key',
    },
)
async def hot_key(req: HotKeyRequest):
    """Press a key combination."""
    svc = _get_svc()
    await svc.hot_key(keys=req.keys)
    return _ok(svc)


@router.post(
    '/page/hover',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'hover',
    },
)
async def hover(req: HoverRequest):
    """Hover over an element by selector or coordinates."""
    svc = _get_svc()
    await svc.hover(selector=req.selector, x=req.x, y=req.y)
    return _ok(svc)


@router.post(
    '/page/select_option',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'select_option',
    },
)
async def select_option(req: SelectOptionRequest):
    """Select an option in a dropdown."""
    svc = _get_svc()
    await svc.select_option(
        selector=req.selector, value=req.value, label=req.label, index=req.index
    )
    return _ok(svc)


@router.post(
    '/page/check',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'check',
    },
)
async def check(req: CheckRequest):
    """Check a checkbox."""
    svc = _get_svc()
    await svc.check(selector=req.selector)
    return _ok(svc)


@router.post(
    '/page/uncheck',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'uncheck',
    },
)
async def uncheck(req: CheckRequest):
    """Uncheck a checkbox."""
    svc = _get_svc()
    await svc.uncheck(selector=req.selector)
    return _ok(svc)


@router.post(
    '/page/upload_file',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'upload_file',
    },
)
async def upload_file(req: UploadFileRequest):
    """Upload files to a file input element."""
    svc = _get_svc()
    await svc.upload_file(selector=req.selector, files=req.files)
    return _ok(svc)


@router.post(
    '/page/fill_form',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'fill_form',
    },
)
async def fill_form(req: FormFillRequest):
    """Batch fill multiple form fields."""
    svc = _get_svc()
    await svc.fill_form(items=req.items)
    return _ok(svc)


# ── Scrolling ───────────────────────────────────────────────────────

@router.post(
    '/page/scroll',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'scroll',
    },
)
async def scroll(req: ScrollRequest):
    """Scroll the page in a direction."""
    svc = _get_svc()
    await svc.scroll(direction=req.direction, amount=req.amount)
    return _ok(svc)


@router.post(
    '/page/scroll_to',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'scroll_to',
    },
)
async def scroll_to(req: ScrollToRequest):
    """Scroll to an absolute position."""
    svc = _get_svc()
    await svc.scroll_to(x=req.x, y=req.y)
    return _ok(svc)


@router.post(
    '/page/scroll_to_element',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'scroll_to_element',
    },
)
async def scroll_to_element(req: ScrollToElementRequest):
    """Scroll an element into view."""
    svc = _get_svc()
    await svc.scroll_to_element(selector=req.selector)
    return _ok(svc)


# ── Content ─────────────────────────────────────────────────────────

@router.get(
    '/page/screenshot',
    response_class=StreamingResponse,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'screenshot',
    },
    responses={
        200: {
            'description': 'Page-level screenshot via Playwright',
            'content': {'image/png': {}},
        }
    },
)
async def page_screenshot(
    full_page: bool = Query(False),
    format: str = Query('png'),
    quality: int | None = Query(None),
):
    """Capture a page screenshot."""
    svc = _get_svc()
    img_bytes = await svc.screenshot(
        full_page=full_page, format=format, quality=quality
    )
    media_type = 'image/jpeg' if format == 'jpeg' else 'image/png'
    return StreamingResponse(io.BytesIO(img_bytes), media_type=media_type)


@router.post(
    '/page/record',
    response_model=Response[dict],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'record',
    },
)
async def page_record(req: RecordRequest):
    """Record page screencast (once/start/pause/resume/stop/status)."""
    svc = _get_svc()
    result = await svc.record(
        action=req.action,
        save_path=req.save_path,
        duration=req.duration,
        fps=req.fps,
        quality=req.quality,
    )
    data = result.model_dump() if result else None
    return _ok(svc, data=data)


@router.get(
    '/page/html',
    response_model=Response[str],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'get_html',
    },
)
async def get_html(outer: bool = Query(False)):
    """Get the page HTML content."""
    svc = _get_svc()
    html = await svc.get_html(outer=outer)
    return _ok(svc, data=html)


@router.get(
    '/page/text',
    response_model=Response[str],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'get_text',
    },
)
async def get_text():
    """Get all visible text from the page."""
    svc = _get_svc()
    text = await svc.get_text()
    return _ok(svc, data=text)


@router.get(
    '/page/markdown',
    response_model=Response[dict],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'get_markdown',
    },
)
async def get_markdown():
    """Get the page content as Markdown using Readability and Turndown."""
    svc = _get_svc()
    result = await svc.get_markdown()
    return _ok(svc, data=result)


@router.get(
    '/page/elements',
    response_model=Response[list],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'get_elements',
    },
)
async def get_interactive_elements():
    """Get all interactive elements on the page."""
    svc = _get_svc()
    elements = await svc.get_interactive_elements()
    data = [el.model_dump() for el in elements] if elements else []
    return _ok(svc, data=data)


@router.get(
    '/page/console',
    response_model=Response[list],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'get_console',
    },
)
async def get_console_logs(clear: bool = Query(False)):
    """Get captured browser console log messages."""
    svc = _get_svc()
    logs = await svc.get_console_logs(clear=clear)
    data = [log.model_dump() for log in logs] if logs else []
    return _ok(svc, data=data)


@router.post(
    '/page/console/export',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'export_console',
    },
)
async def export_console_logs(req: ExportConsoleLogsRequest):
    """Export console logs to a JSON file."""
    svc = _get_svc()
    await svc.export_console_logs(save_path=req.save_path, clear=req.clear)
    return _ok(svc)


@router.post(
    '/page/evaluate',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'evaluate',
    },
)
async def evaluate(req: EvaluateRequest):
    """Execute JavaScript and return the result."""
    svc = _get_svc()
    result = await svc.evaluate(expression=req.expression)
    return _ok(svc, data=result)


@router.post(
    '/page/find_text',
    response_model=Response[list],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'find_text',
    },
)
async def find_text(req: FindTextRequest):
    """Find text occurrences on the page."""
    svc = _get_svc()
    matches = await svc.find_text(keyword=req.keyword)
    data = [m.model_dump() for m in matches] if matches else []
    return _ok(svc, data=data)


# ── Unified wait ────────────────────────────────────────────────────

@router.post(
    '/page/wait',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.page',
        'x-fern-sdk-method-name': 'wait',
    },
)
async def wait(req: WaitRequest):
    """Unified wait: selector, load, url, network_idle, download, function, response, request, or timeout."""
    svc = _get_svc()
    if req.type == 'selector':
        if not req.selector:
            raise HTTPException(
                status_code=422,
                detail='selector is required when type is "selector"',
            )
        await svc.wait_for_selector(
            selector=req.selector, state=req.state or 'visible', timeout=req.timeout
        )
        return _ok(svc)
    elif req.type == 'load':
        await svc.wait_for_load(state=req.state or 'load', timeout=req.timeout)
        return _ok(svc)
    elif req.type == 'url':
        if not req.url:
            raise HTTPException(
                status_code=422,
                detail='url is required when type is "url"',
            )
        await svc.wait_for_url(url=req.url, timeout=req.timeout)
        return _ok(svc)
    elif req.type == 'network_idle':
        await svc.wait_for_network_idle(timeout=req.timeout)
        return _ok(svc)
    elif req.type == 'download':
        result = await svc.wait_for_download(
            save_path=req.save_path, timeout=req.timeout
        )
        data = result.model_dump() if result else None
        return _ok(svc, data=data)
    elif req.type == 'function':
        if not req.expression:
            raise HTTPException(
                status_code=422,
                detail='expression is required when type is "function"',
            )
        polling: float | str = req.polling if req.polling is not None else 'raf'
        result = await svc.wait_for_function(
            expression=req.expression, timeout=req.timeout, polling=polling
        )
        return _ok(svc, data=result)
    elif req.type == 'response':
        if not req.url_pattern:
            raise HTTPException(
                status_code=422,
                detail='url_pattern is required when type is "response"',
            )
        result = await svc.wait_for_response(
            url_pattern=req.url_pattern, timeout=req.timeout
        )
        return _ok(svc, data=result)
    elif req.type == 'request':
        if not req.url_pattern:
            raise HTTPException(
                status_code=422,
                detail='url_pattern is required when type is "request"',
            )
        result = await svc.wait_for_request(
            url_pattern=req.url_pattern, timeout=req.timeout
        )
        return _ok(svc, data=result)
    elif req.type == 'timeout':
        import asyncio
        await asyncio.sleep(req.timeout)
        return _ok(svc)


# ══════════════════════════════════════════════════════════════════════
# Tabs  →  /tabs
# ══════════════════════════════════════════════════════════════════════

@router.get(
    '/tabs',
    response_model=Response[list],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.tabs',
        'x-fern-sdk-method-name': 'list',
    },
)
async def list_tabs():
    """List all open browser tabs."""
    svc = _get_svc()
    pages = await svc.list_pages()
    data = [p.model_dump() for p in pages] if pages else []
    return _ok(svc, data=data)


@router.post(
    '/tabs',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.tabs',
        'x-fern-sdk-method-name': 'create',
    },
)
async def create_tab(req: CreatePageRequest):
    """Create a new browser tab."""
    svc = _get_svc()
    await svc.create_page(url=req.url)
    pages = await svc.list_pages()
    active = next((p for p in pages if p.is_active), None)
    data = {"index": active.index} if active else None
    return _ok(svc, data=data)


@router.delete(
    '/tabs/{index}',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.tabs',
        'x-fern-sdk-method-name': 'close',
    },
)
async def close_tab(index: int):
    """Close a browser tab by index."""
    svc = _get_svc()
    await svc.close_page(index=index)
    return _ok(svc)


@router.put(
    '/tabs/{index}/activate',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.tabs',
        'x-fern-sdk-method-name': 'activate',
    },
)
async def activate_tab(index: int):
    """Activate (bring to front) a browser tab by index."""
    svc = _get_svc()
    await svc.activate_tab(index=index)
    return _ok(svc)


# ══════════════════════════════════════════════════════════════════════
# Cookies  →  /cookies
# ══════════════════════════════════════════════════════════════════════

@router.get(
    '/cookies',
    response_model=Response[list],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.cookies',
        'x-fern-sdk-method-name': 'get_cookies',
    },
)
async def get_cookies(urls: str | None = Query(None)):
    """Get browser cookies, optionally filtered by URLs (comma-separated)."""
    svc = _get_svc()
    url_list = urls.split(',') if urls else None
    cookies = await svc.get_cookies(urls=url_list)
    data = [c.model_dump() for c in cookies] if cookies else []
    return Response(success=True, data=data)


@router.post(
    '/cookies',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.cookies',
        'x-fern-sdk-method-name': 'set_cookies',
    },
)
async def set_cookies(req: CookieSetRequest):
    """Set browser cookies."""
    svc = _get_svc()
    await svc.set_cookies(cookies=req.cookies)
    return Response(success=True)


@router.delete(
    '/cookies',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.cookies',
        'x-fern-sdk-method-name': 'clear_cookies',
    },
)
async def clear_cookies():
    """Clear all browser cookies."""
    svc = _get_svc()
    await svc.clear_cookies()
    return Response(success=True)


# ══════════════════════════════════════════════════════════════════════
# State  →  /state/*
# ══════════════════════════════════════════════════════════════════════

@router.post(
    '/state/save',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.state',
        'x-fern-sdk-method-name': 'save',
    },
)
async def save_state(req: StateSaveRequest):
    """Save browser state (cookies, localStorage, etc.) to a file."""
    svc = _get_svc()
    await svc.save_state(path=req.path)
    return Response(success=True)


@router.post(
    '/state/load',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.state',
        'x-fern-sdk-method-name': 'load',
    },
)
async def load_state(req: StateLoadRequest):
    """Load browser state from a previously saved file."""
    svc = _get_svc()
    await svc.load_state(path=req.path)
    return Response(success=True)


# ══════════════════════════════════════════════════════════════════════
# Network  →  /network/*
# ══════════════════════════════════════════════════════════════════════

@router.post(
    '/network/headers',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.network',
        'x-fern-sdk-method-name': 'set_headers',
    },
)
async def set_extra_headers(req: HeadersRequest):
    """Set extra HTTP headers for all subsequent requests."""
    svc = _get_svc()
    await svc.set_extra_headers(headers=req.headers)
    return Response(success=True)


@router.post(
    '/network/scoped_headers',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.network',
        'x-fern-sdk-method-name': 'set_scoped_headers',
    },
)
async def set_scoped_headers(req: ScopedHeadersRequest):
    """Set HTTP headers scoped to a specific origin."""
    svc = _get_svc()
    await svc.set_scoped_headers(origin=req.origin, headers=req.headers)
    return Response(success=True)


@router.post(
    '/network/route',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.network',
        'x-fern-sdk-method-name': 'add_route',
    },
)
async def add_route(req: NetworkRouteRequest):
    """Add a network route to mock or abort matching requests."""
    svc = _get_svc()
    await svc.add_route(
        url_pattern=req.url_pattern, response=req.response, abort=req.abort
    )
    return Response(success=True)


@router.delete(
    '/network/route',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.network',
        'x-fern-sdk-method-name': 'remove_route',
    },
)
async def remove_route(req: NetworkRouteRemoveRequest):
    """Remove a previously added network route."""
    svc = _get_svc()
    await svc.remove_route(url_pattern=req.url_pattern)
    return Response(success=True)


@router.get(
    '/network/requests',
    response_model=Response[list],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.network',
        'x-fern-sdk-method-name': 'get_requests',
    },
)
async def get_requests(
    filter: str | None = Query(None),
    limit: int = Query(100),
):
    """Get tracked network requests."""
    svc = _get_svc()
    requests = await svc.get_requests(filter=filter, limit=limit)
    data = [r.model_dump() for r in requests] if requests else []
    return Response(success=True, data=data)


@router.post(
    '/network/export_har',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.network',
        'x-fern-sdk-method-name': 'export_har',
    },
)
async def export_har(req: ExportHarRequest):
    """Export tracked network requests as a HAR file."""
    svc = _get_svc()
    await svc.export_har(save_path=req.save_path)
    return Response(success=True)


# ══════════════════════════════════════════════════════════════════════
# CAPTCHA  →  /captcha/*
# ══════════════════════════════════════════════════════════════════════

@router.get(
    '/captcha/detect',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.captcha',
        'x-fern-sdk-method-name': 'detect',
    },
)
async def detect_captcha():
    """Detect CAPTCHA on the current page."""
    svc = _get_svc()
    result = await svc.detect_captcha()
    data = result.model_dump() if result else None
    return Response(success=True, data=data)


@router.post(
    '/captcha/wait',
    response_model=Response[CaptchaWaitResult],
    openapi_extra={
        'x-fern-sdk-group-name': 'browser.captcha',
        'x-fern-sdk-method-name': 'wait',
    },
)
async def wait_for_captcha(req: CaptchaWaitRequest):
    """Wait for CAPTCHA to be resolved."""
    svc = _get_svc()
    resolved = await svc.wait_for_captcha_resolution(
        timeout=req.timeout, poll_interval=req.poll_interval
    )
    data = CaptchaWaitResult(resolved=resolved)
    return Response(success=True, data=data)


# ══════════════════════════════════════════════════════════════════════
# Session  →  /restart
# ══════════════════════════════════════════════════════════════════════

@router.post(
    '/restart',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser',
        'x-fern-sdk-method-name': 'restart',
    },
)
async def restart(req: RestartRequest | None = None):
    """Restart the browser session.

    - **soft** (default): Reconnect the Playwright session only.
    - **hard**: Restart the browser process via supervisorctl, optionally
      updating URL blocklist/allowlist policies before restart.
    """
    svc = _get_svc()
    if req is None:
        req = RestartRequest()
    await svc.restart(
        mode=req.mode,
        url_blocklist=req.url_blocklist,
        url_allowlist=req.url_allowlist,
        locale=req.locale,
    )
    return Response(success=True)


# ══════════════════════════════════════════════════════════════════════
# Proxy PAC  →  /proxy.pac
# ══════════════════════════════════════════════════════════════════════

@router.get(
    '/proxy.pac',
    response_class=StreamingResponse,
    openapi_extra={
        'x-fern-sdk-group-name': 'browser',
        'x-fern-sdk-method-name': 'get_proxy_pac',
    },
    responses={
        200: {
            'description': 'Proxy Auto-Configuration file',
            'content': {'application/x-ns-proxy-autoconfig': {}},
        }
    },
)
async def get_proxy_pac():
    """Return the current PAC file for proxy split-routing (if configured)."""
    import os
    pac_path = '/opt/gem/proxy.pac'
    if not os.path.isfile(pac_path):
        raise HTTPException(
            status_code=404,
            detail='No PAC file configured. Set PROXY_SERVER + PROXY_BYPASS_LIST.',
        )
    with open(pac_path) as f:
        content = f.read()
    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type='application/x-ns-proxy-autoconfig',
    )
