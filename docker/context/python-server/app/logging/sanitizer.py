from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlsplit

MAX_LOG_DEPTH = 4
MAX_LOG_ITEMS = 20
MAX_STRING_PREVIEW = 160
MAX_BULKY_PREVIEW = 256

SENSITIVE_FIELD_MARKERS = (
    "access_token",
    "api_key",
    "apikey",
    "auth_cmd",
    "authorization",
    "cookie",
    "credential",
    "jwt",
    "password",
    "passwd",
    "refresh_token",
    "secret",
    "set-cookie",
    "ticket",
    "token",
)

BULKY_FIELD_MARKERS = (
    "body",
    "code",
    "command",
    "content",
    "expression",
    "file_text",
    "html",
    "input",
    "markdown",
    "new_str",
    "old_str",
    "script",
    "stderr",
    "stdin",
    "stdout",
    "text",
    "traceback",
)


def short_sha256(value: bytes | str) -> str:
    payload = value if isinstance(value, bytes) else value.encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:12]


def summarize_headers_for_logging(headers: Mapping[str, str]) -> dict[str, Any]:
    return sanitize_for_logging(dict(headers), field_name="headers")


def sanitize_for_logging(
    data: Any,
    *,
    field_name: str | None = None,
    path: tuple[str, ...] = (),
    depth: int = 0,
    include_bulky_preview: bool = False,
    bulky_preview_chars: int = MAX_BULKY_PREVIEW,
) -> Any:
    current_path = path + ((field_name,) if field_name else ())

    if depth >= MAX_LOG_DEPTH:
        return {"type": type(data).__name__, "summary": "max_depth"}

    if data is None or isinstance(data, (bool, int, float)):
        return data

    if isinstance(data, bytes):
        return {"type": "bytes", "len": len(data), "sha256": short_sha256(data)}

    if isinstance(data, str):
        return _sanitize_string(
            data,
            current_path,
            include_bulky_preview=include_bulky_preview,
            bulky_preview_chars=bulky_preview_chars,
        )

    if hasattr(data, "model_dump"):
        return sanitize_for_logging(
            data.model_dump(exclude_none=True),
            path=current_path,
            depth=depth + 1,
            include_bulky_preview=include_bulky_preview,
            bulky_preview_chars=bulky_preview_chars,
        )

    if isinstance(data, Mapping):
        items: dict[str, Any] = {}
        for index, (key, value) in enumerate(data.items()):
            if index >= MAX_LOG_ITEMS:
                items["__truncated_items__"] = len(data) - MAX_LOG_ITEMS
                break
            key_str = str(key)
            items[key_str] = sanitize_for_logging(
                value,
                field_name=key_str,
                path=current_path,
                depth=depth + 1,
                include_bulky_preview=include_bulky_preview,
                bulky_preview_chars=bulky_preview_chars,
            )
        return items

    if isinstance(data, Sequence):
        sanitized_items: list[Any] = []
        for index, item in enumerate(data):
            if index >= MAX_LOG_ITEMS:
                sanitized_items.append({"__truncated_items__": len(data) - MAX_LOG_ITEMS})
                break
            sanitized_items.append(
                sanitize_for_logging(
                    item,
                    path=current_path,
                    depth=depth + 1,
                    include_bulky_preview=include_bulky_preview,
                    bulky_preview_chars=bulky_preview_chars,
                )
            )
        return sanitized_items

    return _sanitize_string(
        str(data),
        current_path,
        include_bulky_preview=include_bulky_preview,
        bulky_preview_chars=bulky_preview_chars,
    )


def _sanitize_string(
    value: str,
    path: tuple[str, ...],
    *,
    include_bulky_preview: bool = False,
    bulky_preview_chars: int = MAX_BULKY_PREVIEW,
) -> Any:
    if _is_sensitive_path(path):
        return {
            "redacted": True,
            "len": len(value),
            "sha256": short_sha256(value),
        }

    field_name = _normalize_log_field_name(path[-1]) if path else ""
    if field_name.endswith("url") or field_name.endswith("uri"):
        return _summarize_url(value)

    if _is_bulky_path(path):
        summary = {
            "len": len(value),
            "sha256": short_sha256(value),
        }
        if include_bulky_preview and value:
            summary["preview"] = _build_preview(value, bulky_preview_chars)
            if len(value) > bulky_preview_chars:
                summary["preview_tail"] = _build_tail_preview(
                    value, bulky_preview_chars
                )
        return summary

    if len(value) <= MAX_STRING_PREVIEW:
        return value

    return {
        "len": len(value),
        "sha256": short_sha256(value),
        "preview": _build_preview(value, MAX_STRING_PREVIEW),
    }


def _build_preview(value: str, limit: int) -> str:
    return value[:limit].replace("\n", "\\n")


def _build_tail_preview(value: str, limit: int) -> str:
    return value[-limit:].replace("\n", "\\n")


def _summarize_url(value: str) -> Any:
    parts = urlsplit(value)
    if not parts.scheme and not parts.netloc:
        return value if len(value) <= MAX_STRING_PREVIEW else _sanitize_string(value, ())

    query_items: dict[str, Any] = {}
    parsed_query = parse_qsl(parts.query, keep_blank_values=True)
    for key, item in parsed_query[:MAX_LOG_ITEMS]:
        existing = query_items.get(key)
        sanitized_item = sanitize_for_logging(item, field_name=key, path=("query",))
        if existing is None:
            query_items[key] = sanitized_item
        elif isinstance(existing, list):
            existing.append(sanitized_item)
        else:
            query_items[key] = [existing, sanitized_item]

    if len(parsed_query) > MAX_LOG_ITEMS:
        query_items["__truncated_items__"] = len(parsed_query) - MAX_LOG_ITEMS

    summary: dict[str, Any] = {
        "url": f"{parts.scheme}://{parts.netloc}{parts.path}",
    }
    if query_items:
        summary["query"] = query_items
    if parts.fragment:
        summary["has_fragment"] = True
    return summary


def _is_bulky_path(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    leaf = _normalize_log_field_name(path[-1])
    return any(_normalize_log_field_name(marker) in leaf for marker in BULKY_FIELD_MARKERS)


def _is_sensitive_path(path: tuple[str, ...]) -> bool:
    if not path:
        return False

    leaf = _normalize_log_field_name(path[-1])
    if any(_normalize_log_field_name(marker) in leaf for marker in SENSITIVE_FIELD_MARKERS):
        return True

    return any("cookie" in _normalize_log_field_name(component) for component in path[:-1])


def _normalize_log_field_name(value: str) -> str:
    return value.lower().replace("-", "_").replace(" ", "_")
