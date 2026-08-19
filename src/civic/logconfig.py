"""structlog configuration: JSON events to a file, one object per line.

Kept tiny and separate so any entry point can call :func:`configure_logging`
once at startup. The fetcher and later phases just do ``structlog.get_logger()``.
"""

import logging
from pathlib import Path

import structlog


def configure_logging(log_path: Path) -> None:
    """Send structured logs as JSON lines to ``log_path`` (created if needed)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    # Replace handlers so repeated calls (e.g. across CLI commands in tests)
    # don't stack duplicate file writers.
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
