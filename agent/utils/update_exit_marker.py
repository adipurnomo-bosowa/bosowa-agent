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

MARKER = config.AGENT_DIR / '.update_replace_pending'
MUTEX_NAME = 'BosowAgent_SingleInstance'


def write_update_replace_marker() -> None:
    try:
        config.AGENT_DIR.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(str(time.time()), encoding='utf-8')
    except Exception:
        pass


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
        return age >= 0 and age < max_age_sec
    except OSError:
        return False


def another_agent_mutex_exists() -> bool:
    """True if some BosowAgent process already holds the single-instance mutex."""
    if sys.platform != 'win32':
        return False
    try:
        SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False
