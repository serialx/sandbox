"""LogID-scoped context shared by middleware and log handlers."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any


DEFAULT_LOGID = '-'
_EMPTY_TAGS: dict[str, str] = {}

_logid_var: ContextVar[str] = ContextVar('logid', default=DEFAULT_LOGID)
_tags_var: ContextVar[dict[str, str]] = ContextVar('log_tags', default=_EMPTY_TAGS)


def get_logid() -> str:
    return _logid_var.get()


def set_logid(logid: str) -> None:
    _logid_var.set(logid)


def get_extra_tags() -> dict[str, str]:
    return _tags_var.get()


def set_extra_tags(tags: dict[str, str]) -> None:
    _tags_var.set(tags)


def update_extra_tags(**kwargs: Any) -> None:
    current = get_extra_tags().copy()
    current.update({k: str(v) for k, v in kwargs.items()})
    _tags_var.set(current)


def get_request_id() -> str:
    return get_logid()


def set_request_id(request_id: str) -> None:
    set_logid(request_id)
