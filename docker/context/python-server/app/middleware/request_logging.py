"""HTTP request logging middleware with safe request summaries."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import parse_qsl

from starlette.datastructures import Headers, QueryParams
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging.sanitizer import sanitize_for_logging, short_sha256
from app.middleware.logid import LOGID_HEADER
from vendors.bytedlogger import get_logid
from vendors.bytedlogger._runtime import generate_logid

_CAPTURE_CONTENT_TYPES = (
    "application/json",
    "application/x-www-form-urlencoded",
    "text/",
)


class RequestLoggingMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_capture_bytes: int = 8192,
    ) -> None:
        self.app = app
        self.max_capture_bytes = max_capture_bytes
        self.logger = logging.getLogger("app.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        request_body, wrapped_receive = await self._prepare_request_body(
            scope,
            request_headers,
            receive,
        )

        started_at = time.perf_counter()
        status_code = 500
        response_headers: dict[str, str] = {}
        caught_exc: Exception | None = None

        async def send_with_logging(message: Message) -> None:
            nonlocal status_code, response_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = {
                    key.decode("latin-1"): value.decode("latin-1")
                    for key, value in message.get("headers", [])
                }
            try:
                await send(message)
            except Exception as exc:
                self._log_send_failure(
                    scope=scope,
                    request_headers=request_headers,
                    response_headers=response_headers,
                    status_code=status_code,
                    message=message,
                    exc=exc,
                )
                raise

        try:
            await self.app(scope, wrapped_receive, send_with_logging)
        except Exception as exc:
            caught_exc = exc
            raise
        finally:
            self._log_request(
                scope=scope,
                headers=request_headers,
                body_summary=request_body,
                status_code=status_code,
                response_headers=response_headers,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                exc=caught_exc,
            )

    async def _prepare_request_body(
        self,
        scope: Scope,
        headers: Headers,
        receive: Receive,
    ) -> tuple[dict[str, Any], Receive]:
        method = scope["method"].upper()
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        content_length = _parse_content_length(headers.get("content-length"))

        summary: dict[str, Any] = {
            "content_type": content_type or None,
            "content_length": content_length,
        }

        if method in {"GET", "HEAD", "OPTIONS"}:
            summary["captured"] = False
            summary["reason"] = "body_not_expected"
            return summary, receive

        if content_length == 0:
            summary["captured"] = False
            summary["reason"] = "empty_body"
            return summary, receive

        if content_length is None:
            summary["captured"] = False
            summary["reason"] = "unknown_content_length"
            return summary, receive

        if content_length > self.max_capture_bytes:
            summary["captured"] = False
            summary["reason"] = "content_length_exceeds_limit"
            return summary, receive

        if not _can_capture_body(content_type):
            summary["captured"] = False
            summary["reason"] = "content_type_not_supported"
            return summary, receive

        raw_messages: list[Message] = []
        raw_body = bytearray()

        while True:
            message = await receive()
            raw_messages.append(message)
            if message["type"] != "http.request":
                break

            raw_body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break

        summary["captured"] = True
        summary["sha256"] = short_sha256(bytes(raw_body))
        summary["_parsed_body"] = _parse_body_summary(content_type, bytes(raw_body))

        replay_messages = list(raw_messages)

        async def replay_receive() -> Message:
            if replay_messages:
                return replay_messages.pop(0)
            # After replaying the captured request body, delegate back to the
            # original receive() so streaming responses can still observe
            # client disconnect events instead of spinning on synthetic
            # empty http.request messages forever.
            return await receive()

        return summary, replay_receive

    def _log_request(
        self,
        *,
        scope: Scope,
        headers: Headers,
        body_summary: dict[str, Any],
        status_code: int,
        response_headers: dict[str, str],
        duration_ms: float,
        exc: Exception | None,
    ) -> None:
        route = scope.get("route")
        handler = scope.get("endpoint")
        context_logid = get_logid()
        logid = (
            response_headers.get(LOGID_HEADER)
            or response_headers.get(LOGID_HEADER.lower())
            or headers.get(LOGID_HEADER)
            or (context_logid if context_logid != "-" else None)
        )

        query = QueryParams(scope["query_string"].decode("utf-8", errors="replace"))
        query_data = {
            key: query.getlist(key) if len(query.getlist(key)) > 1 else query.get(key)
            for key in query.keys()
        }
        path_params = scope.get("path_params") or {}

        record_logid = logid or generate_logid()
        include_bulky_preview = exc is not None or status_code >= 400

        log_entry: dict[str, Any] = {
            "event": "http_request",
            "method": scope["method"],
            "path": scope["path"],
            "route": getattr(route, "path", None),
            "handler": getattr(handler, "__name__", None),
            "status": status_code,
            "duration_ms": duration_ms,
            "logid": record_logid,
            "client_ip": scope.get("client", ("-", 0))[0],
            "query": sanitize_for_logging(query_data, field_name="query"),
            "path_params": sanitize_for_logging(path_params, field_name="path_params"),
            "request": self._build_request_log_summary(
                body_summary,
                include_bulky_preview=include_bulky_preview,
            ),
            "response": {
                "content_type": response_headers.get("content-type"),
                "content_length": _parse_content_length(response_headers.get("content-length")),
            },
        }

        if exc is not None:
            log_entry["exception"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }

        message = f"HTTP_REQUEST {json.dumps(log_entry, ensure_ascii=False, sort_keys=True)}"
        self.logger.info(
            message,
            extra={
                "tags": {
                    "_logid": record_logid,
                    "http_method": scope["method"],
                    "http_path": scope["path"],
                    "http_route": getattr(route, "path", scope["path"]) or scope["path"],
                    "http_status": str(status_code),
                }
            },
        )

    def _log_send_failure(
        self,
        *,
        scope: Scope,
        request_headers: Headers,
        response_headers: dict[str, str],
        status_code: int,
        message: Message,
        exc: Exception,
    ) -> None:
        route = scope.get("route")
        context_logid = get_logid()
        record_logid = (
            response_headers.get(LOGID_HEADER)
            or response_headers.get(LOGID_HEADER.lower())
            or request_headers.get(LOGID_HEADER)
            or (context_logid if context_logid != "-" else None)
            or generate_logid()
        )

        body = message.get("body", b"")
        if isinstance(body, bytes):
            body_len = len(body)
        else:
            body_len = len(str(body))

        payload = {
            "event": "http_response_send_failure",
            "method": scope["method"],
            "path": scope["path"],
            "route": getattr(route, "path", None),
            "status": status_code,
            "logid": record_logid,
            "response": {
                "content_type": response_headers.get("content-type"),
                "content_length": _parse_content_length(
                    response_headers.get("content-length")
                ),
            },
            "message": {
                "type": message["type"],
                "body_len": body_len,
                "more_body": message.get("more_body", False),
            },
            "exception": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
        self.logger.error(
            "HTTP_RESPONSE_SEND_FAILURE %s",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            extra={
                "tags": {
                    "_logid": record_logid,
                    "http_method": scope["method"],
                    "http_path": scope["path"],
                    "http_route": getattr(route, "path", scope["path"]) or scope["path"],
                    "http_status": str(status_code),
                }
            },
        )

    def _build_request_log_summary(
        self,
        body_summary: dict[str, Any],
        *,
        include_bulky_preview: bool,
    ) -> dict[str, Any]:
        request_summary = {
            key: value
            for key, value in body_summary.items()
            if key != "_parsed_body"
        }
        if "_parsed_body" in body_summary:
            request_summary["parsed"] = sanitize_for_logging(
                body_summary["_parsed_body"],
                field_name="body",
                include_bulky_preview=include_bulky_preview,
            )
        return request_summary


def _can_capture_body(content_type: str) -> bool:
    if not content_type:
        return False
    return content_type in _CAPTURE_CONTENT_TYPES or any(
        content_type.startswith(prefix)
        for prefix in _CAPTURE_CONTENT_TYPES
        if prefix.endswith("/")
    )


def _parse_body_summary(content_type: str, raw_body: bytes) -> Any:
    if not raw_body:
        return None

    decoded = raw_body.decode("utf-8", errors="replace")
    try:
        if content_type == "application/json":
            return json.loads(decoded)
        if content_type == "application/x-www-form-urlencoded":
            parsed = {}
            for key, value in parse_qsl(decoded, keep_blank_values=True):
                existing = parsed.get(key)
                if existing is None:
                    parsed[key] = value
                elif isinstance(existing, list):
                    existing.append(value)
                else:
                    parsed[key] = [existing, value]
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    return decoded


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
