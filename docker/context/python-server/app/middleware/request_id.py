"""Backward-compatible import aliases that map request-id naming to LogID."""

from app.middleware.logid import (
    LOGID_HEADER as LEGACY_LOGID_HEADER,
    LOGID_HEADERS as REQUEST_ID_HEADERS,
    LogIDMiddleware as RequestIDMiddleware,
    generate_logid as generate_request_id,
)

REQUEST_ID_HEADER = LEGACY_LOGID_HEADER

__all__ = [
    'LEGACY_LOGID_HEADER',
    'REQUEST_ID_HEADER',
    'REQUEST_ID_HEADERS',
    'RequestIDMiddleware',
    'generate_request_id',
]
