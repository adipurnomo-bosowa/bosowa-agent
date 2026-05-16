"""Logging setup with rotating file handler."""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from agent import config


def setup_logger(name: str = 'BosowAgent') -> logging.Logger:
    """Return a logger that writes to both a rotating file and stderr."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = logging.DEBUG if config.DEV_MODE else logging.INFO
    logger.setLevel(level)

    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # File handler – rotating, max 10 MB, keep 3 backups
    try:
        log_path = config.LOG_DIR / 'agent.log'
        fh = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding='utf-8',
        )
    except Exception as e:
        # Fallback to a user‑writable directory (e.g., %USERPROFILE%/.bosowa_agent/logs)
        fallback_dir = Path.home() / '.bosowa_agent' / 'logs'
        fallback_dir.mkdir(parents=True, exist_ok=True)
        log_path = fallback_dir / 'agent.log'
        fh = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding='utf-8',
        )
        logger.warning('Failed to open log file %s: %s. Using fallback %s', config.LOG_DIR / 'agent.log', e, log_path)
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Prevent double-logging when this is a child logger (e.g. BosowAgent.watchdog)
    if '.' in name:
        logger.propagate = False

    # Console handler only in dev mode
    if config.DEV_MODE:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    logger.info('Logger initialised (level=%s)', logging.getLevelName(level))
    return logger


# Convenience logger used throughout the agent
logger = setup_logger()