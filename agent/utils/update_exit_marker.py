"""Self-update exit coordination — avoid watchdog relaunching during binary replace.

When the main process exits immediately after spawning PowerShell to copy the new
exe, the companion watchdog would otherwise treat that as a crash and start another
instance (often the old binary still on disk), causing download/replace loops.
"""
from __future__ import annotations

import ctypes
import sys
import time

from agent import config
from agent.utils.logger import logger

MARKER = config.AGENT_DIR / '.update_replace_pending'
MUTEX_NAME = 'BosowAgent_SingleInstance'
# Enough rights to open the mutex created by the main process (default DACL).
MUTEX_ALL_ACCESS = 0x1F0001


def write_update_replace_marker() -> None:
    try:
        config.AGENT_DIR.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(str(time.time()), encoding='utf-8')
    except Exception as e:
        logger.warning('update marker write failed (watchdog may relaunch during replace): %s', e)


def clear_update_replace_marker() -> None:
    try:
        MARKER.unlink(missing_ok=True)
    except Exception:
        pass


def update_replace_marker_fresh(max_age_sec: float = 240.0) -> bool:
    try:
        if not MARKER.is_file():
            return False
        age = time.time() - MARKER.stat().st_mtime
        # Windows can report mtime slightly ahead of time.time(); allow tiny skew.
        return age > -5.0 and age < max_age_sec
    except OSError:
        return False


def another_agent_mutex_exists() -> bool:
    """True if some BosowAgent process already holds the single-instance mutex."""
    if sys.platform != 'win32':
        return False
    try:
        h = ctypes.windll.kernel32.OpenMutexW(MUTEX_ALL_ACCESS, False, MUTEX_NAME)
        if h and int(h) != 0:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False
