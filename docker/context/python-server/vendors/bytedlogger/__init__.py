"""bytedlogger - Pure Python rewrite for AIO Sandbox.

StreamLog integration via local ttlogagent Unix socket.
Zero external dependencies. Async-safe logID propagation via contextvars.

Usage:
    # Option 1: Auto-configure (adds StreamLog + console handlers to root logger)
    from vendors.bytedlogger import setup
    setup()

    # Option 2: Manual handler
    from vendors.bytedlogger import StreamLogHandler
    handler = StreamLogHandler(version=3)
    logging.getLogger().addHandler(handler)

    # LogID management (async-safe)
    from vendors.bytedlogger import set_logid, get_logid
    set_logid("my-request-id")

    # Or pass per-record
    logger.info("hello", extra={"tags": {"_logid": "abc123"}})
"""

from ._context import (
    get_extra_tags,
    get_logid,
    set_extra_tags,
    set_logid,
    update_extra_tags,
)
from ._handler import StreamLogHandler

__all__ = [
    "StreamLogHandler",
    "setup",
    "get_logid",
    "set_logid",
    "get_extra_tags",
    "set_extra_tags",
    "update_extra_tags",
]


def setup(
    level: int | None = None,
    version: int = 3,
    tags: dict[str, str] | None = None,
    console: bool = True,
    format: str = "%(asctime)s %(levelname)s [%(name)s] %(message)s",
) -> None:
    """One-call setup: adds StreamLogHandler (+ optional console) to root logger.

    Safe to call even when agent socket is not available (non-Linux, no agent).
    """
    import logging

    root = logging.getLogger()

    if level is not None:
        root.setLevel(level)

    stream_handler = StreamLogHandler(level=level or logging.INFO, tags=tags, version=version)
    root.addHandler(stream_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level or logging.INFO)
        console_handler.setFormatter(logging.Formatter(format))
        root.addHandler(console_handler)
