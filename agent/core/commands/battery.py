"""GET_BATTERY command — return battery status."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

POWER_TIME_UNLIMITED = -2


async def handle_get_battery(payload: dict) -> dict:
    """Return battery status via psutil."""
    import psutil
    battery = psutil.sensors_battery()
    if battery is None:
        return {
            'has_battery': False,
            'note': 'No battery detected (desktop PC)',
        }

    time_left = None
    if battery.secsleft not in (psutil.POWER_TIME_UNLIMITED, -1, POWER_TIME_UNLIMITED):
        time_left = battery.secsleft

    status = 'charging' if battery.power_plugged else 'discharging'
    logger.debug('Battery: %s%% %s', battery.percent, status)
    return {
        'has_battery': True,
        'percent': round(battery.percent, 1),
        'plugged_in': battery.power_plugged,
        'time_left_seconds': time_left,
        'status': status,
    }