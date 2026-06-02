"""Backward-compatible aliases for request-context naming."""

from __future__ import annotations

from typing import Any

from app.core import logid as _logid_context

_request_id_var = _logid_context._logid_var
_tags_var = _logid_context._tags_var


def get_request_id() -> str:
    return _logid_context.get_request_id()


def set_request_id(request_id: str) -> None:
    _logid_context.set_request_id(request_id)


def get_extra_tags() -> dict[str, str]:
    return _logid_context.get_extra_tags()


def set_extra_tags(tags: dict[str, str]) -> None:
    _logid_context.set_extra_tags(tags)


def update_extra_tags(**kwargs: Any) -> None:
    _logid_context.update_extra_tags(**kwargs)
