"""Logging handler factories for open-source and internal deployments."""

from __future__ import annotations

import logging
import sys


def build_stdout_handler(formatter: logging.Formatter) -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    return handler


def build_internal_streamlog_handler(level: int) -> logging.Handler:
    from vendors.bytedlogger import StreamLogHandler

    return StreamLogHandler(level=level, version=3)
