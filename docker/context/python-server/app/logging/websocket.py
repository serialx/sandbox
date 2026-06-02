from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

from app.logging.sanitizer import sanitize_for_logging
from app.middleware.logid import LOGID_HEADER
from vendors.bytedlogger import get_logid, set_logid
from vendors.bytedlogger._runtime import generate_logid


def bind_websocket_logid(websocket: WebSocket) -> tuple[str, str]:
    logid = websocket.headers.get(LOGID_HEADER) or generate_logid()
    previous_logid = get_logid()
    set_logid(logid)
    return logid, previous_logid


def restore_logid(previous_logid: str) -> None:
    set_logid(previous_logid)


def log_websocket_event(
    logger: logging.Logger,
    event: str,
    *,
    websocket: WebSocket,
    route: str,
    session_id: str | None = None,
    level: int = logging.INFO,
    logid: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    current_logid = logid or get_logid() or "-"
    payload: dict[str, Any] = {
        "event": event,
        "route": route,
        "path": websocket.url.path,
        "client_ip": websocket.client.host if websocket.client else "-",
        "logid": current_logid,
    }

    if session_id is not None:
        payload["session_id"] = session_id

    if websocket.query_params:
        payload["query"] = sanitize_for_logging(
            dict(websocket.query_params),
            field_name="query",
        )

    if details:
        payload["details"] = sanitize_for_logging(
            details,
            field_name="details",
            include_bulky_preview=level >= logging.ERROR,
        )

    logger.log(
        level,
        "WEBSOCKET_EVENT %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        extra={
            "tags": {
                "_logid": current_logid,
                "ws_event": event,
                "ws_route": route,
            }
        },
    )
