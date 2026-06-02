from __future__ import annotations

import json
import logging
from typing import Any

from app.logging.sanitizer import sanitize_for_logging
from vendors.bytedlogger import get_logid


def _normalize_status(status: Any) -> Any:
    return getattr(status, 'value', status)


def log_execution_event(
    logger: logging.Logger,
    *,
    component: str,
    operation: str,
    phase: str,
    status: Any,
    session_id: str | None = None,
    command_id: str | None = None,
    exit_code: int | None = None,
    duration_ms: float | None = None,
    details: dict[str, Any] | None = None,
    level: int = logging.INFO,
) -> None:
    logid = get_logid() or '-'
    normalized_status = _normalize_status(status)

    payload: dict[str, Any] = {
        'event': 'execution',
        'component': component,
        'operation': operation,
        'phase': phase,
        'status': normalized_status,
        'logid': logid,
    }

    if session_id is not None:
        payload['session_id'] = session_id
    if command_id is not None:
        payload['command_id'] = command_id
    if exit_code is not None:
        payload['exit_code'] = exit_code
    if duration_ms is not None:
        payload['duration_ms'] = round(duration_ms, 2)
    if details:
        payload['details'] = sanitize_for_logging(
            details,
            field_name='details',
            include_bulky_preview=level >= logging.ERROR,
        )

    logger.log(
        level,
        'EXECUTION_EVENT %s',
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        extra={
            'tags': {
                '_logid': logid,
                'execution_component': component,
                'execution_operation': operation,
                'execution_phase': phase,
                'execution_status': str(normalized_status),
            }
        },
    )
