import logging
import sys
from pathlib import Path

import structlog

from app.infra.settings import get_settings

# Processors shared between structlog-native loggers and the stdlib
# ProcessorFormatter. Anything that enriches the event dict (timestamps, log
# level, context) lives here so both paths render identically.
_SHARED_PROCESSORS: list[structlog.types.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]


def configure_logging() -> None:
    """Configure structlog to emit JSON through stdlib logging.

    Call once at app startup. Routing structlog through the stdlib logging
    handlers (rather than structlog's own PrintLogger) means a single rendered
    JSON line reaches BOTH stdout and the log file. The file lives on a mounted
    volume so logs survive container restarts.
    """
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper())

    log_dir = Path("/var/log/modir")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "modir.json.log"

    # The formatter renders the structlog event dict to a JSON line. It runs
    # inside stdlib logging, so every handler attached below shares it.
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=_SHARED_PROCESSORS,
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(level)

    # Structlog hands events off to stdlib logging via ProcessorFormatter.
    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a logger. Use this instead of logging.getLogger."""
    return structlog.get_logger(name)
