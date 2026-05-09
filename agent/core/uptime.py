"""Power-on/off event tracking."""
from __future__ import annotations

import atexit
import json
import signal
import sys
from datetime import datetime, timezone

import requests
import certifi

from agent import config
from agent.core.hardware import get_mac_address
from agent.utils.logger import logger

_power_on_ts: datetime | None = None


def send_power_on(token: str) -> bool:
    """Called when the agent starts (= PC just powered on). Returns True on success."""
    global _power_on_ts
    ts = datetime.now(timezone.utc)
    _power_on_ts = ts

    # Store locally as crash-recovery backup
    _save_poweron_local(ts)

    payload = {
        'device_mac': get_mac_address(),
        'timestamp': ts.isoformat(),
        'hostname': _get_hostname(),
    }
    try:
        resp = requests.post(
            f'{config.API_BASE}/uptime/power-on',
            json=payload,
            headers={'Authorization': f'Bearer {token}'},
            timeout=config.HTTP_TIMEOUT,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        logger.info('Power-on event sent (ts=%s)', ts.isoformat())
        return True
    except requests.exceptions.SSLError as e:
        logger.error('SSL error sending power-on event: %s', e)
        return False
    except requests.exceptions.ConnectionError as e:
        logger.warning('Server unreachable for power-on event: %s', e)
        return False
    except Exception as e:
        logger.error('Failed to send power-on event: %s', e)
        return False


def send_power_off(token: str) -> bool:
    """Called on normal PC shutdown / agent exit. Returns True on success."""
    ts = datetime.now(timezone.utc)
    payload = {
        'device_mac': get_mac_address(),
        'timestamp': ts.isoformat(),
        'hostname': _get_hostname(),
    }
    try:
        resp = requests.post(
            f'{config.API_BASE}/uptime/power-off',
            json=payload,
            headers={'Authorization': f'Bearer {token}'},
            timeout=config.HTTP_TIMEOUT,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        logger.info('Power-off event sent (ts=%s)', ts.isoformat())
        return True
    except requests.exceptions.SSLError as e:
        logger.error('SSL error sending power-off event: %s', e)
        return False
    except requests.exceptions.ConnectionError:
        logger.warning('Server unreachable for power-off event')
        return False
    except Exception as e:
        logger.error('Failed to send power-off event: %s', e)
        return False


# ---------------------------------------------------------------------------
# Local crash-recovery backup
# ---------------------------------------------------------------------------

def _save_poweron_local(ts: datetime) -> None:
    try:
        config.POWERON_FILE.write_text(json.dumps({'power_on': ts.isoformat()}))
    except Exception as e:
        logger.warning('Failed to write power-on backup: %s', e)


def get_last_power_on() -> datetime | None:
    """Read crash-recovery power-on timestamp from local file."""
    try:
        if config.POWERON_FILE.exists():
            data = json.loads(config.POWERON_FILE.read_text())
            return datetime.fromisoformat(data['power_on']).replace(tzinfo=timezone.utc)
    except Exception as e:
        logger.warning('Failed to read power-on backup: %s', e)
    return None


def clear_power_on() -> None:
    try:
        if config.POWERON_FILE.exists():
            config.POWERON_FILE.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Windows shutdown hook (CTRL_SHUTDOWN_EVENT)
# ---------------------------------------------------------------------------

def _get_hostname() -> str:
    import platform
    return platform.node()


def register_shutdown_hooks(token_ref: object) -> None:
    """Register atexit + Windows console control handlers.

    token_ref should be a callable that returns the current valid token,
    or None if no token is available.
    """
    # We'll store the token getter as a module-level reference
    global _token_getter
    _token_getter = token_ref

    atexit.register(_atexit_power_off)

    if sys.platform == 'win32':
        try:
            import win32api
            import win32con
            import pywintypes

            def win32_handler(event):
                if event in (win32con.CTRL_SHUTDOWN_EVENT, win32con.CTRL_CLOSE_EVENT):
                    _safe_power_off()
                    return 1
                return 0

            win32api.SetConsoleCtrlHandler(win32_handler, True)
            logger.debug('Windows shutdown handler registered')
        except ImportError:
            logger.warning('pywin32 not available – Windows shutdown hook not registered')
        except Exception as e:
            logger.warning('Failed to register Windows shutdown handler: %s', e)


# Token getter injected by agent_service
_token_getter: callable | None = None


def _atexit_power_off() -> None:
    _safe_power_off()


def _safe_power_off() -> None:
    global _token_getter
    if _token_getter:
        token = _token_getter()
    else:
        token = None
    if token:
        send_power_off(token)
    else:
        logger.debug('No token available for power-off event')