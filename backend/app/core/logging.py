"""JSON-structured logging configuration for the backend.

See AGENTS.md section 6.
"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger import jsonlogger

from app.core.config import settings

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str | None = None) -> None:
    """Install a JSON formatter on the root logger.

    Idempotent -- calling this more than once replaces the existing handler
    rather than stacking duplicates.

    Args:
        level: Log level name. Defaults to ``settings.log_level``.
    """
    resolved = (level or settings.log_level).upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter(_LOG_FORMAT, rename_fields={"levelname": "level"})
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(resolved)

    logger.info("Logging configured", extra={"level": resolved})
