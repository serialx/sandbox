import logging
import platform
import subprocess
from importlib import resources
from typing import TYPE_CHECKING, Annotated
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx
import pyperclip
from fastapi import Request
from pydantic import Field

from app.core.env import get_env_int
from app.logging.decorators import trace_api
from app.logging.sanitizer import sanitize_for_logging, summarize_headers_for_logging
from app.models.browser import (
    ActionResponse,
    AnyAction,
    BrowserInfoResult,
    BrowserScreenshotResult,
    BrowserViewport,
    ClickAction,
    DoubleClickAction,
    DragRelAction,
    DragToAction,
    HotkeyAction,
    KeyDownAction,
    KeyUpAction,
    MouseDownAction,
    MouseUpAction,
    MoveRelAction,
    MoveToAction,
    PressAction,
    RightClickAction,
    ScrollAction,
    TypingAction,
    WaitAction,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

    # Move pyautogui to TYPE_CHECKING to avoid slow import


class XrandrError(Exception):
    """Custom exception for xrandr related errors."""

    pass


class BrowserService:
    """Browser Operation Service"""

    def __init__(self):
        # Use CDP port directly since gem server was removed after gembrowser merge
        self.cdp_port = get_env_int(
            'BROWSER_REMOTE_DEBUGGING_PORT',
            9222,
            min_value=1,
            max_value=65535,
        )

    def _rewrite_websocket_urls(
        self, url: str, proxy_host: str, ws_protocol: str, path_prefix: str = ''
    ) -> str:
        """
        Rewrite WebSocket URLs in CDP JSON response.

        Args:
            url: Original WebSocket URL
            proxy_host: Proxy host (e.g., 'example.com:8080')
            ws_protocol: WebSocket protocol ('ws' or 'wss')
            path_prefix: Path prefix from X-Forwarded-Prefix header (e.g., '/api/v1')

        Returns:
            Rewritten WebSocket URL with path prefix if provided

        Notes:
            - If path_part already contains the path_prefix, we don't add it again
            - This handles cases where upstream proxy has already rewritten paths
        """

        path_part = urlparse(url).path

        # Check if path_part already starts with path_prefix to avoid duplication
        # This happens when upstream proxy has already rewritten the path
        if path_prefix and path_part.startswith(path_prefix):
            # Path already contains prefix, just add /cdp after the prefix
            # Extract the part after prefix
            remaining_path = path_part[len(path_prefix) :]
            new_url = f'{ws_protocol}://{proxy_host}{path_prefix}/cdp{remaining_path}'
        else:
            # Normal case: add prefix and /cdp
            new_url = f'{ws_protocol}://{proxy_host}{path_prefix}/cdp{path_part}'

        logging.info(f'Rewriting WebSocket URL: {url} -> {new_url}')

        return new_url

    def build_vnc_url(
        self, http_scheme: str, proxy_host: str, path_prefix: str, query_string: str
    ) -> str:
        """
        Build VNC URL with websockify path encoded in query parameter.

        Args:
            http_scheme: HTTP scheme ('http' or 'https')
            proxy_host: Proxy host (e.g., 'example.com:8080')
            path_prefix: Path prefix from X-Forwarded-Prefix header (e.g., '/api/v1')
            query_string: Original query string including '?' (e.g., '?token=abc')

        Returns:
            VNC URL with path parameter pointing to websockify endpoint

        Example:
            Input: prefix='/api/v1', query='?jwt_token=xxx'
            Output: https://host/api/v1/vnc/index.html?jwt_token=xxx&path=api%2Fv1%2Fwebsockify%3Fjwt_token%3Dxxx

        Notes:
            noVNC uses the 'path' query parameter to determine the WebSocket endpoint.
            - Original query_string is preserved in the vnc URL
            - path parameter is appended with the websockify endpoint
            - path value has no leading '/' and query part is URL-encoded
        """
        # Build websockify path for noVNC (no leading '/')
        prefix_for_path = path_prefix.lstrip('/')
        websockify_path = (
            f'{prefix_for_path}/websockify{query_string}'
            if prefix_for_path
            else f'websockify{query_string}'
        )

        # Parse original query params and append 'path' parameter
        query_params = parse_qsl(query_string.lstrip('?')) if query_string else []
        query_params.append(('path', websockify_path))

        return f'{http_scheme}://{proxy_host}{path_prefix}/vnc/index.html?{urlencode(query_params)}'

    @trace_api('browser')
    async def get_browser_info(self, request: Request):
        from app.utils import normalize_path_prefix

        async with httpx.AsyncClient(timeout=15) as client:
            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in ('host', 'accept-encoding', 'content-length')
            }
            headers['Host'] = f'127.0.0.1:{self.cdp_port}'

            logger.info('Headers: %s', summarize_headers_for_logging(headers))

            response = await client.get(
                f'http://127.0.0.1:{self.cdp_port}/json/version', headers=headers
            )
            res = response.json()

            # Priority: X-Forwarded-Host > Host header > request.url.netloc
            # X-Forwarded-Host preserves the original host when behind reverse proxies
            proxy_host = (
                request.headers.get('x-forwarded-host')
                or request.headers.get('host')
                or request.url.netloc
            )
            logger.info('proxy_host: %s', proxy_host)

            # Check X-Forwarded-Proto first, fallback to request.url.scheme
            # In reverse proxy scenarios, the internal request may be http
            # but the external client connection is https
            forwarded_proto = request.headers.get('x-forwarded-proto', '').lower()
            if forwarded_proto in ('https', 'http'):
                is_https = forwarded_proto == 'https'
            else:
                is_https = request.url.scheme == 'https'

            http_scheme = 'https' if is_https else 'http'
            ws_protocol = 'wss' if is_https else 'ws'

            logger.info(
                'Protocol detection - X-Forwarded-Proto: %s, request.url.scheme: %s, final: %s',
                forwarded_proto or 'none',
                request.url.scheme,
                http_scheme,
            )

            # Get and normalize path prefix from X-Forwarded-Prefix header
            path_prefix = normalize_path_prefix(
                request.headers.get('x-forwarded-prefix')
            )

            # Preserve query parameters from original request
            query_string = f'?{request.url.query}' if request.url.query else ''

            cdp_url = self._rewrite_websocket_urls(
                res.get('webSocketDebuggerUrl'), proxy_host, ws_protocol, path_prefix
            )
            # Append query parameters to cdp_url
            cdp_url = f'{cdp_url}{query_string}'

            # Build vnc_url with websockify path encoded in query parameter
            vnc_url = self.build_vnc_url(
                http_scheme, proxy_host, path_prefix, query_string
            )

            # Build cdp_ui_url for browser-ui endpoint
            cdp_ui_url = (
                f'{http_scheme}://{proxy_host}{path_prefix}/browser-ui{query_string}'
            )

            viewport = self.get_resolution()
            logger.info('viewport: %s', viewport)

            width = viewport.width if viewport else get_env_int('DISPLAY_WIDTH', 0)
            height = viewport.height if viewport else get_env_int('DISPLAY_HEIGHT', 0)

            page_viewport = await self.get_page_viewport()
            logger.info('page_viewport: %s', page_viewport)

            return BrowserInfoResult(
                user_agent=res.get('User-Agent', ''),
                cdp_url=cdp_url,
                vnc_url=vnc_url,
                cdp_ui_url=cdp_ui_url,
                viewport=BrowserViewport(
                    width=width,
                    height=height,
                ),
                page_viewport=page_viewport,
            )

    # Chrome UI chrome (tab bar ~35px + address bar ~35px + window decoration ~10px)
    CHROME_UI_HEIGHT = 80

    async def get_page_viewport(self) -> BrowserViewport | None:
        """Get Chrome's real page viewport size.

        Tries CDP Runtime.evaluate on every page-type target, picks the
        largest valid result.  Falls back to display_size - CHROME_UI_HEIGHT
        if CDP fails or returns garbage.
        """
        display = self.get_resolution()
        display_w = (
            display.width if display else get_env_int('DISPLAY_WIDTH', 0)
        )
        display_h = (
            display.height if display else get_env_int('DISPLAY_HEIGHT', 0)
        )

        cdp_viewport = await self._query_page_viewport_via_cdp()

        if cdp_viewport and cdp_viewport.height > 200:
            return cdp_viewport

        # Fallback: derive from display dimensions
        if display_w and display_h:
            return BrowserViewport(
                width=display_w,
                height=max(display_h - self.CHROME_UI_HEIGHT, 600),
            )

        return None

    async def _query_page_viewport_via_cdp(self) -> BrowserViewport | None:
        """Query window.innerWidth/Height via CDP on every page target.

        Returns the largest viewport found (real tabs have large viewports,
        internal pages like chrome://newtab may return tiny values).
        """
        import asyncio
        import json

        import websockets

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f'http://127.0.0.1:{self.cdp_port}/json')
                pages = [p for p in resp.json() if p.get('type') == 'page']

            if not pages:
                return None

            best: BrowserViewport | None = None

            for page in pages:
                page_ws = page.get('webSocketDebuggerUrl')
                if not page_ws:
                    continue
                try:
                    async with websockets.connect(page_ws, close_timeout=3) as ws:
                        await ws.send(
                            json.dumps(
                                {
                                    'id': 1,
                                    'method': 'Runtime.evaluate',
                                    'params': {
                                        'expression': 'JSON.stringify({w:window.innerWidth,h:window.innerHeight})',
                                    },
                                }
                            )
                        )
                        raw = await asyncio.wait_for(ws.recv(), timeout=3)
                        data = json.loads(raw)
                        val = data.get('result', {}).get('result', {}).get('value')
                        if val:
                            dims = json.loads(val)
                            vp = BrowserViewport(width=dims['w'], height=dims['h'])
                            if best is None or vp.height > best.height:
                                best = vp
                except Exception:
                    continue

            return best
        except Exception as e:
            logger.warning('Failed to query page viewport via CDP: %s', e)
            return None

    def _resolve_display_dimensions(
        self, screenshot_width: int, screenshot_height: int
    ) -> BrowserViewport:
        """Pick the most reliable display dimensions for screenshot headers.

        `pyautogui.size()` can disagree with the actual screenshot image under Xvfb,
        so prefer xrandr/env values and fall back to the captured image when the
        aspect ratio is clearly inconsistent.
        """
        display = self.get_resolution()
        width = display.width if display else get_env_int('DISPLAY_WIDTH', 0)
        height = display.height if display else get_env_int('DISPLAY_HEIGHT', 0)

        if width <= 0 or height <= 0:
            return BrowserViewport(width=screenshot_width, height=screenshot_height)

        display_ratio = width / height
        screenshot_ratio = screenshot_width / screenshot_height
        if abs(display_ratio - screenshot_ratio) > 0.01:
            logger.warning(
                'Display dimensions %sx%s disagree with screenshot %sx%s; '
                'using screenshot dimensions for headers',
                width,
                height,
                screenshot_width,
                screenshot_height,
            )
            return BrowserViewport(width=screenshot_width, height=screenshot_height)

        return BrowserViewport(width=width, height=height)

    @trace_api('browser')
    async def task_screenshot(self) -> tuple['PILImage', BrowserScreenshotResult]:
        import pyautogui  # Lazy import to avoid startup delay

        # Disable fail-safe in sandbox environment to allow clicking screen corners
        pyautogui.FAILSAFE = False

        image = pyautogui.screenshot()
        screenshot_width, screenshot_height = image.size
        display = self._resolve_display_dimensions(
            screenshot_width=screenshot_width,
            screenshot_height=screenshot_height,
        )

        return image, BrowserScreenshotResult(
            display_width=display.width,
            display_height=display.height,
            screenshot_width=screenshot_width,
            screenshot_height=screenshot_height,
        )

    @trace_api('browser')
    async def execute_action(
        self, action: Annotated[AnyAction, Field(discriminator='action_type')]
    ) -> ActionResponse:
        import pyautogui  # Lazy import to avoid startup delay

        # Disable fail-safe in sandbox environment to allow clicking screen corners
        pyautogui.FAILSAFE = False

        if isinstance(action, MoveToAction):
            pyautogui.moveTo(action.x, action.y)
        elif isinstance(action, MoveRelAction):
            pyautogui.moveRel(action.x_offset, action.y_offset)
        elif isinstance(action, ClickAction):
            pyautogui.click(
                x=action.x, y=action.y, button=action.button, clicks=action.num_clicks
            )
        elif isinstance(action, MouseDownAction):
            pyautogui.mouseDown(button=action.button)
        elif isinstance(action, MouseUpAction):
            pyautogui.mouseUp(button=action.button)
        elif isinstance(action, RightClickAction):
            pyautogui.rightClick(x=action.x, y=action.y)
        elif isinstance(action, DoubleClickAction):
            pyautogui.doubleClick(x=action.x, y=action.y)
        elif isinstance(action, DragToAction):
            pyautogui.dragTo(action.x, action.y)
        elif isinstance(action, DragRelAction):
            pyautogui.dragRel(action.x_offset, action.y_offset)
        elif isinstance(action, ScrollAction):
            pyautogui.scroll(action.dy)
            pyautogui.hscroll(action.dx)
        elif isinstance(action, TypingAction):
            if action.use_clipboard:
                try:
                    original_clipboard = pyperclip.paste()
                except Exception:
                    original_clipboard = None
                pyperclip.copy(action.text)
                is_macos = platform.system() == 'Darwin'
                if is_macos:
                    # https://github.com/asweigart/pyautogui/issues/796#issuecomment-2052361304
                    pyautogui.keyUp('fn')
                    pyautogui.sleep(0.1)

                pyautogui.hotkey(
                    'command' if platform.system() == 'Darwin' else 'ctrl',
                    'v',
                )
                pyautogui.sleep(0.1)
                if original_clipboard is not None:
                    pyperclip.copy(original_clipboard)
            else:
                pyautogui.typewrite(action.text)
        elif isinstance(action, PressAction):
            pyautogui.press(action.key)
        elif isinstance(action, KeyDownAction):
            pyautogui.keyDown(action.key)
        elif isinstance(action, KeyUpAction):
            pyautogui.keyUp(action.key)
        elif isinstance(action, HotkeyAction):
            pyautogui.hotkey(*action.keys)
        elif isinstance(action, WaitAction):
            pyautogui.sleep(action.duration)

        return ActionResponse(status='success', action_performed=action.action_type)

    @staticmethod
    def _generate_exact_modeline(
        width: int, height: int, refresh_rate: float = 60.0
    ) -> tuple[str, str]:
        """
        Generates a precise xrandr modeline for a given resolution and refresh rate.

        Returns:
            A tuple containing (mode_name, modeline_params_string).
            The mode_name is in the standard "WIDTHxHEIGHT" format.
        """
        # The mode name should be the standard format to match system-defined modes.
        mode_name = f'{width}x{height}'

        # 1. Define simplified timing parameters
        h_sync_start = width + 16
        h_sync_end = h_sync_start + 96
        h_total = h_sync_end + 48

        v_sync_start = height + 3
        v_sync_end = v_sync_start + 5
        v_total = v_sync_end + 20

        # 2. Calculate the required pixel clock in MHz
        pclk_hz = h_total * v_total * refresh_rate
        pclk_mhz = pclk_hz / 1_000_000

        # 3. Format the modeline parameters into a single string
        modeline_params = (
            f'{pclk_mhz:.2f} '
            f'{width} {h_sync_start} {h_sync_end} {h_total} '
            f'{height} {v_sync_start} {v_sync_end} {v_total} '
            '+hsync +vsync'
        )

        return mode_name, modeline_params

    def get_resolution(self) -> BrowserViewport | None:
        command_pipeline = r'xrandr --verbose | grep -oP "(?<=current )\d+ x \d+"'

        try:
            # Step 1: Execute the entire pipeline using shell=True
            # shell=True tells Python to pass the command to the system's shell (/bin/sh or /bin/bash)
            # The shell understands the pipe '|' symbol and handles the connection.
            result = subprocess.run(
                command_pipeline, shell=True, capture_output=True, text=True, check=True
            )

            # The output of the whole pipeline is in result.stdout
            resolution_str = result.stdout.strip()

            if not resolution_str:
                print('Error: The command pipeline produced no output.')
                return None

            # Step 2: Parse the final output string
            parts = resolution_str.split(' x ')
            width = int(parts[0])
            height = int(parts[1])

            return BrowserViewport(width=width, height=height)

        except FileNotFoundError:
            print(
                'Error: A command in the pipeline was not found (e.g., xrandr or grep).'
            )
            return None
        except subprocess.CalledProcessError as e:
            print(f'Error during command execution: {e}')
            print(f'Stderr: {e.stderr}')
            return None

    def set_resolution(self, width: int, height: int) -> str:
        """
        Calculates a precise 60Hz modeline and calls the set-resolution.sh script
        to apply it. It uses a standard "WIDTHxHEIGHT" mode name for compatibility.

        Args:
            width: The target width.
            height: The target height.

        Returns:
            A success message extracted from the script's output.

        Raises:
            XrandrError: If the script fails or is not found.
        """
        try:
            mode_name, modeline_params = BrowserService._generate_exact_modeline(
                width, height
            )
            logger.info(
                f"Using standard mode name '{mode_name}' with "
                f'calculated modeline: {modeline_params}'
            )

            with resources.path('app.scripts', 'set-resolution.sh') as script_path:
                command = [str(script_path), mode_name, modeline_params]
                logger.info(f'Executing command: {" ".join(command)}')

                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=True,
                    encoding='utf-8',
                )

            success_message = ''
            for line in result.stdout.strip().split('\n'):
                if line.startswith('Success:'):
                    success_message = line.replace('Success: ', '')

            if not success_message:
                success_message = 'Resolution script executed successfully, but no success message found.'

            logger.info(
                'Script output: %s',
                sanitize_for_logging(result.stdout.strip(), field_name='stdout'),
            )
            return success_message

        except FileNotFoundError as e:
            raise XrandrError(
                f"Script 'set-resolution.sh' not found. Original error: {e}"
            )
        except subprocess.CalledProcessError as e:
            error_output = e.stderr.strip()
            logging.error(
                f'Script failed with exit code {e.returncode}. Stderr: {error_output}'
            )
            raise XrandrError(f'Failed to set resolution. Reason: {error_output}')
