"""USB_CONTROL command — enable/disable USB mass storage via registry."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

USB_REGISTRY_PATH = r'SYSTEM\CurrentControlSet\Services\USBSTOR'


async def handle_usb_control(payload: dict) -> dict:
    """Enable or disable USB mass storage via HKLM registry.

    Requires elevated (admin) privileges.
    action: 'enable' | 'disable'
    """
    import winreg

    action = payload.get('action')
    if action not in ('enable', 'disable'):
        raise ValueError("action must be 'enable' or 'disable'")

    start_value = 4 if action == 'disable' else 3

    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            USB_REGISTRY_PATH,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_READ,
        )
        winreg.SetValueEx(key, 'Start', 0, winreg.REG_DWORD, start_value)
        winreg.CloseKey(key)
    except PermissionError:
        raise PermissionError('Admin privileges required to modify USB storage settings')
    except FileNotFoundError:
        raise RuntimeError('USBSTOR registry key not found — not a Windows system?')

    # Verify
    key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        USB_REGISTRY_PATH,
        0,
        winreg.KEY_READ,
    )
    current, _ = winreg.QueryValueEx(key, 'Start')
    winreg.CloseKey(key)

    applied = current == start_value
    logger.info('USB storage %s (registry Start=%d, applied=%s)', action, current, applied)
    return {
        'action': action,
        'registry_value': current,
        'applied': applied,
        'note': 'Restart may be required for changes to take full effect',
    }


def get_usb_locked_sync() -> bool | None:
    """Return True if USB mass storage is locked (Start=4), False if enabled, None on error."""
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            USB_REGISTRY_PATH,
            0,
            winreg.KEY_READ,
        )
        val, _ = winreg.QueryValueEx(key, 'Start')
        winreg.CloseKey(key)
        return val == 4
    except Exception:
        return None


def set_usb_enabled_sync() -> bool:
    """Enable USB mass storage (Start=3). Returns True on success."""
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            USB_REGISTRY_PATH,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_READ,
        )
        winreg.SetValueEx(key, 'Start', 0, winreg.REG_DWORD, 3)
        winreg.CloseKey(key)
        logger.info('USB storage enabled via PIN unlock')
        return True
    except Exception as e:
        logger.warning('set_usb_enabled_sync failed: %s', e)
        return False


async def get_usb_status() -> dict:
    """Return current USB storage state."""
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            USB_REGISTRY_PATH,
            0,
            winreg.KEY_READ,
        )
        val, _ = winreg.QueryValueEx(key, 'Start')
        winreg.CloseKey(key)
        return {'enabled': val != 4, 'registry_value': val}
    except Exception as e:
        return {'enabled': None, 'error': str(e)}


async def handle_usb_status(payload: dict) -> dict:
    """Query current USB storage status from registry."""
    return await get_usb_status()
