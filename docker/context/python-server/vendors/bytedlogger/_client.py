"""Unix socket clients for ttlogagent.

Supports V1 (simple) and V3 (batched) protocols.
Warns on transport failures; V3 retains pending batches for retry.
"""

from __future__ import annotations

import atexit
import errno as errno_mod
import logging
import os
import platform
import signal
import socket
import threading
import time

from ._proto import BatchMsgHeader, MsgHeaderV3, MsgV1, MsgV3, _encode_bytes_field

_logger = logging.getLogger("bytedlogger.client")
_logger.propagate = False  # CRITICAL: prevent recursive emit → deadlock
_logger.addHandler(logging.StreamHandler())  # agent status → stderr only
_logger.setLevel(logging.WARNING)

_IS_LINUX = platform.system() == "Linux"

_DEFAULT_V1_SOCKET_PATH = "/opt/tmp/ttlogagent/socket.sock"
_DEFAULT_V3_SOCKET_PATH = "/opt/tmp/ttlogagent/unixpacket_v3.sock"
_V3_VERSION_MASK = b"\x03\x00"
_WARN_THROTTLE_SECONDS = 30.0
_DEFAULT_V3_MAX_PACKET_BYTES = 64 * 1024

# Maximum pending messages before dropping oldest (prevents OOM if agent is down)
_MAX_PENDING = 10000


def _resolve_positive_int_env(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return max(value, minimum)


class _BaseClient:
    """Base Unix socket client with reconnection logic."""

    def __init__(
        self,
        socket_path: str,
        connect_timeout: float = 0.2,
        send_timeout: float = 0.2,
        send_buf: int | None = None,
    ):
        self._socket_path = socket_path.encode() if isinstance(socket_path, str) else socket_path
        self._connect_timeout = connect_timeout
        self._send_timeout = send_timeout
        self._send_buf = send_buf
        self._sock: socket.socket | None = None
        self._last_connect_ms: int = 0
        self._prev_available: bool | None = None
        self._cached_available: bool | None = None
        self._last_avail_check: float = 0.0
        self._last_warning_key: tuple[str, str] | None = None
        self._last_warning_ts: float = 0.0
        self._last_send_errno: int | None = None

    def _invalidate_avail_cache(self) -> None:
        """Force next available check to re-stat. Call after send failures."""
        self._cached_available = None

    def _warn_transport_issue(self, issue: str, detail: str) -> None:
        """Log transport failures once per issue/detail pair within a throttle window."""
        now = time.monotonic()
        key = (issue, detail)
        if (
            self._last_warning_key == key
            and now - self._last_warning_ts < _WARN_THROTTLE_SECONDS
        ):
            return

        self._last_warning_key = key
        self._last_warning_ts = now
        _logger.warning(
            "logagent %s: %s (%s)",
            issue,
            self._socket_path.decode(),
            detail,
        )

    def _clear_transport_issue(self) -> None:
        self._last_warning_key = None
        self._last_warning_ts = 0.0

    @staticmethod
    def _format_os_error(exc: OSError) -> str:
        if exc.errno is not None:
            return f"[Errno {exc.errno}] {exc.strerror or exc}"
        return str(exc)

    @property
    def available(self) -> bool:
        """Check socket availability, cached for 5 seconds."""
        now = time.monotonic()
        if self._cached_available is None or now - self._last_avail_check > 5.0:
            prev = self._cached_available
            self._cached_available = _IS_LINUX and os.path.exists(self._socket_path)
            self._last_avail_check = now
            if self._cached_available and not prev and prev is not None:
                _logger.info("logagent socket available: %s", self._socket_path.decode())
            elif not self._cached_available and prev:
                _logger.warning("logagent socket lost: %s", self._socket_path.decode())
        return self._cached_available

    def _build_socket(self) -> bool:
        now_ms = int(time.time() * 1000)
        if now_ms - self._last_connect_ms < 500:
            return False
        self._last_connect_ms = now_ms
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            if self._send_buf:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self._send_buf)
            sock.settimeout(self._connect_timeout)
            sock.connect(self._socket_path)
            sock.settimeout(self._send_timeout)
            self._sock = sock
            self._clear_transport_issue()
            return True
        except OSError as exc:
            self._sock = None
            self._warn_transport_issue("connect failed", self._format_os_error(exc))
            return False

    def _send(self, buf: bytes) -> bool:
        """Send buffer to agent. Returns True if sent successfully."""
        self._last_send_errno = None
        if not self.available:
            self._warn_transport_issue(
                "socket unavailable",
                "path missing or agent not ready",
            )
            return False

        if self._sock:
            try:
                self._sock.send(buf)
                self._clear_transport_issue()
                return True
            except OSError as exc:
                self._last_send_errno = exc.errno
                self._sock = None
                self._invalidate_avail_cache()
                self._warn_transport_issue("send failed", self._format_os_error(exc))

        if self._build_socket() and self._sock:
            try:
                self._sock.send(buf)
                self._clear_transport_issue()
                return True
            except OSError as exc:
                self._last_send_errno = exc.errno
                self._sock = None
                self._invalidate_avail_cache()
                self._warn_transport_issue("send failed", self._format_os_error(exc))
        return False

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class ClientV1(_BaseClient):
    """V1 protocol: send each message immediately."""

    def __init__(
        self,
        socket_path: str = _DEFAULT_V1_SOCKET_PATH,
        connect_timeout: float = 0.2,
        send_timeout: float = 0.2,
        send_buf: int | None = None,
    ):
        super().__init__(socket_path, connect_timeout, send_timeout, send_buf)

    def emit(self, msg: bytes, tags: dict[bytes, bytes]) -> None:
        pb = MsgV1(msg, tags)
        self._send(pb.marshal())


class ClientV3(_BaseClient):
    """V3 protocol: batch messages and flush periodically.

    Uses a daemon flush thread. Registers SIGTERM handler to flush
    remaining logs before process exit (covers supervisor stop).
    """

    def __init__(
        self,
        batch_header: BatchMsgHeader,
        socket_path: str = _DEFAULT_V3_SOCKET_PATH,
        connect_timeout: float = 0.2,
        send_timeout: float = 0.2,
        send_buf: int | None = None,
        queue_size: int = 1024,
        flush_interval: float = 3.0,
        max_packet_bytes: int | None = None,
    ):
        super().__init__(socket_path, connect_timeout, send_timeout, send_buf)
        self._batch_header = batch_header
        self._batch_header_field = _encode_bytes_field(0x0A, batch_header.marshal())
        self._queue_size = queue_size
        self._flush_interval = flush_interval
        self._max_packet_bytes = max_packet_bytes or _resolve_positive_int_env(
            "BYTEDLOGGER_V3_MAX_PACKET_BYTES",
            _DEFAULT_V3_MAX_PACKET_BYTES,
            minimum=4096,
        )
        self._pending: list[MsgV3] = []
        self._lock = threading.Lock()
        self._closed = False
        self._owner_pid = os.getpid()

        self._stop_event = threading.Event()
        self._flush_thread: threading.Thread | None = None
        self._start_flush_thread()

        atexit.register(self.close)
        self._install_sigterm_handler()

    def _install_sigterm_handler(self) -> None:
        """Chain SIGTERM handler to flush logs before supervisor kills us."""
        try:
            prev_handler = signal.getsignal(signal.SIGTERM)
        except (OSError, ValueError):
            return

        def _on_sigterm(signum: int, frame: object) -> None:
            self.close()
            if callable(prev_handler) and prev_handler not in (signal.SIG_DFL, signal.SIG_IGN):
                prev_handler(signum, frame)

        try:
            signal.signal(signal.SIGTERM, _on_sigterm)
        except (OSError, ValueError):
            # Not main thread, or signal not allowed — skip silently
            pass

    def _start_flush_thread(self) -> None:
        self._stop_event.clear()
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="bytedlogger-flush",
        )
        self._flush_thread.start()

    def _ensure_flush_thread(self) -> None:
        """Restart flush thread if it died (e.g., after fork)."""
        if self._closed:
            return
        current_pid = os.getpid()
        if current_pid != self._owner_pid:
            # We're in a forked child — reset state
            self._owner_pid = current_pid
            self._sock = None  # parent's socket is invalid
            self._start_flush_thread()
            return
        if self._flush_thread is None or not self._flush_thread.is_alive():
            self._start_flush_thread()

    def _flush_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._flush_interval)
            self.commit()

    def emit(
        self,
        msg: bytes,
        tags: dict[bytes, bytes],
        level: bytes,
        location: bytes,
        log_id: bytes,
        ts: int = 0,
        span_id: int = 0,
        *,
        trans_id: int | None = None,
    ) -> None:
        if self._closed:
            return
        if trans_id is not None and ts == 0:
            ts = trans_id
        header = MsgHeaderV3(level, location, log_id, ts, span_id)
        message = MsgV3(header, msg, tags)
        with self._lock:
            self._ensure_flush_thread()
            # Drop oldest if queue is too large (agent down, prevents OOM)
            if len(self._pending) >= _MAX_PENDING:
                drop_count = len(self._pending) - _MAX_PENDING + self._queue_size
                del self._pending[:drop_count]
            self._pending.append(message)
            if len(self._pending) >= self._queue_size:
                self._flush_locked()

    def commit(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        """Flush pending messages. Must be called with self._lock held.

        Note: _send() may block up to connect_timeout (0.2s) on reconnect.
        This is acceptable because flush happens at batch boundaries or on
        the background thread, not on every log call.
        """
        if not self._pending:
            return
        messages, self._pending = self._pending, []
        batches = self._split_packet_batches(messages)
        for index, batch in enumerate(batches):
            packet = self._build_packet(batch)
            if not self._send(packet):
                if self._last_send_errno == errno_mod.EMSGSIZE:
                    self._warn_transport_issue(
                        "drop oversized batch",
                        "logagent rejected packet as too large",
                    )
                    continue
                # Preserve ordering so unsent messages can be retried on the next flush.
                remaining = [
                    message
                    for remaining_batch in batches[index:]
                    for message, _ in remaining_batch
                ]
                self._pending = remaining + self._pending
                return

    def _build_packet(self, batch: list[tuple[MsgV3, bytes]]) -> bytes:
        parts = bytearray()
        parts.extend(_V3_VERSION_MASK)
        parts.extend(self._batch_header_field)
        for _, encoded_message in batch:
            parts.extend(encoded_message)
        return bytes(parts)

    def _split_packet_batches(
        self, messages: list[MsgV3]
    ) -> list[list[tuple[MsgV3, bytes]]]:
        batches: list[list[tuple[MsgV3, bytes]]] = []
        current: list[tuple[MsgV3, bytes]] = []
        current_size = len(_V3_VERSION_MASK) + len(self._batch_header_field)

        for message in messages:
            encoded_message = _encode_bytes_field(0x12, message.marshal())
            encoded_size = len(encoded_message)
            single_packet_size = (
                len(_V3_VERSION_MASK) + len(self._batch_header_field) + encoded_size
            )
            if single_packet_size > self._max_packet_bytes:
                self._warn_transport_issue(
                    "drop oversized message",
                    f"packet exceeds {self._max_packet_bytes} bytes",
                )
                continue

            if current and current_size + encoded_size > self._max_packet_bytes:
                batches.append(current)
                current = []
                current_size = len(_V3_VERSION_MASK) + len(self._batch_header_field)

            current.append((message, encoded_message))
            current_size += encoded_size

        if current:
            batches.append(current)
        return batches

    def close(self) -> None:
        """Idempotent close: flush remaining logs, stop thread, close socket."""
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self.commit()
        super().close()
