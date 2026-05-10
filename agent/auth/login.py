"""Login orchestration – direct + web-signal flows."""
from __future__ import annotations

import asyncio
import json
import threading
import uuid

import requests
import certifi

from agent import config
from agent.auth.token_store import (
    store_device_token,
    store_refresh_token,
    get_device_token,
    get_refresh_token,
    store_session_code,
    clear_all_credentials,
    get_device_token_expiry,
)
from agent.core.hardware import get_mac_address
from agent.core.socket_client import AgentSocketClient
from agent.utils.logger import logger


# ---------------------------------------------------------------------------
# Login error messages (portal should return JSON with `code` or `message`)
# ---------------------------------------------------------------------------

USER_DISABLED_CODES = frozenset({'USER_DISABLED', 'ACCOUNT_DISABLED'})


def message_for_agent_login_failure(status_code: int, response: requests.Response | None) -> str | None:
    """Map API error to a short Indonesian message, or None for generic handling."""
    if response is None:
        return None
    try:
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    code = str(data.get('code') or data.get('error') or '').upper()
    if code in USER_DISABLED_CODES:
        return 'Akun dinonaktifkan. Hubungi IT.'
    if code == 'DEVICE_LOCKED':
        return str(data.get('error') or 'Perangkat ini terkunci oleh IT Admin. Hubungi IT Support.')
    msg = str(data.get('message') or data.get('error') or '')
    lower = msg.lower()
    if 'disabled' in lower and ('user' in lower or 'account' in lower or 'akun' in lower):
        return 'Akun dinonaktifkan. Hubungi IT.'
    if status_code == 403 and ('nonaktif' in lower or 'dinonaktifkan' in lower):
        return 'Akun dinonaktifkan. Hubungi IT.'
    if status_code == 403 and ('terkunci' in lower or 'lock' in lower):
        return msg or 'Perangkat ini terkunci oleh IT Admin.'
    return None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class AuthTokens:
    token: str
    refresh_token: str | None
    user: dict

    def __init__(self, token: str, refresh_token: str | None, user: dict):
        self.token = token
        self.refresh_token = refresh_token
        self.user = user


# ---------------------------------------------------------------------------
# Direct login
# ---------------------------------------------------------------------------

def direct_login(email: str, password: str) -> AuthTokens | None:
    """Authenticate via API, return tokens + user or None on failure."""
    import platform
    try:
        resp = requests.post(
            f'{config.API_BASE}/auth/agent-login',
            json={
                'email': email,
                'password': password,
                'device_mac': get_mac_address(),
                'hostname': platform.node(),
            },
            timeout=config.HTTP_TIMEOUT,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        data = resp.json()

        token = data['token']
        refresh_token = data.get('refresh_token')
        user = data.get('user', {})

        # Persist securely
        store_device_token(token)
        if refresh_token:
            store_refresh_token(refresh_token)

        logger.info('Direct login succeeded for user=%s', user.get('name', email))
        return AuthTokens(token, refresh_token, user)

    except requests.exceptions.ConnectionError:
        logger.warning('Server unreachable for direct login')
        return None
    except requests.HTTPError as e:
        resp = e.response
        code = resp.status_code if resp else '-'
        extra = ''
        if resp is not None:
            hint = message_for_agent_login_failure(resp.status_code, resp)
            if hint:
                extra = f' ({hint})'
        logger.warning('Direct login HTTP error %s: %s%s', code, e, extra)
        return None
    except Exception as e:
        logger.error('Direct login failed: %s', e)
        return None


# ---------------------------------------------------------------------------
# Session code (web login)
# ---------------------------------------------------------------------------

def generate_session_code() -> str:
    """Generate a one-time session code for browser-based login."""
    code = str(uuid.uuid4())[:8].upper()
    store_session_code(code)
    return code


async def wait_for_web_login(
    socket_client: AgentSocketClient,
    session_code: str,
    timeout: int = 300,
) -> tuple[str, dict] | None:
    """Wait for the server to emit unlock_device with the session code.

    Returns (token, user) on success, None on timeout.
    """
    event_name = f'unlock_device:{session_code}'
    result = None

    async def _handler(data: dict):
        nonlocal result
        result = (data.get('token', ''), data.get('user', {}))

    socket_client.sio.on(event_name, _handler)

    try:
        await asyncio.wait_for(asyncio.Event().wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning('Web login timeout for session_code=%s', session_code)
    finally:
        socket_client.sio.off(event_name)

    return result


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

def refresh_token_action(refresh_token: str) -> str | None:
    """Send refresh request. Returns new device_token or None."""
    try:
        resp = requests.post(
            f'{config.API_BASE}/auth/refresh',
            json={'refresh_token': refresh_token},
            timeout=config.HTTP_TIMEOUT,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        data = resp.json()
        new_token = data['token']
        store_device_token(new_token)
        new_refresh = data.get('refresh_token')
        if new_refresh:
            store_refresh_token(new_refresh)
        logger.info('Token refreshed successfully')
        return new_token
    except requests.HTTPError as e:
        resp = e.response
        if resp is not None and resp.status_code in (401, 403):
            logger.warning(
                'Token refresh rejected (%s); clearing stored credentials (user may be disabled or session revoked)',
                resp.status_code,
            )
            clear_all_credentials()
        else:
            logger.warning('Token refresh failed: %s', e)
        return None
    except Exception as e:
        logger.warning('Token refresh failed: %s', e)
        return None


def check_and_refresh_token() -> str | None:
    """Check if stored token is expired/near-expiry and refresh if needed."""
    token = get_device_token()
    if not token:
        return None

    expiry = get_device_token_expiry()
    if expiry:
        from datetime import datetime, timezone, timedelta
        # Refresh if within 8 minutes of expiry (tokens are 15 min, check every 4 min)
        if datetime.now(timezone.utc) + timedelta(minutes=8) < expiry:
            return token  # still valid

    refresh = get_refresh_token()
    if not refresh:
        return None

    new_token = refresh_token_action(refresh)
    return new_token or token  # return old token if refresh failed


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

def logout() -> None:
    """Wipe all stored credentials."""
    clear_all_credentials()
    logger.info('Agent logged out, credentials cleared')