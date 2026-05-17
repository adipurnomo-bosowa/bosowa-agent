"""Configuration for Bosowa Agent.

All mutable values are loaded from environment variables with safe defaults.
Server URL and API keys are loaded from a local encrypted config file
(C:\ProgramData\BosowAgent\config.enc) which is created by the installer.
"""
from __future__ import annotations

import os
import sys
import json
import base64
import hashlib
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROGDATA = Path(os.environ.get('PROGDATA', 'C:/ProgramData'))
AGENT_DIR = PROGDATA / 'BosowAgent'
AGENT_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_FILE = AGENT_DIR / 'tokens.enc'      # encrypted refresh token + PIN
CONFIG_FILE = AGENT_DIR / 'config.enc'   # encrypted server URL + API keys
PIN_FILE = AGENT_DIR / 'pin.enc'          # encrypted PIN hash
POWERON_FILE = AGENT_DIR / 'poweron.json' # backup power-on timestamp
LOG_DIR = AGENT_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Encrypted config loading
# ---------------------------------------------------------------------------

_KEY_ENV = 'BOSOWA_ENC_KEY'  # machine-specific encryption key env var name


def _get_machine_key_32b() -> bytes:
    """Return a stable 32-byte key for this machine."""
    env_key = os.environ.get(_KEY_ENV)
    if env_key:
        try:
            raw = base64.b64decode(env_key)
            if len(raw) == 32:
                return raw
        except Exception:
            # Fall back to derivation below
            pass

    hostname = os.environ.get('COMPUTERNAME', os.environ.get('HOSTNAME', 'default'))
    mac = uuid.getnode()
    digest = hashlib.sha256(f'{hostname}:{mac}'.encode()).digest()  # 32 bytes
    try:
        os.environ[_KEY_ENV] = base64.b64encode(digest).decode()
    except Exception:
        # Non-fatal if env is locked down
        pass
    return digest


def _decrypt_config_bytes(blob: bytes) -> dict | None:
    """Decrypt config.enc (Fernet) to dict. Returns None on failure."""
    try:
        from cryptography.fernet import Fernet
        key32 = _get_machine_key_32b()
        fkey = base64.b64encode(key32)
        plaintext = Fernet(fkey).decrypt(blob)
        return json.loads(plaintext)
    except Exception:
        return None


def _load_installer_config() -> dict:
    """Load installer-written config from CONFIG_FILE (encrypted JSON)."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        blob = CONFIG_FILE.read_bytes()
        data = _decrypt_config_bytes(blob)
        if isinstance(data, dict):
            return data
        # Backward/edge case: plain JSON config
        try:
            plain = json.loads(blob.decode('utf-8'))
            return plain if isinstance(plain, dict) else {}
        except Exception:
            return {}
    except Exception:
        return {}


_INSTALLER_CFG = _load_installer_config()

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
def _pick_server_url() -> str:
    # Priority: explicit env var > installer config > default
    env_url = os.environ.get('BOSOWA_SERVER_URL')
    if env_url:
        return env_url
    for key in ('server_url', 'SERVER_URL', 'url', 'base_url', 'host'):
        val = _INSTALLER_CFG.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return 'https://portal.bosowa.co.id'


SERVER_URL = _pick_server_url().strip().rstrip('/')

# Optional Google Geolocation API key — improves WiFi BSSID accuracy significantly.
# Set google_geo_key in config.enc (installer) or BOSOWA_GOOGLE_GEO_KEY env var.
GOOGLE_GEO_KEY: str = (
    _INSTALLER_CFG.get('google_geo_key')
    or os.environ.get('BOSOWA_GOOGLE_GEO_KEY', '')
)

API_BASE = f'{SERVER_URL}/api'
SOCKET_URL = SERVER_URL  # python-socketio connects to the same host

# ---------------------------------------------------------------------------
# Timeouts & intervals
# ---------------------------------------------------------------------------
HTTP_TIMEOUT = 15           # seconds
HEARTBEAT_INTERVAL = 30      # seconds
TOKEN_REFRESH_INTERVAL = 240   # check token expiry every 4 min (token lifetime is 15 min)
AUDIT_SAMPLE_INTERVAL = 10    # seconds between foreground-app samples (1 count = 10 s of focus)
PIN_VALIDITY_DAYS = 7
PIN_SETUP_TIMEOUT = 10      # seconds for server request in PIN setup

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------
DEV_MODE = os.environ.get('BOSOWA_DEV', '0') == '1'
LOG_LEVEL = 'DEBUG' if DEV_MODE else 'INFO'
AGENT_VERSION = '1.2.9'

# Background silent download/replace from /api/agent/version (off by default).
# Can fight the watchdog if replace fails or server version never matches the
# embedded binary; use portal "Update Agent" instead. Opt-in: BOSOWA_AGENT_SILENT_UPDATE=1
SILENT_AGENT_UPDATE = os.environ.get('BOSOWA_AGENT_SILENT_UPDATE', '').lower() in (
    '1',
    'true',
    'yes',
    'on',
)

# ---------------------------------------------------------------------------
# PyQt5 / overlay
# ---------------------------------------------------------------------------
OVERLAY_LOCK_COLOR = '#0A1628'
OVERLAY_ACCENT = '#1E88E5'
OVERLAY_SUCCESS = '#43A047'
OVERLAY_ERROR = '#E53935'

# ---------------------------------------------------------------------------
# Internal – do not change
# ---------------------------------------------------------------------------
__all__ = [
    'PROGDATA',
    'AGENT_DIR',
    'TOKEN_FILE',
    'CONFIG_FILE',
    'PIN_FILE',
    'POWERON_FILE',
    'LOG_DIR',
    'SERVER_URL',
    'API_BASE',
    'SOCKET_URL',
    'HTTP_TIMEOUT',
    'HEARTBEAT_INTERVAL',
    'TOKEN_REFRESH_INTERVAL',
    'AUDIT_SAMPLE_INTERVAL',
    'PIN_VALIDITY_DAYS',
    'PIN_SETUP_TIMEOUT',
    'DEV_MODE',
    'LOG_LEVEL',
    'AGENT_VERSION',
    'SILENT_AGENT_UPDATE',
    'GOOGLE_GEO_KEY',
    'OVERLAY_LOCK_COLOR',
    'OVERLAY_ACCENT',
    'OVERLAY_SUCCESS',
    'OVERLAY_ERROR',
    '_KEY_ENV',
]