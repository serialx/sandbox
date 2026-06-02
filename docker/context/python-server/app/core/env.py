from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)


def parse_int_env(
    value: str | int | None,
    *,
    env_name: str,
    default: int | None = None,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int | None:
    """Parse an integer env value, treating empty strings as unset."""
    if value is None:
        return default

    if isinstance(value, int):
        parsed = value
    else:
        trimmed = value.strip()
        if not trimmed:
            return default
        try:
            parsed = int(trimmed)
        except ValueError:
            logger.warning('Invalid %s=%r, using default %r', env_name, value, default)
            return default

    if min_value is not None and parsed < min_value:
        logger.warning(
            'Out-of-range %s=%r (min=%s), using default %r',
            env_name,
            parsed,
            min_value,
            default,
        )
        return default

    if max_value is not None and parsed > max_value:
        logger.warning(
            'Out-of-range %s=%r (max=%s), using default %r',
            env_name,
            parsed,
            max_value,
            default,
        )
        return default

    return parsed


def get_env_int(
    name: str,
    default: int,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    """Read an integer env var with empty/invalid fallback to default."""
    result = parse_int_env(
        os.environ.get(name),
        env_name=name,
        default=default,
        min_value=min_value,
        max_value=max_value,
    )
    return default if result is None else result
