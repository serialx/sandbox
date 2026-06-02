from __future__ import annotations

import asyncio
import functools
import ipaddress
import json
import logging
import os
import shutil
from typing import Any, Literal
from urllib.parse import urlsplit

from app.logging.decorators import trace_api


logger = logging.getLogger(__name__)


WaitUntil = Literal['load', 'domcontentloaded', 'networkidle', 'commit']


# Error messages that indicate the browser/context was closed externally.
# Used as a fallback when Playwright's internal _is_closed_or_closing flag
# has not been updated yet (e.g. browser process killed by supervisord).
_CLOSED_MARKERS = (
    'has been closed',
    'target closed',
    'browser disconnected',
    'connection refused',
    'not connected',
)


def _looks_like_closed_error(exc: BaseException) -> bool:
    """Heuristic: does this exception look like a dead browser/context?"""
    msg = str(exc).lower()
    return any(marker in msg for marker in _CLOSED_MARKERS)


def _resolve_agent_browser_cmd() -> str:
    explicit = os.environ.get('AGENT_BROWSER_CMD', '').strip()
    if explicit:
        if os.sep in explicit:
            if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
                return explicit
        else:
            resolved = shutil.which(explicit)
            if resolved:
                return resolved

    for candidate in (
        '/opt/gem/bin/agent-browser',
        '/opt/gem/agent-browser-wrapper.sh',
        shutil.which('agent-browser'),
        '/usr/local/bin/agent-browser',
    ):
        if not candidate:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return explicit or 'agent-browser'


def _auto_reconnect(fn):
    """Decorator: retry once with a fresh session when the browser context dies.

    Detection uses two complementary strategies:
    1. Proactive — ``_is_session_alive()`` checks Playwright internals.
    2. Reactive  — ``_looks_like_closed_error()`` matches known error messages
       for cases where Playwright's internal flag hasn't been updated yet
       (e.g. browser process killed externally).

    If either check indicates a stale session the operation is retried
    exactly once after reconnecting.  Other errors are re-raised immediately.
    """

    @functools.wraps(fn)
    async def wrapper(self: BrowserSDKService, *args, **kwargs):
        try:
            return await fn(self, *args, **kwargs)
        except Exception as exc:
            session_dead = (
                self._session is not None and not self._is_session_alive()
            )
            if not session_dead:
                session_dead = _looks_like_closed_error(exc)
            if not session_dead:
                # Session was already reset inside the call (e.g. _get_page
                # recovery failed) — just need to retry with a fresh session.
                session_dead = self._session is None
            if session_dead:
                logger.warning(
                    'Stale browser session detected after error, reconnecting: %s',
                    exc,
                )
                await self._reset_session()
                result = await fn(self, *args, **kwargs)
                # Ensure hint is set after reconnection — the agent's page
                # context has been lost even if the retry succeeded normally.
                if self._recovery_hint is None:
                    self._recovery_hint = (
                        'Browser session was lost and has been automatically reconnected. '
                        'Previous navigation state and page context have been lost.'
                    )
                return result
            raise

    return wrapper


class BrowserSDKService:
    """Service wrapping BrowserFlare for page-level browser automation via CDP."""

    def __init__(self):
        self._session = None
        self._flare = None
        self._lock = asyncio.Lock()
        self._locale: str = os.environ.get('BROWSER_LANG', 'en-US')
        self._download_dir: str = self._resolve_download_dir()
        self._recovery_hint: str | None = None

    @staticmethod
    def _resolve_download_dir() -> str:
        """Resolve browser download dir using the same semantics as browser services."""
        explicit = os.environ.get('BROWSER_DOWNLOAD_DIR', '').strip()
        if explicit:
            return explicit

        from app.utils import get_workspace

        return os.path.join(get_workspace(), 'Downloads')

    async def _reset_session(self) -> None:
        """Close stale session so the next _ensure_session creates a fresh one."""
        async with self._lock:
            if self._session is not None:
                try:
                    await self._session.close()
                except Exception:
                    pass
                self._session = None
                self._flare = None
                logger.info('BrowserSDKService: stale session cleared')

    def _is_session_alive(self) -> bool:
        """Check if the current session's browser context is still usable."""
        if self._session is None:
            return False
        browser = getattr(self._session, 'browser', None)
        if browser is None:
            return False
        pw_context = getattr(browser, '_pw_context', None)
        if pw_context is None:
            return False
        # Playwright marks closed contexts via _impl_obj
        impl = getattr(pw_context, '_impl_obj', None)
        if impl is not None:
            is_closing = getattr(impl, '_is_closed_or_closing', False)
            if is_closing:
                return False
        return True

    async def _wait_for_cdp(self, timeout: float = 10.0) -> None:
        """Poll until the CDP port is accepting connections.

        This handles the gap between browser process crash and supervisord
        restarting it — without this wait, reconnection would fail immediately
        with "connection refused".
        """
        import socket

        interval = 0.5
        elapsed = 0.0
        while elapsed < timeout:
            try:
                sock = socket.create_connection(('127.0.0.1', 9222), timeout=1)
                sock.close()
                return
            except (ConnectionRefusedError, socket.timeout, OSError):
                await asyncio.sleep(interval)
                elapsed += interval
        raise RuntimeError(
            f'Browser CDP port not available after {timeout}s — '
            f'browser process may have crashed and not restarted'
        )

    async def _ensure_session(self) -> Any:
        """Lazy-init: connect to local Chromium on first call.

        If the existing session has a closed browser context (e.g. Chromium
        was restarted by supervisord), the stale session is discarded and a
        fresh connection is established automatically.
        """
        was_stale = False
        if self._session is not None and not self._is_session_alive():
            logger.warning(
                'BrowserSDKService: stale session detected, reconnecting'
            )
            await self._reset_session()
            was_stale = True

        if self._session is None:
            async with self._lock:
                if self._session is None:
                    # If reconnecting after a stale session, wait for the
                    # browser process to be available (supervisord restart).
                    if was_stale:
                        await self._wait_for_cdp()

                    from browser_sdk import (
                        BrowserFlare,
                        BrowserFlareConfig,
                        LocalConfig,
                        SessionConfig,
                    )

                    os.makedirs(self._download_dir, exist_ok=True)
                    self._flare = BrowserFlare(
                        BrowserFlareConfig(
                            default_locale=self._locale,
                            download_dir=self._download_dir,
                        )
                    )
                    result = await self._flare.connect(
                        SessionConfig(
                            type='local',
                            local=LocalConfig(cdp_port=9222, cdp_host='127.0.0.1'),
                        )
                    )
                    if not result.success:
                        error_message = (
                            result.error.message if result.error else 'unknown error'
                        )
                        raise RuntimeError(
                            f'Failed to connect browser: {error_message}'
                        )
                    if result.data is None:
                        raise RuntimeError('Failed to connect browser: empty session')
                    self._session = result.data
                    logger.info('BrowserSDKService: session connected')
        session = self._session
        if session is None:
            raise RuntimeError('Browser session is not initialized')
        return session

    def _require_browser(self, session: Any) -> Any:
        browser = session.browser
        if browser is None:
            raise RuntimeError('Browser is not available in current session')
        return browser

    async def _get_page(self) -> Any:
        session = await self._ensure_session()
        browser = self._require_browser(session)
        result = await browser.pages.get_current()
        if not result.success:
            # No pages open — auto-create a blank page to recover.
            # This handles the case where all tabs were closed externally
            # (e.g. via VNC) and the browser restarted but has no pages.
            logger.warning('No current page available, creating a blank page to recover')
            create_result = await browser.pages.create()
            if not create_result.success:
                # Creation failed — session is likely dead (CDP broken).
                # Force-reset so _auto_reconnect will reconnect and retry.
                await self._reset_session()
                error_message = create_result.error.message if create_result.error else 'unknown error'
                raise RuntimeError(
                    f'Browser session lost — all pages were closed externally '
                    f'and reconnection is needed: {error_message}'
                )
            if create_result.data is None:
                await self._reset_session()
                raise RuntimeError(
                    'Browser session lost — all pages were closed externally '
                    'and reconnection is needed'
                )
            self._recovery_hint = (
                'All browser tabs were closed externally (e.g. via VNC). '
                'A blank page has been created automatically. '
                'Previous navigation state and page context have been lost.'
            )
            return create_result.data
        if result.data is None:
            raise RuntimeError('Failed to get current page: empty page data')
        return result.data

    def _unwrap(self, result):
        """Unwrap a Result[T] — return data on success, raise on failure."""
        if result.success:
            return result.data
        error_msg = result.error.message if result.error else 'Unknown browser error'
        raise RuntimeError(error_msg)

    def collect_hints(self) -> str | None:
        """Collect page-change hints after an operation.

        Returns a human-readable string describing tab changes (new tabs,
        active tab switch) or None if nothing changed.
        """
        # Drain recovery hint first — takes priority
        recovery = self._recovery_hint
        if recovery:
            self._recovery_hint = None
            return recovery
        if not self._session:
            return None
        browser = self._session.browser
        if browser is None:
            return None
        pages_mgr = browser.pages
        drain = getattr(pages_mgr, 'drain_page_events', None)
        if not drain:
            return None
        events = drain()
        if not events:
            return None
        active = getattr(pages_mgr, '_active', None)
        context = getattr(pages_mgr, '_context', None)
        pages = getattr(context, 'pages', None)
        if not isinstance(pages, list):
            return None
        tab_count = len(pages)
        # Build human-readable hint
        parts: list[str] = []

        # Classify events
        new_tabs = [e for e in events if e['type'] == 'new_tab']
        closed_tabs = [e for e in events if e['type'] == 'tab_closed']
        recovery = [e for e in events if e['type'] == 'recovery']

        if recovery:
            parts.append(
                'All browser tabs were closed externally. '
                'A blank page has been created automatically. '
                'Previous navigation state and page context have been lost.'
            )
        else:
            if closed_tabs:
                if len(closed_tabs) == 1:
                    parts.append(f'Tab closed externally: {closed_tabs[0]["url"]}')
                else:
                    descs = [e['url'] for e in closed_tabs]
                    parts.append(f'{len(closed_tabs)} tabs closed externally: {", ".join(descs)}')
            if new_tabs:
                if len(new_tabs) == 1:
                    ev = new_tabs[0]
                    parts.append(f'New tab #{ev["index"]} opened: {ev["url"]}')
                else:
                    descs = [f'#{ev["index"]} {ev["url"]}' for ev in new_tabs]
                    parts.append(f'{len(new_tabs)} new tabs opened: {", ".join(descs)}')

        if active and active in pages:
            idx = next((i for i, p in enumerate(pages) if p is active), -1)
            parts.append(f'now active: tab #{idx} ({active.url})')
        parts.append(f'{tab_count} tabs total')
        return ' | '.join(parts)

    # ── Navigation ──────────────────────────────────────────────────

    @staticmethod
    def _should_default_to_http(hostname: str | None) -> bool:
        """Use http:// for local loopback hosts by default."""
        if not hostname:
            return False
        host = hostname.lower()
        if host == 'localhost':
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Add URL scheme when missing.

        Default to http:// for local loopback hosts (e.g. localhost:8080),
        and https:// for other hostnames.
        """
        normalized = url.strip()
        if not normalized:
            return normalized

        parsed = urlsplit(normalized)
        non_hierarchical_schemes = {
            'about',
            'data',
            'file',
            'mailto',
            'javascript',
            'blob',
        }
        if parsed.scheme and (
            '://' in normalized or parsed.scheme.lower() in non_hierarchical_schemes
        ):
            return normalized

        if normalized.startswith('//'):
            return f'https:{normalized}'

        probe = urlsplit(f'//{normalized}')
        scheme = (
            'http://'
            if BrowserSDKService._should_default_to_http(probe.hostname)
            else 'https://'
        )
        return f'{scheme}{normalized}'

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def navigate(
        self, url: str, wait_until: WaitUntil = 'load', timeout: float = 30.0
    ):
        from browser_sdk import OpenOptions

        url = self._normalize_url(url)
        page = await self._get_page()
        result = await page.open(
            url, options=OpenOptions(wait_until=wait_until, timeout=timeout)
        )
        return self._unwrap(result)

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def go_back(self):
        page = await self._get_page()
        return self._unwrap(await page.back())

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def go_forward(self):
        page = await self._get_page()
        return self._unwrap(await page.forward())

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def reload(self):
        page = await self._get_page()
        return self._unwrap(await page.reload())

    # ── Interaction ─────────────────────────────────────────────────

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def click(
        self,
        selector: str | None = None,
        index: int | None = None,
        x: float | None = None,
        y: float | None = None,
        button: str = 'left',
        click_count: int = 1,
    ):
        page = await self._get_page()
        return self._unwrap(
            await page.click(
                selector=selector,
                index=index,
                x=x,
                y=y,
                button=button,
                click_count=click_count,
            )
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def fill(
        self,
        text: str,
        selector: str | None = None,
        index: int | None = None,
    ):
        page = await self._get_page()
        return self._unwrap(await page.fill(text=text, selector=selector, index=index))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def type_text(self, text: str, delay: float = 0):
        page = await self._get_page()
        return self._unwrap(await page.type_text(text=text, delay=delay))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def press_key(self, key: str):
        page = await self._get_page()
        return self._unwrap(await page.press_key(key=key))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def hot_key(self, keys: list[str]):
        page = await self._get_page()
        return self._unwrap(await page.hot_key(keys=keys))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def hover(
        self,
        selector: str | None = None,
        x: float | None = None,
        y: float | None = None,
    ):
        page = await self._get_page()
        return self._unwrap(await page.hover(selector=selector, x=x, y=y))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def select_option(
        self,
        selector: str,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
    ):
        page = await self._get_page()
        return self._unwrap(
            await page.select_option(
                selector=selector, value=value, label=label, index=index
            )
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def check(self, selector: str):
        page = await self._get_page()
        return self._unwrap(await page.check(selector=selector))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def uncheck(self, selector: str):
        page = await self._get_page()
        return self._unwrap(await page.uncheck(selector=selector))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def upload_file(self, selector: str, files: list[str]):
        page = await self._get_page()
        return self._unwrap(await page.upload_file(selector=selector, files=files))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def fill_form(self, items: list[dict[str, Any]]):
        from browser_sdk import FormFillItem

        page = await self._get_page()
        form_items = [FormFillItem(**item) for item in items]
        return self._unwrap(await page.fill_form(items=form_items))

    # ── Scrolling ───────────────────────────────────────────────────

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def scroll(self, direction: str = 'down', amount: int = 300):
        page = await self._get_page()
        return self._unwrap(await page.scroll(direction=direction, amount=amount))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def scroll_to(self, x: int = 0, y: int = 0):
        page = await self._get_page()
        return self._unwrap(await page.scroll_to(x=x, y=y))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def scroll_to_element(self, selector: str):
        page = await self._get_page()
        return self._unwrap(await page.scroll_to_element(selector=selector))

    # ── Content ─────────────────────────────────────────────────────

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def screenshot(
        self,
        full_page: bool = False,
        format: str = 'png',
        quality: int | None = None,
    ) -> bytes:
        page = await self._get_page()
        return self._unwrap(
            await page.screenshot(full_page=full_page, format=format, quality=quality)
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def record(
        self,
        action: str = 'once',
        save_path: str | None = None,
        duration: float = 10.0,
        fps: int = 15,
        quality: int = 80,
    ):
        page = await self._get_page()
        return self._unwrap(
            await page.record(
                action=action,
                save_path=save_path,
                duration=duration,
                fps=fps,
                quality=quality,
            )
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def get_html(self, outer: bool = False) -> str:
        page = await self._get_page()
        return self._unwrap(await page.get_html(outer=outer))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def get_text(self) -> str:
        page = await self._get_page()
        return self._unwrap(await page.get_text())

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def get_markdown(self) -> dict[str, Any]:
        page = await self._get_page()
        result = self._unwrap(await page.get_markdown())
        title = result.get('title', '')
        markdown = result.get('markdown', '')
        result['markdown'] = f'Title: {title}\nContent:\n{markdown}'
        return result

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def evaluate(self, expression: str):
        page = await self._get_page()
        return self._unwrap(await page.evaluate(expression=expression))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def get_interactive_elements(self):
        page = await self._get_page()
        return self._unwrap(await page.get_interactive_elements())

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def find_text(self, keyword: str):
        page = await self._get_page()
        return self._unwrap(await page.find_text(keyword=keyword))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def get_console_logs(self, clear: bool = False):
        page = await self._get_page()
        return self._unwrap(await page.get_console_logs(clear=clear))

    # ── Wait ────────────────────────────────────────────────────────

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def wait_for_selector(
        self, selector: str, state: str = 'visible', timeout: float = 30.0
    ):
        page = await self._get_page()
        return self._unwrap(
            await page.wait_for_selector(
                selector=selector, state=state, timeout=timeout
            )
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def wait_for_load(self, state: str = 'load', timeout: float = 30.0):
        page = await self._get_page()
        return self._unwrap(await page.wait_for_load(state=state, timeout=timeout))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def wait_for_url(self, url: str, timeout: float = 30.0):
        page = await self._get_page()
        return self._unwrap(await page.wait_for_url(url=url, timeout=timeout))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def wait_for_network_idle(self, timeout: float = 30.0):
        page = await self._get_page()
        return self._unwrap(await page.wait_for_network_idle(timeout=timeout))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def wait_for_function(
        self, expression: str, timeout: float = 30.0, polling: float | str = 'raf'
    ):
        page = await self._get_page()
        return self._unwrap(
            await page.wait_for_function(expression=expression, timeout=timeout, polling=polling)
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def wait_for_response(self, url_pattern: str, timeout: float = 30.0):
        page = await self._get_page()
        return self._unwrap(
            await page.wait_for_response(url_pattern=url_pattern, timeout=timeout)
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def wait_for_request(self, url_pattern: str, timeout: float = 30.0):
        page = await self._get_page()
        return self._unwrap(
            await page.wait_for_request(url_pattern=url_pattern, timeout=timeout)
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def wait_for_download(
        self, save_path: str | None = None, timeout: float = 30.0
    ):
        page = await self._get_page()
        return self._unwrap(
            await page.wait_for_download(save_path=save_path, timeout=timeout)
        )

    # ── Pages (tabs) ────────────────────────────────────────────────

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def list_pages(self):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(await browser.pages.list())

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def create_page(self, url: str | None = None):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(await browser.pages.create(url=url))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def close_page(self, index: int | None = None):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(await browser.pages.close(index=index))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def activate_tab(self, index: int):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        pages_mgr = browser.pages
        all_pages = pages_mgr._context.pages
        # Auto-recover when all pages were closed externally (e.g. via VNC)
        if not all_pages:
            logger.warning('No pages available for activate_tab, creating a blank page to recover')
            create_result = await pages_mgr.create()
            if not create_result.success:
                await self._reset_session()
                error_message = create_result.error.message if create_result.error else 'unknown error'
                raise RuntimeError(
                    f'Browser session lost — all pages were closed externally '
                    f'and reconnection is needed: {error_message}'
                )
            all_pages = pages_mgr._context.pages
            self._recovery_hint = (
                'All browser tabs were closed externally (e.g. via VNC). '
                'A blank page has been created automatically. '
                'Previous navigation state and page context have been lost.'
            )
        if index < 0 or index >= len(all_pages):
            raise RuntimeError(f'Tab index {index} out of range (0-{len(all_pages) - 1})')
        pages_mgr.set_active(index)
        await all_pages[index].bring_to_front()

    # ── Cookies ─────────────────────────────────────────────────────

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def get_cookies(self, urls: list[str] | None = None):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(await browser.cookies.get(urls=urls))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def set_cookies(self, cookies: list[dict[str, Any]]):
        from browser_sdk import CookieParam

        session = await self._ensure_session()
        browser = self._require_browser(session)
        cookie_params = [CookieParam(**c) for c in cookies]
        return self._unwrap(await browser.cookies.set(cookies=cookie_params))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def clear_cookies(self):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(await browser.cookies.clear())

    # ── State ───────────────────────────────────────────────────────

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def save_state(self, path: str):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(await browser.cookies.save_state(path))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def load_state(self, path: str):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(await browser.cookies.load_state(path))

    # ── Network ─────────────────────────────────────────────────────

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def set_extra_headers(self, headers: dict[str, str]):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(
            await browser.network.set_extra_headers(headers=headers)
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def set_scoped_headers(self, origin: str, headers: dict[str, str]):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(
            await browser.network.set_scoped_headers(
                origin=origin, headers=headers
            )
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def add_route(
        self,
        url_pattern: str,
        response: Any = None,
        abort: bool = False,
    ):
        from browser_sdk import RouteResponse

        session = await self._ensure_session()
        browser = self._require_browser(session)
        mock = RouteResponse(**response.model_dump()) if response else None
        return self._unwrap(
            await browser.network.add_route(
                url_pattern=url_pattern, response=mock, abort=abort
            )
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def remove_route(self, url_pattern: str):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(
            await browser.network.remove_route(url_pattern=url_pattern)
        )

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def get_requests(self, filter: str | None = None, limit: int = 100):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(
            await browser.network.get_requests(filter=filter, limit=limit)
        )

    # ── Export ──────────────────────────────────────────────────────

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def export_har(self, save_path: str):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(await browser.network.export_har(save_path))

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def export_console_logs(self, save_path: str, clear: bool = False):
        page = await self._get_page()
        logs = self._unwrap(await page.get_console_logs(clear=clear))
        data = [log.model_dump() for log in logs] if logs else []
        with open(save_path, 'w') as f:
            json.dump(data, f, indent=2)

    # ── CAPTCHA ─────────────────────────────────────────────────────

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def detect_captcha(self):
        session = await self._ensure_session()
        browser = self._require_browser(session)
        return self._unwrap(await browser.captcha.detect())

    @trace_api('browser_sdk')
    @_auto_reconnect
    async def wait_for_captcha_resolution(
        self, timeout: float = 60.0, poll_interval: float = 2.0
    ) -> bool:
        session = await self._ensure_session()
        browser = self._require_browser(session)
        resolved = self._unwrap(
            await browser.captcha.wait_for_resolution(
                timeout=timeout, poll_interval=poll_interval
            )
        )
        return bool(resolved)

    # ── Lifecycle ───────────────────────────────────────────────────

    async def close(self):
        """Close session and release resources."""
        if self._session is not None:
            try:
                await self._session.close()
            except Exception as e:
                logger.warning(f'Error closing browser SDK session: {e}')
            finally:
                self._session = None
                self._flare = None

    async def _disconnect_session(self) -> None:
        """Drop the local Playwright session without closing Chromium."""
        if self._session is not None:
            try:
                disconnect = getattr(self._session, 'disconnect', None)
                if callable(disconnect):
                    await disconnect()
                else:
                    await self._session.close()
            except Exception as e:
                logger.warning(f'Error disconnecting browser SDK session: {e}')
            finally:
                self._session = None
                self._flare = None

    @trace_api('browser_sdk')
    async def restart(
        self,
        mode: str = 'hard',
        url_blocklist: list[str] | None = None,
        url_allowlist: list[str] | None = None,
        locale: str | None = None,
    ):
        """Restart browser session.

        Args:
            mode: 'soft' = reconnect Playwright session only.
                  'hard' = restart browser process via supervisorctl then reconnect.
            url_blocklist: Update URL blocklist policy (hard mode only).
            url_allowlist: Update URL allowlist policy (hard mode only).
            locale: Override navigator.languages locale (e.g. 'zh-CN').
        """
        if locale is not None:
            self._locale = locale
        if mode == 'soft':
            await self._disconnect_session()
        else:
            await self.close()

        if mode == 'hard':
            agent_browser_cmd = _resolve_agent_browser_cmd()

            # Write URL policy if provided
            if url_blocklist is not None or url_allowlist is not None:
                policy: dict[str, Any] = {}
                if url_blocklist is not None:
                    policy['URLBlocklist'] = url_blocklist
                if url_allowlist is not None:
                    policy['URLAllowlist'] = url_allowlist
                policy_path = '/etc/browser/policies/managed/url_filter.json'
                os.makedirs(os.path.dirname(policy_path), exist_ok=True)
                with open(policy_path, 'w') as f:
                    json.dump(policy, f)
                logger.info(f'Wrote URL policy to {policy_path}')

            # Restart browser via supervisorctl
            proc = await asyncio.create_subprocess_exec(
                'supervisorctl', 'restart', 'browser',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f'supervisorctl restart browser failed: {stderr.decode().strip()}'
                )
            logger.info(f'Browser process restarted: {stdout.decode().strip()}')

            # Kill the agent-browser native daemon so it reconnects to the new Chrome.
            # The daemon auto-starts on next CLI command.
            ab_proc = await asyncio.create_subprocess_exec(
                agent_browser_cmd, 'close',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await ab_proc.communicate()
            logger.info('agent-browser daemon stopped for reconnection')

            # Wait for CDP port to become available
            import socket

            for _ in range(30):
                try:
                    sock = socket.create_connection(('127.0.0.1', 9222), timeout=1)
                    sock.close()
                    break
                except (ConnectionRefusedError, socket.timeout, OSError):
                    await asyncio.sleep(1)
            else:
                raise RuntimeError('Browser did not restart within 30 seconds')

        await self._ensure_session()
