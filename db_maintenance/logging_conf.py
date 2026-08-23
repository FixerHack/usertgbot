"""Logging for db_maintenance goes to a dedicated file, never silently to stdout."""

import logging
from logging.handlers import RotatingFileHandler

from db_maintenance.config import settings


def setup_logging() -> logging.Logger:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("db_maintenance")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            settings.log_dir / "db_maintenance.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    return logger
