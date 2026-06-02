"""StreamLogHandler - standard logging.Handler for ttlogagent.

Drop-in replacement: add to any logger, app code uses logging.xxx as usual.
"""

from __future__ import annotations

import logging
import platform
import sys

from ._client import ClientV1, ClientV3, _DEFAULT_V1_SOCKET_PATH, _DEFAULT_V3_SOCKET_PATH
from ._context import get_extra_tags, get_logid
from ._proto import BatchMsgHeader
from ._runtime import encode_host_bytes, resolve_runtime_meta


def _ensure_bytes(s: object, encoding: str = "utf-8") -> bytes:
    if isinstance(s, bytes):
        return s
    return str(s).encode(encoding)


class StreamLogHandler(logging.Handler):
    """Logging handler that writes to ByteDance StreamLog via local ttlogagent.

    Compatible with the original bytedlogger.StreamLogHandler wire format.
    Uses contextvars for logID propagation (async-safe).

    If the agent socket is not available, logs are silently dropped.

    All metadata fields (psm, cluster, host, etc.) can be overridden via
    the `meta` dict, falling back to standard TCE environment variables.
    Socket path can be overridden via `socket_path` for non-TCE environments.
    """

    def __init__(
        self,
        level: int = logging.NOTSET,
        tags: dict[str, str] | None = None,
        version: int = 3,
        timeout: float = 0.2,
        socket_path: str | None = None,
        socket_send_buf: int | None = None,
        queue_size: int = 1024,
        flush_interval: float = 3.0,
        meta: dict[str, str] | None = None,
    ):
        super().__init__(level)
        self.extra_tags = tags or {}
        self._version = version
        _meta = meta or {}
        runtime_meta = resolve_runtime_meta(_meta)

        self._psm = runtime_meta.psm
        self._cluster = runtime_meta.cluster
        self._pod_name = runtime_meta.pod_name
        self._stage = runtime_meta.stage
        self._host = runtime_meta.host
        self._idc = runtime_meta.idc
        self._tenant = _meta.get("tenant", "")
        self._log_stream = _meta.get("log_stream", "")
        self._language = "%s%d.%d" % (
            platform.python_implementation(),
            sys.version_info[0],
            sys.version_info[1],
        )
        self._host_bytes = encode_host_bytes(self._host)

        # Pre-compute static tag values (called once, not per-record)
        self._static_tags = {
            b"_language": _ensure_bytes(self._language),
            b"_taskName": _ensure_bytes(self._psm),
            b"_psm": _ensure_bytes(self._psm),
            b"_cluster": _ensure_bytes(self._cluster),
            b"_deployStage": _ensure_bytes(self._stage),
            b"_podName": _ensure_bytes(self._pod_name),
        }
        if self._version != 3 or not self._host_bytes:
            self._static_tags[b"_host"] = _ensure_bytes(self._host)

        if self._version == 3:
            header = BatchMsgHeader(
                task_name=_ensure_bytes(self._psm),
                language=_ensure_bytes(self._language),
                cluster=_ensure_bytes(self._cluster),
                psm=_ensure_bytes(self._psm),
                pod_name=_ensure_bytes(self._pod_name),
                stage=_ensure_bytes(self._stage),
                host=self._host_bytes,
                idc=_ensure_bytes(self._idc),
                tenant=_ensure_bytes(self._tenant),
                log_stream=_ensure_bytes(self._log_stream),
            )
            self._client: ClientV1 | ClientV3 = ClientV3(
                batch_header=header,
                socket_path=socket_path or _DEFAULT_V3_SOCKET_PATH,
                connect_timeout=timeout,
                send_timeout=timeout,
                send_buf=socket_send_buf,
                queue_size=queue_size,
                flush_interval=flush_interval,
            )
        else:
            self._client = ClientV1(
                socket_path=socket_path or _DEFAULT_V1_SOCKET_PATH,
                connect_timeout=timeout,
                send_timeout=timeout,
            )

    def _get_logid(self, record: logging.LogRecord) -> str:
        record_tags = getattr(record, "tags", None)
        if isinstance(record_tags, dict) and "_logid" in record_tags:
            return str(record_tags["_logid"])
        return get_logid()

    def _build_default_tags(self, record: logging.LogRecord) -> dict[bytes, bytes]:
        logid = self._get_logid(record)
        # Start with pre-computed static tags, add per-record dynamic ones
        tags = dict(self._static_tags)
        tags[b"_level"] = _ensure_bytes(record.levelname)
        tags[b"_ts"] = _ensure_bytes(int(record.created * 1000))
        tags[b"_process"] = _ensure_bytes(
            "%s(%d)" % (record.processName, record.process)
        )
        tags[b"_location"] = _ensure_bytes(
            "%s:%d" % (record.pathname, record.lineno)
        )
        tags[b"_logid"] = _ensure_bytes(logid)
        return tags

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self._version == 3:
                self._emit_v3(record)
            else:
                self._emit_v1(record)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            self.handleError(record)

    def _emit_v1(self, record: logging.LogRecord) -> None:
        tags = self._build_default_tags(record)
        for k, v in get_extra_tags().items():
            tags[_ensure_bytes(k)] = _ensure_bytes(v)
        for k, v in self.extra_tags.items():
            tags[_ensure_bytes(k)] = _ensure_bytes(v)
        record_tags = getattr(record, "tags", None)
        if isinstance(record_tags, dict):
            for k, v in record_tags.items():
                tags[_ensure_bytes(k)] = _ensure_bytes(v)

        msg = _ensure_bytes(self.format(record))
        self._client.emit(msg, tags)  # type: ignore[arg-type]

    def _emit_v3(self, record: logging.LogRecord) -> None:
        default_tags = self._build_default_tags(record)
        log_id = default_tags.get(b"_logid", b"-")
        level = _ensure_bytes(record.levelname)
        location = _ensure_bytes("%s:%d" % (record.pathname, record.lineno))
        ts = int(record.created * 1000)

        tags = dict(default_tags)
        for k, v in self.extra_tags.items():
            tags[_ensure_bytes(k)] = _ensure_bytes(v)
        for k, v in get_extra_tags().items():
            tags[_ensure_bytes(k)] = _ensure_bytes(v)
        record_tags = getattr(record, "tags", None)
        if isinstance(record_tags, dict):
            for k, v in record_tags.items():
                tags[_ensure_bytes(k)] = _ensure_bytes(v)
        if self._host_bytes:
            # V3 carries IP hosts in the batch header; drop any late `_host`
            # override so we do not send both packed and string forms.
            tags.pop(b"_host", None)

        msg = _ensure_bytes(self.format(record))
        self._client.emit(msg, tags, level, location, log_id, ts)  # type: ignore[arg-type]

    def close(self) -> None:
        self._client.close()
        super().close()
