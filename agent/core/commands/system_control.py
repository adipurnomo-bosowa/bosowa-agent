"""LOCK_SCREEN, SHUTDOWN, RESTART, UNINSTALL_AGENT commands."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from agent import config

logger = logging.getLogger(__name__)


async def handle_lock_screen(payload: dict) -> dict:
    """Lock the Windows workstation."""
    import ctypes
    ctypes.windll.user32.LockWorkStation()
    logger.info('Workstation locked')
    return {'action': 'lock', 'executed': True}


async def handle_shutdown(payload: dict) -> dict:
    """Shut down the PC after optional delay."""
    import subprocess
    delay = payload.get('delay_seconds', 30)
    message = payload.get('message', 'PC ini akan dimatikan oleh IT Admin Bosowa.')
    subprocess.run(
        ['shutdown', '/s', '/t', str(delay), '/c', message, '/f'],
        capture_output=True,
    )
    logger.info('Shutdown scheduled in %ds', delay)
    return {'action': 'shutdown', 'delay_seconds': delay, 'message': message}


async def handle_restart(payload: dict) -> dict:
    """Restart the PC after optional delay."""
    import subprocess
    delay = payload.get('delay_seconds', 30)
    message = payload.get('message', 'PC ini akan di-restart oleh IT Admin Bosowa.')
    subprocess.run(
        ['shutdown', '/r', '/t', str(delay), '/c', message, '/f'],
        capture_output=True,
    )
    logger.info('Restart scheduled in %ds', delay)
    return {'action': 'restart', 'delay_seconds': delay, 'message': message}


def _schedule_delete_executable() -> None:
    """After process exit, remove the frozen .exe (Windows). Dev mode: no-op."""
    if not getattr(sys, 'frozen', False):
        logger.info('UNINSTALL_AGENT: not frozen — skipping self-delete of exe')
        return
    exe = Path(sys.executable)
    agent_dir = str(config.AGENT_DIR)
    try:
        bat = Path(tempfile.gettempdir()) / 'bosowa_agent_uninstall_del.bat'
        bat.write_text(
            '@echo off\r\n'
            'ping 127.0.0.1 -n 3 > nul\r\n'
            f'del /f /q "{exe}"\r\n'
            f'rd /s /q "{agent_dir}"\r\n'
            'del /f /q "%~f0"\r\n',
            encoding='utf-8',
        )
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        subprocess.Popen(
            ['cmd.exe', '/c', str(bat)],
            cwd=str(tempfile.gettempdir()),
            creationflags=flags,
            close_fds=True,
        )
        logger.info('UNINSTALL_AGENT: scheduled deletion of %s and %s', exe, agent_dir)
    except Exception as e:
        logger.warning('UNINSTALL_AGENT: could not schedule exe delete: %s', e)


async def handle_uninstall_agent(payload: dict) -> dict:
    """Remove persistence, credentials, local config; exit and delete frozen exe."""
    from agent.auth.token_store import clear_all_credentials
    from agent.utils.startup import unregister_startup, unregister_task_scheduler

    unregister_startup()
    unregister_task_scheduler()
    clear_all_credentials()
    for p in (config.CONFIG_FILE, config.PIN_FILE, config.TOKEN_FILE, config.POWERON_FILE):
        try:
            p.unlink(missing_ok=True)
        except Exception as e:
            logger.debug('UNINSTALL_AGENT: remove %s: %s', p, e)

    def _exit_after_ack() -> None:
        _schedule_delete_executable()
        os._exit(0)

    loop = asyncio.get_running_loop()
    loop.call_later(1.5, _exit_after_ack)
    logger.warning('UNINSTALL_AGENT initiated — process will exit shortly')
    return {'action': 'uninstall', 'initiated': True}
