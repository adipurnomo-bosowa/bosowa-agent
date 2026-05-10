"""Remote command dispatcher for Bosowa Agent."""
from __future__ import annotations

from agent.utils.logger import logger

from .screenshot import handle_screenshot
from .processes import handle_get_processes, handle_kill_process
from .network import handle_get_network_info
from .usb_control import handle_usb_control, handle_usb_status
from .system_control import (
    handle_lock_screen,
    handle_shutdown,
    handle_restart,
    handle_uninstall_agent,
)
from .battery import handle_get_battery
from .software import handle_get_software
from .software_install import handle_install_software
from .hardware_info import handle_get_hardware_info
from .website_control import (
    handle_block_website,
    handle_unblock_website,
    handle_get_blocked_sites,
)

# logger imported from agent.utils.logger above

COMMAND_HANDLERS = {
    'SCREENSHOT': handle_screenshot,
    'GET_PROCESSES': handle_get_processes,
    'KILL_PROCESS': handle_kill_process,
    'GET_NETWORK_INFO': handle_get_network_info,
    'USB_CONTROL': handle_usb_control,
    'USB_STATUS': handle_usb_status,
    'LOCK_SCREEN': handle_lock_screen,
    'SHUTDOWN': handle_shutdown,
    'RESTART': handle_restart,
    'UNINSTALL_AGENT': handle_uninstall_agent,
    'GET_BATTERY': handle_get_battery,
    'GET_SOFTWARE': handle_get_software,
    'INSTALL_SOFTWARE': handle_install_software,
    'GET_HARDWARE_INFO': handle_get_hardware_info,
    'BLOCK_WEBSITE': handle_block_website,
    'UNBLOCK_WEBSITE': handle_unblock_website,
    'GET_BLOCKED_SITES': handle_get_blocked_sites,
}


async def dispatch_command(
    command_type: str,
    payload: dict,
    command_id: str,
) -> dict:
    """Route a command to its handler and return the result dict."""
    handler = COMMAND_HANDLERS.get(command_type)
    if not handler:
        return {'success': False, 'error': f'Unknown command: {command_type}'}
    try:
        result = await handler(payload)
        return {'success': True, 'data': result}
    except PermissionError as e:
        logger.warning('Command %s permission error: %s', command_type, e)
        return {'success': False, 'error': str(e)}
    except ValueError as e:
        logger.warning('Command %s bad input: %s', command_type, e)
        return {'success': False, 'error': str(e)}
    except Exception as e:
        logger.error('Command %s failed: %s', command_type, e)
        return {'success': False, 'error': str(e)}


__all__ = ['dispatch_command', 'COMMAND_HANDLERS']