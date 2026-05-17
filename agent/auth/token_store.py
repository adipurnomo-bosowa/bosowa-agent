"""Secure token storage using keyring + Fernet encryption."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import keyring

from agent import config
from agent.utils.logger import logger

# ---------------------------------------------------------------------------
# Key derivation – machine-specific encryption key
# ---------------------------------------------------------------------------

def _get_encryption_key() -> bytes:
    """Derive a 32-byte AES key from machine identity.

    The key is stored once in an environment variable after first run.
    If missing, we derive it from hostname + MAC and store it.
    """
    env_key = os.environ.get(config._KEY_ENV)
    if env_key:
        import base64
        return base64.b64decode(env_key)

    import uuid as _uuid
    import hashlib
    import base64 as _b64

    # Derive key from persistent hardware identifiers
    mac = _uuid.getnode()
    hostname = os.environ.get('COMPUTERNAME', os.environ.get('HOSTNAME', 'default'))
    raw = f'{hostname}:{mac}'.encode()
    digest = hashlib.sha256(raw).digest()

    # Store for next launch
    encoded = _b64.b64encode(digest).decode()
    os.environ[config._KEY_ENV] = encoded
    logger.debug('Derived new machine encryption key')
    return digest


def _get_fernet():
    """Return a Fernet instance using the machine-specific key."""
    from cryptography.fernet import Fernet
    key = _get_encryption_key()
    if len(key) == 32:
        # Pad to 32 bytes for Fernet (needs base64 of 32-byte key with proper padding)
        import base64 as _b64
        fkey = _b64.b64encode(key)
        return Fernet(fkey)
    return Fernet(key)


# ---------------------------------------------------------------------------
# Keyring helpers (device_token in Windows Credential Manager)
# ---------------------------------------------------------------------------

_KEYRING_SERVICE = 'BosowAgent'
_KEYRING_DEVICE_TOKEN_ATTR = 'device_token'
_KEYRING_EXPIRES_ATTR = 'device_token_expires'


def store_device_token(token: str, expires_at: datetime | None = None) -> None:
    """Store device JWT in Windows Credential Manager via keyring."""
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_DEVICE_TOKEN_ATTR, token)
        if expires_at:
            keyring.set_password(
                _KEYRING_SERVICE,
                _KEYRING_EXPIRES_ATTR,
                expires_at.isoformat()
            )
        logger.debug('Stored device_token in keyring (expires=%s)', expires_at)
    except Exception as e:
        logger.error('Failed to store device_token in keyring: %s', e)


def store_device_token_from_jwt(token: str) -> None:
    """Persist device JWT and expiry from the `exp` claim (drives refresh + auto-login)."""
    import base64
    import json as _json
    from datetime import datetime, timezone
    try:
        payload_b64 = token.split('.')[1]
        payload_b64 += '=' * (4 - len(payload_b64) % 4)
        payload = _json.loads(base64.b64decode(payload_b64))
        exp = payload.get('exp')
        if exp:
            store_device_token(token, datetime.fromtimestamp(int(exp), tz=timezone.utc))
            return
    except Exception as e:
        logger.debug('store_device_token_from_jwt: no exp in token: %s', e)
    store_device_token(token)


def get_device_token() -> str | None:
    """Retrieve device JWT from Windows Credential Manager."""
    try:
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_DEVICE_TOKEN_ATTR)
    except Exception as e:
        logger.warning('Failed to retrieve device_token from keyring: %s', e)
        return None


def get_device_token_expiry() -> datetime | None:
    """Retrieve device token expiry timestamp."""
    try:
        exp_str = keyring.get_password(_KEYRING_SERVICE, _KEYRING_EXPIRES_ATTR)
        if exp_str:
            return datetime.fromisoformat(exp_str).replace(tzinfo=timezone.utc)
    except Exception as e:
        logger.warning('Failed to retrieve token expiry: %s', e)
    return None


def clear_device_token() -> None:
    """Remove device token and expiry from keyring."""
    try:
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_DEVICE_TOKEN_ATTR)
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_EXPIRES_ATTR)
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception as e:
        logger.warning('Failed to clear keyring tokens: %s', e)


# ---------------------------------------------------------------------------
# Encrypted file (refresh_token + PIN hash + session_code secret)
# ---------------------------------------------------------------------------

def _fernet_encrypt(data: dict) -> bytes:
    f = _get_fernet()
    plaintext = json.dumps(data, default=str).encode()
    return f.encrypt(plaintext)


def _fernet_decrypt(blob: bytes) -> dict:
    f = _get_fernet()
    plaintext = f.decrypt(blob)
    return json.loads(plaintext)


# ---- Token file helpers ----------------------------------------------------

def _read_token_file() -> dict:
    """Decrypt and return the token file as a dict, or {} on missing/failure."""
    if not config.TOKEN_FILE.exists():
        return {}
    try:
        blob = config.TOKEN_FILE.read_bytes()
        return _fernet_decrypt(blob)
    except Exception as e:
        logger.error('Failed to read token file: %s', e)
        return {}


def _write_token_file(data: dict) -> None:
    """Encrypt and write dict to TOKEN_FILE, then restrict permissions."""
    try:
        blob = _fernet_encrypt(data)
        config.TOKEN_FILE.write_bytes(blob)
        _restrict_file(config.TOKEN_FILE)
    except Exception as e:
        logger.error('Failed to write token file: %s', e)


# ---- Refresh token ---------------------------------------------------------

def store_refresh_token(refresh_token: str) -> None:
    """Store refresh token encrypted alongside any existing session data."""
    data = _read_token_file()
    data['refresh_token'] = refresh_token
    _write_token_file(data)
    logger.debug('Stored refresh_token in token file')


def get_refresh_token() -> str | None:
    """Read refresh_token from encrypted local file."""
    if not config.TOKEN_FILE.exists():
        return None
    try:
        blob = config.TOKEN_FILE.read_bytes()
        data = _fernet_decrypt(blob)
        return data.get('refresh_token')
    except Exception as e:
        logger.error('Failed to read refresh_token: %s', e)
        return None


def clear_refresh_token() -> None:
    """Remove refresh token file."""
    try:
        if config.TOKEN_FILE.exists():
            config.TOKEN_FILE.unlink()
    except Exception as e:
        logger.warning('Failed to remove token file: %s', e)


# ---- User session ----------------------------------------------------------

def store_user_session(user: dict) -> None:
    """Persist user dict alongside refresh token (encrypted)."""
    data = _read_token_file()
    data['user'] = user
    _write_token_file(data)


def get_user_session() -> dict | None:
    """Return stored user dict, or None if not present."""
    data = _read_token_file()
    user = data.get('user')
    return user if isinstance(user, dict) and user else None


def clear_user_session() -> None:
    """Remove user session from token file, keep other data intact."""
    data = _read_token_file()
    if 'user' not in data:
        return
    data.pop('user')
    _write_token_file(data)


# ---- PIN hash --------------------------------------------------------------

def store_pin_hash(pin_hash: bytes, valid_until: datetime) -> None:
    """Store IT-admin-set PIN hash and expiry."""
    try:
        blob = _fernet_encrypt({
            'pin_hash': pin_hash.decode('latin-1'),
            'valid_until': valid_until.isoformat(),
        })
        config.PIN_FILE.write_bytes(blob)
        _restrict_file(config.PIN_FILE)
        logger.debug('Stored PIN hash (valid until %s)', valid_until)
    except Exception as e:
        logger.error('Failed to store PIN hash: %s', e)


def get_pin_hash_and_expiry() -> tuple[bytes, datetime] | None:
    """Return (pin_hash, valid_until) or None if not set / expired."""
    if not config.PIN_FILE.exists():
        return None
    try:
        blob = config.PIN_FILE.read_bytes()
        data = _fernet_decrypt(blob)
        valid_until = datetime.fromisoformat(data['valid_until']).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now > valid_until:
            logger.info('PIN has expired (was valid until %s)', valid_until)
            return None
        return data['pin_hash'].encode('latin-1'), valid_until
    except Exception as e:
        logger.error('Failed to read PIN hash: %s', e)
        return None


def clear_pin_hash() -> None:
    try:
        if config.PIN_FILE.exists():
            config.PIN_FILE.unlink()
    except Exception as e:
        logger.warning('Failed to remove PIN file: %s', e)


# ---- Session code (ephemeral) -----------------------------------------------

def store_session_code(code: str) -> None:
    """Store session code encrypted alongside any existing session data."""
    data = _read_token_file()
    data['session_code'] = code
    _write_token_file(data)


def get_session_code() -> str | None:
    """Read pending session code, then delete it (one-time use)."""
    if not config.TOKEN_FILE.exists():
        return None
    try:
        blob = config.TOKEN_FILE.read_bytes()
        data = _fernet_decrypt(blob)
        code = data.get('session_code')
        # Consume it immediately
        config.TOKEN_FILE.unlink(missing_ok=True)
        return code
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Clear all credentials
# ---------------------------------------------------------------------------

def clear_all_credentials() -> None:
    """Wipe keyring + encrypted files on logout."""
    clear_device_token()
    clear_user_session()
    clear_refresh_token()
    clear_pin_hash()
    logger.info('All credentials cleared')


# ---------------------------------------------------------------------------
# Lock-screen one-shot message (set when server force-locks the device)
# ---------------------------------------------------------------------------

LOCK_MESSAGE_FILE = config.AGENT_DIR / 'lock_message.txt'


def store_lock_message(message: str) -> None:
    try:
        LOCK_MESSAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCK_MESSAGE_FILE, 'w', encoding='utf-8') as f:
            f.write(message or '')
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except Exception as e:
        logger.warning('store_lock_message failed: %s', e)


def consume_lock_message() -> str | None:
    """Return the lock message once, then delete the file."""
    try:
        if not LOCK_MESSAGE_FILE.exists():
            return None
        msg = LOCK_MESSAGE_FILE.read_text(encoding='utf-8').strip()
        try:
            LOCK_MESSAGE_FILE.unlink()
        except Exception:
            pass
        return msg or None
    except Exception as e:
        logger.warning('consume_lock_message failed: %s', e)
        return None


# ---------------------------------------------------------------------------
# Permission hardening (Windows)
# ---------------------------------------------------------------------------

def _restrict_file(path: 'os.PathLike') -> None:
    """Remove inherited ACLs; grant SYSTEM, Administrators, and the current
    user Full Control.  The current user MUST retain access because the agent
    process runs as that user and needs to read its own token file.
    Without this, icacls strips user access and every read returns EACCES,
    breaking auto-login on the next start (the login-loop bug).
    """
    try:
        import subprocess
        from agent.utils.proc import NO_WINDOW
        username = os.environ.get('USERNAME', '').strip()
        cmd = ['icacls', str(path), '/inheritance:r', '/grant:r',
               'SYSTEM:F', 'Administrators:F']
        if username:
            cmd.append(f'{username}:F')
        result = subprocess.run(cmd, capture_output=True, timeout=5, creationflags=NO_WINDOW)
        if result.returncode != 0:
            logger.warning('icacls failed on %s: %s', path, result.stderr.decode())
    except Exception as e:
        logger.warning('Failed to restrict file permissions on %s: %s', path, e)