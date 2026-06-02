"""Backward-compatible aliases for request-scoped logging context."""

from __future__ import annotations

from typing import Any

from app.core import logid as _logid_context

_logid_var = _logid_context._logid_var
_tags_var = _logid_context._tags_var


def get_logid() -> str:
    return _logid_context.get_logid()


def set_logid(logid: str) -> None:
    _logid_context.set_logid(logid)


def get_extra_tags() -> dict[str, str]:
    return _logid_context.get_extra_tags()


def set_extra_tags(tags: dict[str, str]) -> None:
    _logid_context.set_extra_tags(tags)


def update_extra_tags(**kwargs: Any) -> None:
    _logid_context.update_extra_tags(**kwargs)
