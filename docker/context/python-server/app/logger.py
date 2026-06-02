from __future__ import annotations

import logging
import os

from app.logging.handlers import build_internal_streamlog_handler, build_stdout_handler


def _is_sandboxd_env() -> bool:
    """Check if running in sandboxd-managed environment (has LogAgent)."""
    return os.environ.get('FAAS_SANDBOX_RUNTIME_INJECTION_ENABLE_SANDBOXD', '').lower() == 'true'


def setup_logging(level: str = 'INFO') -> None:
    """
    Configure a general-purpose logger with millisecond timestamps.
    In sandboxd environments, also attaches StreamLog handler for log collection.
    """
    log_format = '[%(asctime)s.%(msecs)03d:%(levelname)s] %(message)s'
    date_format = '%m%d/%H%M%S'

    formatter = logging.Formatter(log_format, datefmt=date_format)

    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    root_logger.addHandler(build_stdout_handler(formatter))

    # StreamLog: only in sandboxd environments where LogAgent is available.
    if _is_sandboxd_env():
        try:
            root_logger.addHandler(build_internal_streamlog_handler(root_logger.level))
            logging.info('StreamLog handler enabled (sandboxd mode)')
        except Exception:
            logging.warning('Failed to initialize StreamLog handler', exc_info=True)
