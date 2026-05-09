"""Spawn a fresh agent process so lock / recovery flows can resume the UI quickly."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agent.utils.logger import logger


def relaunch_agent_process() -> None:
    """Start the same agent entrypoint detached from this process (Windows-friendly).

    Task Scheduler only registers ONLOGON, so `os._exit` alone would leave the
    workstation without the lock overlay until the next logon. A detached child
    process restores behaviour within seconds.
    """
    try:
        from agent.utils.startup import get_exe_path

        exe = get_exe_path()
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

        if getattr(sys, 'frozen', False):
            subprocess.Popen(
                [exe],
                creationflags=creationflags,
                close_fds=True,
            )
            logger.info('Relaunched frozen agent exe: %s', exe)
            return

        # Dev / source tree: repo root is parent of the `agent` package
        root = Path(__file__).resolve().parents[2]
        subprocess.Popen(
            [exe, '-m', 'agent.main'],
            cwd=str(root),
            creationflags=creationflags,
            close_fds=True,
        )
        logger.info('Relaunched agent via %s -m agent.main (cwd=%s)', exe, root)
    except Exception as e:
        logger.warning('relaunch_agent_process failed: %s', e)
