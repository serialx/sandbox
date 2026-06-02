from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass

from app.core.env import get_env_int
from app.logging.decorators import trace_api
from app.models.display import DisplayRecordResult

logger = logging.getLogger(__name__)


@dataclass
class _ActiveRecorder:
    process: asyncio.subprocess.Process
    save_path: str
    start_time: float
    fps: int
    crf: int


class DisplayService:
    def __init__(self) -> None:
        self._recorder: _ActiveRecorder | None = None

    async def _detect_resolution(self, display: str) -> tuple[int, int]:
        """Detect X11 display resolution via xrandr."""
        try:
            env = {**os.environ, 'DISPLAY': display}
            proc = await asyncio.create_subprocess_exec(
                'xrandr',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            # Match line like: "VNC-0 connected 1280x1024+0+0" or "Screen 0: ... current 1280 x 1024"
            for line in stdout.decode().splitlines():
                # Connected output with resolution: "VNC-0 connected 1280x1024+0+0"
                m = re.search(r'\bconnected\s+(\d+)x(\d+)\+', line)
                if m:
                    w, h = int(m.group(1)), int(m.group(2))
                    logger.info(f'Detected display resolution: {w}x{h}')
                    return w, h
                # Fallback: "Screen 0: ... current 1280 x 1024"
                m = re.search(r'current\s+(\d+)\s*x\s*(\d+)', line)
                if m:
                    w, h = int(m.group(1)), int(m.group(2))
                    logger.info(f'Detected display resolution: {w}x{h}')
                    return w, h
        except Exception as e:
            logger.warning(f'Failed to detect display resolution: {e}')

        # Fallback to environment variables
        w = get_env_int('DISPLAY_WIDTH', 1280)
        h = get_env_int('DISPLAY_HEIGHT', 1024)
        logger.info(f'Using fallback display resolution: {w}x{h}')
        return w, h

    @trace_api('display')
    async def record(
        self,
        action: str,
        save_path: str | None = None,
        fps: int = 5,
        crf: int = 28,
        max_duration: float = 600.0,
        width: int | None = None,
        height: int | None = None,
    ) -> DisplayRecordResult:
        if action == 'start':
            return await self._start(
                save_path=save_path,
                fps=fps,
                crf=crf,
                max_duration=max_duration,
                width=width,
                height=height,
            )
        elif action == 'stop':
            return await self._stop()
        elif action == 'status':
            return self._status()
        else:
            raise ValueError(f'Unknown action: {action}')

    async def _start(
        self,
        save_path: str | None = None,
        fps: int = 5,
        crf: int = 28,
        max_duration: float = 600.0,
        width: int | None = None,
        height: int | None = None,
    ) -> DisplayRecordResult:
        if self._recorder is not None:
            logger.info('Recording already in progress, returning current status')
            return self._status()

        display = os.environ.get('DISPLAY', ':99.0')

        # Use caller-specified resolution, or auto-detect from X11
        if width is None or height is None:
            detected_w, detected_h = await self._detect_resolution(display)
            width = width or detected_w
            height = height or detected_h

        if save_path is None:
            os.makedirs('/tmp/recordings', exist_ok=True)
            save_path = f'/tmp/recordings/recording_{int(time.time())}.mp4'
        else:
            os.makedirs(os.path.dirname(save_path) or '.', exist_ok=True)

        cmd = [
            'ffmpeg',
            '-y',
            '-f', 'x11grab',
            '-video_size', f'{width}x{height}',
            '-framerate', str(fps),
            '-i', display,
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', str(crf),
            '-t', str(max_duration),
            '-pix_fmt', 'yuv420p',
            '-movflags', 'frag_keyframe+empty_moov',
            '-flush_packets', '1',
            save_path,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._recorder = _ActiveRecorder(
            process=process,
            save_path=save_path,
            start_time=time.time(),
            fps=fps,
            crf=crf,
        )

        logger.info(f'Display recording started: {save_path} ({fps}fps, crf={crf})')

        return DisplayRecordResult(
            status='recording',
            save_path=save_path,
        )

    async def _stop(self) -> DisplayRecordResult:
        if self._recorder is None:
            return DisplayRecordResult(status='idle')

        recorder = self._recorder
        process = recorder.process

        # Graceful stop: send 'q' to ffmpeg stdin to finalize MP4 moov atom
        if process.stdin and process.returncode is None:
            try:
                process.stdin.write(b'q')
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass

        # Wait for ffmpeg to finish writing, fallback to SIGKILL
        try:
            await asyncio.wait_for(process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning('ffmpeg did not exit in 10s, killing')
            process.kill()
            await process.wait()

        duration = time.time() - recorder.start_time
        file_size = None
        if os.path.exists(recorder.save_path):
            file_size = os.path.getsize(recorder.save_path)

        self._recorder = None

        logger.info(
            f'Display recording stopped: {recorder.save_path} '
            f'(duration={duration:.1f}s, size={file_size})'
        )

        return DisplayRecordResult(
            status='stopped',
            save_path=recorder.save_path,
            duration=round(duration, 2),
            file_size_bytes=file_size,
        )

    def _status(self) -> DisplayRecordResult:
        if self._recorder is None:
            return DisplayRecordResult(status='idle')

        duration = time.time() - self._recorder.start_time
        file_size = None
        if os.path.exists(self._recorder.save_path):
            file_size = os.path.getsize(self._recorder.save_path)

        return DisplayRecordResult(
            status='recording',
            save_path=self._recorder.save_path,
            duration=round(duration, 2),
            file_size_bytes=file_size,
        )

    async def cleanup(self) -> None:
        """Called during server shutdown to gracefully stop recording."""
        if self._recorder is not None:
            logger.info('Cleaning up active display recording on shutdown')
            await self._stop()
