"""Bosowa Agent – entry point."""
from __future__ import annotations

import asyncio
import ctypes
import subprocess
import sys
import os
import atexit
from pathlib import Path

_single_instance_mutex = None  # Keep reference so GC doesn't release it

def _ensure_single_instance() -> bool:
    """Create a named Windows mutex. Returns False if another instance is running."""
    global _single_instance_mutex
    if sys.platform != 'win32':
        return True
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, True, 'BosowAgent_SingleInstance')
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
        _single_instance_mutex = handle
        return True
    except Exception:
        return True  # fail open

from agent import config
from agent.auth.login import AuthTokens
from agent.auth.token_store import store_device_token, store_refresh_token
from agent.core.agent_service import AgentService
from agent.overlay.lockscreen import LockScreenOverlay, OverlayConfig
from agent.ui.tray_app import AgentTrayApp
from agent.utils.logger import logger, setup_logger
from agent.utils.startup import register_all, is_registered

_tray: AgentTrayApp | None = None
_service_loop: asyncio.AbstractEventLoop | None = None
_stop_event: asyncio.Event | None = None

WATCHDOG_PID_FILE = config.AGENT_DIR / 'watchdog.pid'


def _spawn_watchdog() -> None:
    """Launch a detached watchdog process that will restart us if we die."""
    try:
        from agent.utils.startup import get_exe_path
        exe = get_exe_path()
        my_pid = os.getpid()
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

        if getattr(sys, 'frozen', False):
            cmd = [exe, '--watchdog', str(my_pid)]
        else:
            root = Path(__file__).resolve().parents[2]
            cmd = [exe, '-m', 'agent.__main__', '--watchdog', str(my_pid)]

        subprocess.Popen(cmd, creationflags=creationflags, close_fds=True)
        logger.info('Watchdog spawned (monitoring PID %d)', my_pid)
    except Exception as e:
        logger.warning('Failed to spawn watchdog: %s', e)


def main() -> None:
    if not _ensure_single_instance():
        # Another instance is already running — exit silently
        sys.exit(0)

    setup_logger('BosowAgent')
    logger.info('=' * 60)
    logger.info('Bosowa Agent v%s starting', config.AGENT_VERSION)
    logger.info('=' * 60)

    # Ensure directories exist
    config.AGENT_DIR.mkdir(parents=True, exist_ok=True)
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Register startup if not already registered
    if not is_registered():
        register_all()
    else:
        logger.debug('Agent already registered for startup')

    # Apply filesystem ACL protection (deny Users write/delete on agent dirs)
    try:
        from agent.utils.file_acl import protect_agent_directories
        protect_agent_directories()
    except Exception as e:
        logger.debug('file_acl protection skipped: %s', e)

    # Spawn watchdog companion process
    _spawn_watchdog()

    if not _try_auto_login():
        _run_auth_flow()


def _try_auto_login() -> bool:
    """Return True if session restore succeeded (skips lock screen overlay)."""
    from agent.auth.token_store import get_user_session
    from agent.auth.login import append_login_log

    token = _try_restore_session()
    if not token:
        # Attempt refresh
        try:
            from agent.auth.login import check_and_refresh_token
            token = check_and_refresh_token()
        except Exception:
            pass

    if not token:
        return False

    user = get_user_session()
    if not user:
        return False

    logger.info('Auto-login: restored session for user=%s', user.get('email', '?'))
    append_login_log(user.get('email', ''), user.get('name', ''), 'AUTO', 'restore', 'OK')
    _start_tray(user)
    _run_agent_service(AuthTokens(token=token, refresh_token=None, user=user))
    return True


def _try_restore_session() -> str | None:
    from agent.auth.token_store import get_device_token, get_device_token_expiry
    from datetime import datetime, timezone, timedelta

    token = get_device_token()
    if not token:
        return None

    expiry = get_device_token_expiry()
    if expiry:
        now = datetime.now(timezone.utc)
        if now < expiry:
            return token
        logger.info('Stored token expired, requiring re-auth')
    else:
        # No expiry stored – assume valid
        return token

    return None


def _run_auth_flow() -> None:
    """Show lock screen and run agent after successful authentication."""
    result: dict = {}

    def on_authenticated(token: str, refresh_token: str, user: dict) -> None:
        result['token'] = token
        result['refresh'] = refresh_token
        result['user'] = user

    def on_web_login_start(session_code: str) -> None:
        logger.info('Web login session code: %s', session_code)
        # TODO: notify server of session_code for web login flow

    overlay_cfg = OverlayConfig(
        on_authenticated=on_authenticated,
        on_web_login_start=on_web_login_start,
    )

    overlay = LockScreenOverlay(overlay_cfg)
    overlay.show()

    # Block main thread until overlay completes auth
    while not result:
        import time
        time.sleep(0.1)

    token = result.get('token', '')
    refresh = result.get('refresh')
    user = result.get('user', {})

    if token and token != '_pin_auth_':
        store_device_token(token)
        if refresh:
            store_refresh_token(refresh)

    # Close overlay and continue
    overlay.wait_until_closed()

    logger.info('Authentication complete for user=%s', user.get('name', '?'))
    _start_tray(user)
    _run_agent_service(AuthTokens(token=token, refresh_token=refresh, user=user))


def _start_tray(user: dict) -> None:
    global _tray
    if _tray is not None:
        return
    try:
        _tray = AgentTrayApp(user=user, stop_callback=request_stop)
        _tray.start()
        logger.info('System tray started')
    except Exception as e:
        logger.warning('Failed to start system tray: %s', e)

def request_stop() -> None:
    global _service_loop, _stop_event
    loop = _service_loop
    ev = _stop_event
    if not loop or not ev:
        return
    try:
        loop.call_soon_threadsafe(ev.set)
    except Exception as e:
        logger.warning('request_stop failed: %s', e)


def _run_agent_service(tokens: AuthTokens) -> None:
    """Run the agent service loop."""
    logger.info('_run_agent_service called for user=%s', tokens.user.get('name', '?'))
    async def _run():
        global _service_loop, _stop_event
        _service_loop = asyncio.get_running_loop()
        _stop_event = asyncio.Event()
        service = AgentService(tokens)
        try:
            logger.info('Starting AgentService...')
            await service.start()
            logger.info('AgentService.start() returned — entering block loop')
            # Block until requested stop from tray
            await _stop_event.wait()
        except asyncio.CancelledError:
            logger.info('AgentService CancelledError')
            pass
        except Exception as e:
            logger.error('AgentService exception: %s', e)
        finally:
            logger.info('Stopping AgentService...')
            await service.stop()
            if _tray:
                _tray.stop()
            logger.info('AgentService stopped')

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info('Interrupted – exiting')


if __name__ == '__main__':
    main()