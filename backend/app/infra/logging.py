import logging
import sys
from pathlib import Path

import structlog

from app.infra.settings import get_settings


def configure_logging() -> None:
    """Configure structlog for JSON output. Call once at app startup."""
    settings = get_settings()

    log_dir = Path("/var/log/modir")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "modir.json.log"

    # Standard library logging — file + stdout
    logging.basicConfig(
        level=settings.log_level,
        format="%(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )

    # Structlog processors — JSON output with timestamps
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper())
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a logger. Use this instead of logging.getLogger."""
    return structlog.get_logger(name)
