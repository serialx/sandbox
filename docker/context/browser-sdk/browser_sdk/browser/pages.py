"""LocalPages and LocalPage — Playwright-backed implementation."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import shutil
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page as PWPage
from playwright.async_api import BrowserContext as PWBrowserContext
from playwright.async_api import Error as PWError

from browser_sdk.browser.base import Page, Pages
from browser_sdk.errors import (
    E_DOWNLOAD_FAILED,
    E_DOWNLOAD_TIMEOUT,
    E_EVAL_SCRIPT_ERROR,
    E_PAGE_ACTION_FAILED,
    E_PAGE_ACTION_TIMEOUT,
    E_PAGE_CLOSED,
    E_PAGE_CURRENT_NOT_FOUND,
    E_PAGE_ELEMENT_NOT_FOUND,
    E_PAGE_NAVIGATE_FAILED,
    E_PAGE_NAVIGATE_TIMEOUT,
    E_PAGE_NOT_FOUND,
)
from browser_sdk.helpers.js_scripts import js_loader
from browser_sdk.models import (
    BoundingBox,
    ConsoleMessage,
    DownloadResult,
    FormFillItem,
    InteractiveElement,
    NavigateResult,
    OpenOptions,
    OperationContext,
    PageRecordResult,
    PageInfo,
    TextMatch,
)
from browser_sdk.result import Result

logger = logging.getLogger(__name__)


@dataclass
class _ActiveRecorder:
    save_path: str
    fps: int
    quality: int
    started_at: float
    frame_count: int
    paused: bool
    frame_queue: asyncio.Queue[bytes | None]
    writer_task: asyncio.Task[None]
    ffmpeg: asyncio.subprocess.Process
    cdp: Any
    on_frame: Any


class LocalPage(Page):
    """Page implementation backed by a Playwright Page."""

    _recorders: dict[int, _ActiveRecorder] = {}

    def __init__(
        self,
        pw_page: PWPage,
        console_buffer: deque[ConsoleMessage],
        max_console: int = 1000,
        captcha_auto_detect: bool = True,
        captcha_detector: Any = None,
        download_dir: str | None = None,
        _listeners_attached: set | None = None,
    ) -> None:
        self._page = pw_page
        self._console = console_buffer
        self._max_console = max_console
        self._captcha_auto_detect = captcha_auto_detect
        self._captcha_detector = captcha_detector
        self._download_dir = download_dir
        # Track which PWPages already have listeners to avoid duplicates
        if _listeners_attached is not None and pw_page not in _listeners_attached:
            self._setup_console_listener()
            _listeners_attached.add(pw_page)
        elif _listeners_attached is None:
            self._setup_console_listener()

    def _record_key(self) -> int:
        return id(self._page)

    def _setup_console_listener(self) -> None:
        page = self._page

        def _on_console(msg):
            self._console.append(
                ConsoleMessage(
                    type=msg.type,
                    text=msg.text,
                    timestamp=time.time(),
                    url=msg.location.get("url") if msg.location else None,
                    line=msg.location.get("lineNumber") if msg.location else None,
                    page_url=page.url,
                )
            )
            while len(self._console) > self._max_console:
                self._console.popleft()

        def _on_pageerror(error):
            self._console.append(
                ConsoleMessage(
                    type="pageerror",
                    text=str(error),
                    timestamp=time.time(),
                    page_url=page.url,
                )
            )
            while len(self._console) > self._max_console:
                self._console.popleft()

        def _on_crash():
            self._console.append(
                ConsoleMessage(
                    type="crash",
                    text="Page crashed",
                    timestamp=time.time(),
                    page_url=page.url,
                )
            )

        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)
        page.on("crash", _on_crash)

    @staticmethod
    def _download_name_matches(candidate: str, suggested: str) -> bool:
        if candidate == suggested:
            return True

        stem, ext = os.path.splitext(suggested)
        duplicate_pattern = rf"^{re.escape(stem)} \(\d+\){re.escape(ext)}$"
        return re.match(duplicate_pattern, candidate) is not None

    def _find_downloaded_file(
        self, suggested_filename: str, started_at: float
    ) -> str | None:
        if not self._download_dir or not os.path.isdir(self._download_dir):
            return None

        candidates: list[tuple[float, int, str]] = []
        for entry in os.scandir(self._download_dir):
            if not entry.is_file():
                continue
            if not self._download_name_matches(entry.name, suggested_filename):
                continue
            stat = entry.stat()
            candidates.append((stat.st_mtime, stat.st_size, entry.path))

        if not candidates:
            return None

        fresh = [item for item in candidates if item[0] >= started_at - 1]
        pool = fresh or candidates
        return max(pool, key=lambda item: (item[0], item[1]))[2]

    @staticmethod
    def _is_navigation_context_error(error: PWError) -> bool:
        message = str(error).lower()
        return (
            "execution context was destroyed" in message
            or "cannot find context with specified id" in message
        )

    async def _evaluate_with_retry(self, expression: str) -> Any:
        """Retry once when navigation replaces the JS execution context."""
        for attempt in range(2):
            try:
                return await self._page.evaluate(expression)
            except PWError as error:
                if attempt == 0 and self._is_navigation_context_error(error):
                    try:
                        await self._page.wait_for_load_state("load", timeout=5000)
                    except PWError:
                        pass
                    await asyncio.sleep(0.1)
                    continue
                raise

    def _resolve_locator(
        self,
        selector: str | None,
        index: int | None,
        x: float | None,
        y: float | None,
    ):
        """Resolve targeting: selector > index > coordinates > None."""
        if selector:
            return self._page.locator(selector).first
        if index is not None:
            # index-based requires get_interactive_elements mapping
            # We use a data attribute approach - elements are indexed via JS
            return None  # handled specially in click/fill
        if x is not None and y is not None:
            return None  # coordinate click
        return None

    # --- Navigation ---

    async def open(
        self, url: str,
        options: OpenOptions | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[NavigateResult]:
        opts = options or OpenOptions()
        t0 = time.monotonic()
        try:
            response = await self._page.goto(
                url,
                wait_until=opts.wait_until,  # type: ignore[arg-type]
                timeout=opts.timeout * 1000,
            )
            elapsed = (time.monotonic() - t0) * 1000
            result = NavigateResult(
                url=self._page.url,
                title=await self._page.title(),
                status=response.status if response else None,
                load_time_ms=elapsed,
            )
            return Result.ok(result)
        except PWError as e:
            if "timeout" in str(e).lower():
                return Result.fail(E_PAGE_NAVIGATE_TIMEOUT, str(e))
            return Result.fail(E_PAGE_NAVIGATE_FAILED, str(e))

    async def back(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[NavigateResult | None]:
        try:
            t0 = time.monotonic()
            await self._page.go_back(wait_until="commit")
            elapsed = (time.monotonic() - t0) * 1000
            return Result.ok(NavigateResult(
                url=self._page.url,
                title=await self._page.title(),
                load_time_ms=elapsed,
            ))
        except PWError as e:
            return Result.fail(E_PAGE_NAVIGATE_FAILED, str(e))

    async def forward(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[NavigateResult | None]:
        try:
            t0 = time.monotonic()
            await self._page.go_forward(wait_until="commit")
            elapsed = (time.monotonic() - t0) * 1000
            return Result.ok(NavigateResult(
                url=self._page.url,
                title=await self._page.title(),
                load_time_ms=elapsed,
            ))
        except PWError as e:
            return Result.fail(E_PAGE_NAVIGATE_FAILED, str(e))

    async def reload(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.reload()
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_NAVIGATE_FAILED, str(e))

    async def is_current(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[bool]:
        # In Playwright, check if this page is the last focused
        try:
            context = self._page.context
            pages = context.pages
            # The "current" page is typically the last one that received focus
            return Result.ok(len(pages) > 0 and pages[-1] == self._page)
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def switch_to_current(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.bring_to_front()
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    # --- Element Interaction ---

    async def click(
        self,
        selector: str | None = None,
        index: int | None = None,
        x: float | None = None,
        y: float | None = None,
        button: str = "left",
        click_count: int = 1,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            if selector:
                await self._page.locator(selector).first.click(
                    button=button, click_count=click_count,  # type: ignore[arg-type]
                )
            elif index is not None:
                elements = await self._page.evaluate(
                    js_loader.get_interactive_elements_script()
                )
                if index >= len(elements):
                    return Result.fail(
                        E_PAGE_ELEMENT_NOT_FOUND,
                        f"Element index {index} out of range (total: {len(elements)})",
                    )
                el = elements[index]
                bb = el["bounding_box"]
                cx = bb["x"] + bb["width"] / 2
                cy = bb["y"] + bb["height"] / 2
                await self._page.mouse.click(
                    cx, cy, button=button, click_count=click_count,  # type: ignore[arg-type]
                )
            elif x is not None and y is not None:
                await self._page.mouse.click(
                    x, y, button=button, click_count=click_count,  # type: ignore[arg-type]
                )
            else:
                return Result.fail(
                    E_PAGE_ELEMENT_NOT_FOUND,
                    "Must provide selector, index, or (x, y) coordinates",
                )
            return Result.ok()
        except PWError as e:
            if "timeout" in str(e).lower():
                return Result.fail(E_PAGE_ACTION_TIMEOUT, str(e))
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def fill(
        self, text: str,
        selector: str | None = None,
        index: int | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            if selector:
                await self._page.locator(selector).first.fill(text)
            elif index is not None:
                elements = await self._page.evaluate(
                    js_loader.get_interactive_elements_script()
                )
                if index >= len(elements):
                    return Result.fail(
                        E_PAGE_ELEMENT_NOT_FOUND,
                        f"Element index {index} out of range (total: {len(elements)})",
                    )
                el = elements[index]
                bb = el["bounding_box"]
                cx = bb["x"] + bb["width"] / 2
                cy = bb["y"] + bb["height"] / 2
                await self._page.mouse.click(cx, cy)
                await self._page.keyboard.press("Control+a")
                await self._page.keyboard.type(text)
            else:
                return Result.fail(
                    E_PAGE_ELEMENT_NOT_FOUND,
                    "Must provide selector or index",
                )
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def type_text(
        self, text: str, delay: float = 0,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.keyboard.type(text, delay=delay)
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def press_key(
        self, key: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.keyboard.press(key)
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def hot_key(
        self, keys: list[str],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            combo = "+".join(keys)
            await self._page.keyboard.press(combo)
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def hover(
        self,
        selector: str | None = None,
        x: float | None = None, y: float | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            if selector:
                await self._page.locator(selector).first.hover()
            elif x is not None and y is not None:
                await self._page.mouse.move(x, y)
            else:
                return Result.fail(E_PAGE_ELEMENT_NOT_FOUND, "Must provide selector or (x, y)")
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def select_option(
        self, selector: str,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            kwargs: dict[str, Any] = {}
            if value is not None:
                kwargs["value"] = value
            if label is not None:
                kwargs["label"] = label
            if index is not None:
                kwargs["index"] = index
            await self._page.locator(selector).first.select_option(**kwargs)
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def check(
        self, selector: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.locator(selector).first.check()
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def uncheck(
        self, selector: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.locator(selector).first.uncheck()
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def upload_file(
        self, selector: str, files: list[str],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.locator(selector).first.set_input_files(files)
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def fill_form(
        self, items: list[FormFillItem],
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        for item in items:
            result = await self.fill(
                text=item.value, selector=item.selector, index=item.index,
            )
            if not result.success:
                return result
        return Result.ok()

    # --- Scrolling ---

    async def scroll(
        self, direction: str = "down", amount: int = 300,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            dx, dy = 0, 0
            if direction == "down":
                dy = amount
            elif direction == "up":
                dy = -amount
            elif direction == "right":
                dx = amount
            elif direction == "left":
                dx = -amount
            await self._page.evaluate(f"window.scrollBy({dx}, {dy})")
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def scroll_to(
        self, x: int = 0, y: int = 0,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.evaluate(f"window.scrollTo({x}, {y})")
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def scroll_to_element(
        self, selector: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.locator(selector).first.scroll_into_view_if_needed()
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    # --- Content Retrieval ---

    async def screenshot(
        self, full_page: bool = False, format: str = "png",
        quality: int | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[bytes]:
        try:
            kwargs: dict[str, Any] = {"full_page": full_page, "type": format}
            if quality is not None and format == "jpeg":
                kwargs["quality"] = quality
            data = await self._page.screenshot(**kwargs)
            return Result.ok(data)
        except PWError as e:
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
        action = action.lower()
        valid_actions = {"once", "start", "pause", "resume", "stop", "status"}
        if action not in valid_actions:
            return Result.fail(
                E_PAGE_ACTION_FAILED,
                f"invalid action '{action}', expected one of {sorted(valid_actions)}",
            )

        key = self._record_key()
        recorder = self._recorders.get(key)

        if action == "status":
            return Result.ok(
                self._build_record_result(action=action, recorder=recorder, status_override="idle")
            )

        if action == "once":
            if duration <= 0:
                return Result.fail(E_PAGE_ACTION_FAILED, "duration must be > 0")
            start_result = await self.record(
                action="start",
                save_path=save_path,
                fps=fps,
                quality=quality,
            )
            if not start_result.success:
                return start_result
            try:
                await asyncio.sleep(duration)
            finally:
                stop_result = await self.record(action="stop")
            if not stop_result.success:
                return stop_result
            return Result.ok(
                self._build_record_result(
                    action="once",
                    recorder=None,
                    stopped_result=stop_result.data,
                )
            )

        if action == "start":
            if recorder is not None:
                return Result.fail(E_PAGE_ACTION_FAILED, "recording is already active")
            if fps <= 0:
                return Result.fail(E_PAGE_ACTION_FAILED, "fps must be > 0")
            if quality < 0 or quality > 100:
                return Result.fail(E_PAGE_ACTION_FAILED, "quality must be in [0, 100]")

            target_path = save_path or f"/tmp/{uuid.uuid4().hex}.webm"
            if not target_path.endswith(".webm"):
                target_path = f"{target_path}.webm"
            os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)

            frame_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max(fps * 2, 32))
            try:
                ffmpeg = await asyncio.create_subprocess_exec(
                    "ffmpeg",
                    "-y",
                    "-f",
                    "image2pipe",
                    "-framerate",
                    str(fps),
                    "-vcodec",
                    "mjpeg",
                    "-i",
                    "-",
                    "-an",
                    "-c:v",
                    "libvpx-vp9",
                    "-pix_fmt",
                    "yuv420p",
                    target_path,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                return Result.fail(
                    E_PAGE_ACTION_FAILED,
                    "ffmpeg is not available; unable to encode screencast",
                )
            except Exception as e:
                return Result.fail(E_PAGE_ACTION_FAILED, str(e))

            stdin = ffmpeg.stdin
            if stdin is None:
                if ffmpeg.returncode is None:
                    ffmpeg.kill()
                    await ffmpeg.wait()
                return Result.fail(E_PAGE_ACTION_FAILED, "ffmpeg stdin is unavailable")

            async def _writer():
                try:
                    while True:
                        frame = await frame_queue.get()
                        if frame is None:
                            break
                        stdin.write(frame)
                        await stdin.drain()
                except Exception:
                    pass
                finally:
                    try:
                        stdin.close()
                        await stdin.wait_closed()
                    except Exception:
                        pass

            writer_task = asyncio.create_task(_writer())

            try:
                cdp = await self._page.context.new_cdp_session(self._page)
            except Exception as e:
                writer_task.cancel()
                try:
                    await writer_task
                except Exception:
                    pass
                if ffmpeg.returncode is None:
                    ffmpeg.kill()
                    await ffmpeg.wait()
                return Result.fail(
                    E_PAGE_ACTION_FAILED, f"failed to create CDP session: {e}"
                )

            recorder_ref: dict[str, _ActiveRecorder] = {}

            async def _ack(session_id: int):
                try:
                    await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
                except Exception:
                    return

            def _on_frame(params: dict[str, Any]) -> None:
                session_id = params.get("sessionId")
                if isinstance(session_id, int):
                    asyncio.create_task(_ack(session_id))
                data = params.get("data")
                recorder_state = recorder_ref.get("state")
                if not isinstance(data, str) or recorder_state is None:
                    return
                try:
                    recorder_state.frame_queue.put_nowait(base64.b64decode(data))
                    recorder_state.frame_count += 1
                except asyncio.QueueFull:
                    pass
                except Exception:
                    pass

            cdp.on("Page.screencastFrame", _on_frame)
            try:
                await cdp.send(
                    "Page.startScreencast",
                    {"format": "jpeg", "quality": quality, "everyNthFrame": 1},
                )
            except Exception as e:
                try:
                    await cdp.detach()
                except Exception:
                    pass
                await frame_queue.put(None)
                await writer_task
                if ffmpeg.returncode is None:
                    ffmpeg.kill()
                    await ffmpeg.wait()
                return Result.fail(E_PAGE_ACTION_FAILED, f"screencast failed: {e}")

            recorder = _ActiveRecorder(
                save_path=target_path,
                fps=fps,
                quality=quality,
                started_at=time.monotonic(),
                frame_count=0,
                paused=False,
                frame_queue=frame_queue,
                writer_task=writer_task,
                ffmpeg=ffmpeg,
                cdp=cdp,
                on_frame=_on_frame,
            )
            recorder_ref["state"] = recorder
            self._recorders[key] = recorder
            return Result.ok(self._build_record_result(action=action, recorder=recorder))

        if recorder is None:
            return Result.fail(E_PAGE_ACTION_FAILED, "no active recording")

        if action == "pause":
            if recorder.paused:
                return Result.ok(self._build_record_result(action=action, recorder=recorder))
            try:
                await recorder.cdp.send("Page.stopScreencast")
                recorder.paused = True
            except Exception as e:
                return Result.fail(E_PAGE_ACTION_FAILED, f"pause failed: {e}")
            return Result.ok(self._build_record_result(action=action, recorder=recorder))

        if action == "resume":
            if not recorder.paused:
                return Result.ok(self._build_record_result(action=action, recorder=recorder))
            try:
                await recorder.cdp.send(
                    "Page.startScreencast",
                    {"format": "jpeg", "quality": recorder.quality, "everyNthFrame": 1},
                )
                recorder.paused = False
            except Exception as e:
                return Result.fail(E_PAGE_ACTION_FAILED, f"resume failed: {e}")
            return Result.ok(self._build_record_result(action=action, recorder=recorder))

        if action == "stop":
            result = await self._stop_recording(recorder, action=action)
            self._recorders.pop(key, None)
            return result

        return Result.fail(E_PAGE_ACTION_FAILED, f"unsupported action '{action}'")

    def _build_record_result(
        self,
        action: str,
        recorder: _ActiveRecorder | None,
        status_override: str | None = None,
        stopped_result: PageRecordResult | None = None,
    ) -> PageRecordResult:
        if stopped_result is not None:
            return PageRecordResult(
                action=action,  # type: ignore[arg-type]
                status=stopped_result.status,
                save_path=stopped_result.save_path,
                duration=stopped_result.duration,
                fps=stopped_result.fps,
                quality=stopped_result.quality,
                frame_count=stopped_result.frame_count,
                is_recording=stopped_result.is_recording,
                is_paused=stopped_result.is_paused,
            )
        if recorder is None:
            return PageRecordResult(
                action=action,  # type: ignore[arg-type]
                status="idle" if status_override is None else status_override,  # type: ignore[arg-type]
                save_path=None,
                duration=0.0,
                fps=0,
                quality=0,
                frame_count=0,
                is_recording=False,
                is_paused=False,
            )

        status = "paused" if recorder.paused else "recording"
        return PageRecordResult(
            action=action,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            save_path=recorder.save_path,
            duration=max(time.monotonic() - recorder.started_at, 0.0),
            fps=recorder.fps,
            quality=recorder.quality,
            frame_count=recorder.frame_count,
            is_recording=True,
            is_paused=recorder.paused,
        )

    async def _stop_recording(
        self,
        recorder: _ActiveRecorder,
        action: str,
    ) -> Result[PageRecordResult]:
        try:
            if not recorder.paused:
                try:
                    await recorder.cdp.send("Page.stopScreencast")
                except Exception:
                    pass

            try:
                remove_listener = getattr(recorder.cdp, "remove_listener", None)
                if callable(remove_listener):
                    remove_listener("Page.screencastFrame", recorder.on_frame)
                else:
                    off = getattr(recorder.cdp, "off", None)
                    if callable(off):
                        off("Page.screencastFrame", recorder.on_frame)
            except Exception:
                pass

            try:
                await recorder.cdp.detach()
            except Exception:
                pass

            await recorder.frame_queue.put(None)
            await recorder.writer_task

            stderr_output = ""
            if recorder.ffmpeg.stderr is not None:
                try:
                    stderr_output = (
                        await recorder.ffmpeg.stderr.read()
                    ).decode("utf-8", errors="ignore")
                except Exception:
                    stderr_output = ""
            await recorder.ffmpeg.wait()

            if recorder.ffmpeg.returncode != 0:
                error_msg = (
                    stderr_output.strip()
                    or f"ffmpeg exited with code {recorder.ffmpeg.returncode}"
                )
                return Result.fail(
                    E_PAGE_ACTION_FAILED, f"ffmpeg encode failed: {error_msg}"
                )

            return Result.ok(
                PageRecordResult(
                    action=action,  # type: ignore[arg-type]
                    status="stopped",
                    save_path=recorder.save_path,
                    duration=max(time.monotonic() - recorder.started_at, 0.0),
                    fps=recorder.fps,
                    quality=recorder.quality,
                    frame_count=recorder.frame_count,
                    is_recording=False,
                    is_paused=False,
                )
            )
        except Exception as e:
            return Result.fail(E_PAGE_ACTION_FAILED, f"stop failed: {e}")

    async def get_html(
        self, outer: bool = False,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[str]:
        try:
            if outer:
                html = await self._evaluate_with_retry(
                    "document.documentElement.outerHTML"
                )
            else:
                html = await self._evaluate_with_retry("document.body.innerHTML")
            return Result.ok(html)
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def get_text(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[str]:
        try:
            text = await self._evaluate_with_retry(js_loader.get_page_text_script())
            return Result.ok(text)
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def get_markdown(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[dict]:
        try:
            result = await self._evaluate_with_retry(
                js_loader.get_page_markdown_script()
            )
            return Result.ok(result)
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def evaluate(
        self, expression: str = "",
        *, script: str = "",
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[Any]:
        expr = expression or script
        try:
            result = await self._evaluate_with_retry(expr)
            return Result.ok(result)
        except PWError as e:
            return Result.fail(E_EVAL_SCRIPT_ERROR, str(e))

    async def get_console_logs(
        self, clear: bool = False,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[ConsoleMessage]]:
        logs = list(self._console)
        if clear:
            self._console.clear()
        return Result.ok(logs)

    async def get_interactive_elements(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[InteractiveElement]]:
        try:
            raw = await self._evaluate_with_retry(
                js_loader.get_interactive_elements_script()
            )
            elements = []
            for item in raw:
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
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def find_text(
        self, keyword: str,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[TextMatch]]:
        try:
            locator = self._page.get_by_text(keyword)
            count = await locator.count()
            matches = []
            for i in range(min(count, 50)):
                el = locator.nth(i)
                bb_raw = await el.bounding_box()
                bb = BoundingBox(**bb_raw) if bb_raw else None
                text = (await el.text_content()) or keyword
                matches.append(TextMatch(text=text, index=i, bounding_box=bb))
            return Result.ok(matches)
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    # --- Wait ---

    async def wait_for_load(
        self, state: str = "load", timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.wait_for_load_state(
                state, timeout=timeout * 1000,  # type: ignore[arg-type]
            )
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_TIMEOUT, str(e))

    async def wait_for_selector(
        self, selector: str, state: str = "visible", timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.wait_for_selector(
                selector, state=state, timeout=timeout * 1000,  # type: ignore[arg-type]
            )
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_TIMEOUT, str(e))

    async def wait_for_url(
        self, url: str, timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.wait_for_url(url, timeout=timeout * 1000)
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_TIMEOUT, str(e))

    async def wait_for_network_idle(
        self, timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.wait_for_load_state(
                "networkidle", timeout=timeout * 1000,
            )
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_TIMEOUT, str(e))

    async def wait_for_function(
        self, expression: str, timeout: float = 30.0,
        polling: float | str = "raf",
        ctx: OperationContext | None = None,
    ) -> Result[Any]:
        try:
            handle = await self._page.wait_for_function(
                expression, timeout=timeout * 1000, polling=polling,
            )
            value = await handle.json_value()
            return Result.ok(value)
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_TIMEOUT, str(e))

    async def wait_for_response(
        self, url_pattern: str, timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[dict]:
        try:
            response = await self._page.wait_for_event(
                "response",
                predicate=lambda r: url_pattern in r.url,
                timeout=timeout * 1000,
            )
            return Result.ok({
                "url": response.url,
                "status": response.status,
                "headers": dict(response.headers),
            })
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_TIMEOUT, str(e))

    async def wait_for_request(
        self, url_pattern: str, timeout: float = 30.0,
        ctx: OperationContext | None = None,
    ) -> Result[dict]:
        try:
            request = await self._page.wait_for_event(
                "request",
                predicate=lambda r: url_pattern in r.url,
                timeout=timeout * 1000,
            )
            return Result.ok({
                "url": request.url,
                "method": request.method,
                "headers": dict(request.headers),
                "resource_type": request.resource_type,
            })
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_TIMEOUT, str(e))

    # --- Download ---

    async def wait_for_download(
        self, save_path: str | None = None, timeout: float = 30.0,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[DownloadResult]:
        try:
            wait_started_at = time.time()
            async with self._page.expect_download(timeout=timeout * 1000) as dl_info:
                pass  # caller should have already triggered the download action
            download = await dl_info.value
            if save_path:
                await download.save_as(save_path)
                final_path = save_path
            else:
                # Wait for the download to finish (writes to temp)
                path = await download.path()
                final_path = str(path) if path else ""

            import os
            size = os.path.getsize(final_path) if final_path and os.path.exists(final_path) else 0

            if size == 0:
                fallback_path = self._find_downloaded_file(
                    download.suggested_filename,
                    wait_started_at,
                )
                if fallback_path:
                    if save_path:
                        if os.path.abspath(fallback_path) != os.path.abspath(save_path):
                            shutil.copy2(fallback_path, save_path)
                        final_path = save_path
                    else:
                        final_path = fallback_path
                    size = os.path.getsize(final_path) if os.path.exists(final_path) else 0

            return Result.ok(DownloadResult(
                url=download.url,
                suggested_filename=download.suggested_filename,
                saved_path=final_path,
                size_bytes=size,
            ))
        except PWError as e:
            if "timeout" in str(e).lower():
                return Result.fail(E_DOWNLOAD_TIMEOUT, str(e))
            return Result.fail(E_DOWNLOAD_FAILED, str(e))
        except Exception as e:
            return Result.fail(E_DOWNLOAD_FAILED, str(e))

    # --- Lifecycle ---

    async def close(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        try:
            await self._page.close()
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_CLOSED, str(e))


# ============================================================
# LocalPages
# ============================================================


class LocalPages(Pages):
    """Pages implementation backed by a Playwright BrowserContext."""

    def __init__(
        self,
        pw_context: PWBrowserContext,
        max_console: int = 1000,
        captcha_auto_detect: bool = True,
        captcha_detector: Any = None,
        download_dir: str | None = None,
    ) -> None:
        self._context = pw_context
        self._max_console = max_console
        self._captcha_auto_detect = captcha_auto_detect
        self._captcha_detector = captcha_detector
        self._download_dir = download_dir
        self._console: deque[ConsoleMessage] = deque(maxlen=max_console)
        self._active: PWPage | None = None
        self._listeners_attached: set[PWPage] = set()
        self._page_events: list[dict] = []
        self._closing_via_api: set[PWPage] = set()
        # Auto-switch active page when any new page appears (popup, window.open, etc.)
        self._context.on("page", self._on_new_page)

    def _wrap_page(self, pw_page: PWPage) -> LocalPage:
        return LocalPage(
            pw_page,
            self._console,
            self._max_console,
            self._captcha_auto_detect,
            self._captcha_detector,
            self._download_dir,
            _listeners_attached=self._listeners_attached,
        )

    def _on_new_page(self, pw_page: PWPage) -> None:
        """Auto-switch active page and attach listeners when a new page appears."""
        self._active = pw_page
        self._wrap_page(pw_page)  # attach console/error listeners
        idx = next(
            (i for i, p in enumerate(self._context.pages) if p is pw_page), -1
        )
        self._page_events.append({
            "type": "new_tab",
            "url": pw_page.url,
            "index": idx,
        })
        # Listen for this page being closed (externally or via window.close())
        pw_page.on("close", lambda p=pw_page: self._on_page_close(p))

    def _on_page_close(self, pw_page: PWPage) -> None:
        """Handle page closed externally (VNC, window.close(), JS).

        Pages closed via the API close() method are marked in
        _closing_via_api and skipped here to avoid duplicate handling
        and false "externally closed" hints.
        """
        if pw_page in self._closing_via_api:
            self._closing_via_api.discard(pw_page)
            return
        # External close — update state and record event
        self._listeners_attached.discard(pw_page)
        if self._active is pw_page:
            self._active = None
        self._page_events.append({
            "type": "tab_closed",
            "url": pw_page.url,
        })

    def drain_page_events(self) -> list[dict]:
        """Return and clear accumulated page events (for hints)."""
        events = self._page_events.copy()
        self._page_events.clear()
        return events

    def set_active(self, index: int) -> None:
        """Set the active page by index. Called by activate_tab()."""
        pages = self._context.pages
        if 0 <= index < len(pages):
            self._active = pages[index]

    async def create(
        self, url: str | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[Page]:
        try:
            pw_page = await self._context.new_page()
            # _on_new_page event handler auto-sets _active and attaches listeners
            page = self._wrap_page(pw_page)
            if url:
                result = await page.open(url)
                if not result.success:
                    return Result.fail(result.error.code, result.error.message)
            return Result.ok(page)
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))

    async def get_current(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[Page]:
        # Active exists and still in context
        if self._active and self._active in self._context.pages:
            return Result.ok(self._wrap_page(self._active))
        # Fallback: most recently created page
        pages = self._context.pages
        if pages:
            self._active = pages[-1]
            return Result.ok(self._wrap_page(self._active))
        # No pages at all — auto-create a blank page to keep the context
        # alive.  This handles external tab closures (VNC, window.close())
        # symmetrically with the protection in close().
        try:
            pw_page = await self._context.new_page()
            page = self._wrap_page(pw_page)
            # _on_new_page sets _active automatically
            self._page_events.append({
                "type": "recovery",
                "url": pw_page.url,
                "reason": "all tabs were closed externally",
            })
            return Result.ok(page)
        except PWError as e:
            return Result.fail(E_PAGE_CURRENT_NOT_FOUND, f"No pages open and recovery failed: {e}")

    async def list(
        self, extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[list[PageInfo]]:
        pages = self._context.pages
        # Resolve active for comparison
        active = self._active if (self._active and self._active in pages) else (pages[-1] if pages else None)
        result = []
        for i, p in enumerate(pages):
            result.append(PageInfo(
                index=i,
                url=p.url,
                title=await p.title(),
                is_active=(p is active),
            ))
        return Result.ok(result)

    async def close(
        self, index: int | None = None,
        extra: Any | None = None, ctx: OperationContext | None = None,
    ) -> Result[None]:
        pages = self._context.pages
        if not pages:
            return Result.fail(E_PAGE_NOT_FOUND, "No pages to close")
        target = index if index is not None else len(pages) - 1
        if target < 0 or target >= len(pages):
            return Result.fail(E_PAGE_NOT_FOUND, f"Page index {target} out of range")
        try:
            pw_page = pages[target]
            self._closing_via_api.add(pw_page)
            self._listeners_attached.discard(pw_page)
            # Invalidate active if we're closing the active page
            if self._active is pw_page:
                self._active = None
            # If this is the last page, create a blank page first to keep
            # the CDP default BrowserContext alive.  Without at least one
            # page, Chromium marks the context as closed and subsequent
            # context-level operations (e.g. add_cookies) will fail.
            if len(pages) == 1:
                await self._context.new_page()
            await pw_page.close()
            return Result.ok()
        except PWError as e:
            return Result.fail(E_PAGE_ACTION_FAILED, str(e))
