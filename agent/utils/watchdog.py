"""Watchdog process — monitors BosowAgent main process and restarts it if killed.

Usage (same binary, different mode):
    BosowAgent.exe --watchdog <main_pid>

The watchdog:
  1. Monitors the given PID (main agent process).
  2. If the process disappears, waits RESTART_DELAY_SECS then relaunches the agent.
  3. Writes its own PID to WATCHDOG_PID_FILE so the main agent can verify it's alive.
"""
from __future__ import annotations

import os
import sys
import time
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger('BosowAgent.watchdog')

POLL_INTERVAL_SECS = 15
RESTART_DELAY_SECS = 5
MAX_RESTART_ATTEMPTS = 10  # guard against crash-loop
RESTART_WINDOW_SECS = 300  # reset counter after 5 min stable run


def _is_pid_running(pid: int) -> bool:
    """Return True if a process with the given PID is running."""
    if sys.platform != 'win32':
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        import psutil
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _relaunch_agent() -> None:
    """Restart the main agent as a detached process."""
    try:
        from agent.utils.startup import get_exe_path
        exe = get_exe_path()
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

        if getattr(sys, 'frozen', False):
            subprocess.Popen([exe], creationflags=creationflags, close_fds=True)
            logger.info('Watchdog: relaunched frozen agent %s', exe)
        else:
            root = Path(__file__).resolve().parents[2]
            subprocess.Popen(
                [exe, '-m', 'agent.main'],
                cwd=str(root),
                creationflags=creationflags,
                close_fds=True,
            )
            logger.info('Watchdog: relaunched agent via %s -m agent.main', exe)
    except Exception as e:
        logger.error('Watchdog: relaunch failed: %s', e)


def _write_watchdog_pid(pid_file: Path) -> None:
    try:
        pid_file.write_text(str(os.getpid()))
    except Exception as e:
        logger.warning('Watchdog: could not write PID file %s: %s', pid_file, e)


def run_watchdog(main_pid: int, pid_file: Path) -> None:
    """Blocking watchdog loop. Call from __main__ after parsing --watchdog arg."""
    from agent.utils.logger import setup_logger
    setup_logger('BosowAgent.watchdog')
    logger.info('Watchdog started, monitoring PID %d', main_pid)
    _write_watchdog_pid(pid_file)

    restart_count = 0
    last_restart_time = 0.0

    while True:
        time.sleep(POLL_INTERVAL_SECS)

        if _is_pid_running(main_pid):
            # Agent is alive — reset restart counter if stable for RESTART_WINDOW_SECS
            if restart_count > 0 and time.time() - last_restart_time > RESTART_WINDOW_SECS:
                restart_count = 0
            continue

        logger.warning('Watchdog: main agent PID %d not found — will restart', main_pid)

        if restart_count >= MAX_RESTART_ATTEMPTS:
            logger.error(
                'Watchdog: %d restart attempts exhausted, giving up', MAX_RESTART_ATTEMPTS
            )
            break

        time.sleep(RESTART_DELAY_SECS)
        _relaunch_agent()
        restart_count += 1
        last_restart_time = time.time()
        # After relaunch, we don't track the new PID — the new process will
        # spawn its own watchdog. Our job here is done.
        break

    logger.info('Watchdog exiting')
    try:
        pid_file.unlink(missing_ok=True)
    except Exception:
        pass
