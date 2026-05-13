"""Heartbeat sender – runs every 30 seconds while agent is active."""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timezone

import psutil

from agent import config
from agent.core.geo import fetch_ip_location, get_cached_location
from agent.core.socket_client import AgentSocketClient
from agent.core.software_compliance import get_installed_programs
from agent.utils.logger import logger


async def heartbeat_loop(socket_client: AgentSocketClient, token: str) -> None:
    """Periodically emit heartbeat events via Socket.IO."""
    static_ctx = _get_static_context()
    last_geo_refresh = 0.0
    _SW_REFRESH_INTERVAL = 600  # 10 minutes
    cached_sw_list: list[str] = []
    last_sw_refresh: float = 0.0
    while True:
        try:
            now_ts = datetime.now(timezone.utc).timestamp()
            now_mono = _time.monotonic()
            if now_mono - last_sw_refresh >= _SW_REFRESH_INTERVAL:
                cached_sw_list = await asyncio.to_thread(get_installed_programs)
                last_sw_refresh = now_mono
            vm = psutil.virtual_memory()
            payload = {
                'token': token,
                'mac_address': static_ctx['mac_address'],
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'cpu_percent': psutil.cpu_percent(interval=None),
                'ram_percent': vm.percent,
                'ram_used_gb': round(vm.used / (1024**3), 2),
                'ram_total_gb': round(vm.total / (1024**3), 2),
                'disk_percent': _get_primary_disk_usage(),
                'ip_address': static_ctx['ip_address'],
            }
            payload['softwareList'] = cached_sw_list[:200]  # cap at 200 entries
            # Refresh IP geolocation at most every 10 minutes; otherwise reuse cache
            location = get_cached_location()
            if location is None or (now_ts - last_geo_refresh) >= 600:
                fresh = await asyncio.to_thread(fetch_ip_location)
                if fresh:
                    location = fresh
                last_geo_refresh = now_ts
            if location:
                payload['location'] = location
            await socket_client.emit_heartbeat(payload)
            logger.debug('Heartbeat sent: cpu=%.1f%% ram=%.1f%%', payload['cpu_percent'], payload['ram_percent'])
        except Exception as e:
            logger.warning('Heartbeat failed: %s', e)
        await asyncio.sleep(config.HEARTBEAT_INTERVAL)


def _get_primary_disk_usage() -> float:
    """Return disk usage % of the system drive."""
    try:
        import os
        drive = os.environ.get('SystemDrive', 'C:')
        return psutil.disk_usage(drive).percent
    except Exception:
        return 0.0


def _get_primary_ip() -> str:
    """Return the primary IP of this machine."""
    try:
        import socket
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return '127.0.0.1'


def _get_mac_address() -> str:
    """Return the MAC address of this machine (same format as socket_client)."""
    try:
        import uuid
        mac = uuid.getnode()
        return ':'.join(f'{(mac >> i) & 0xFF:02X}' for i in range(0, 48, 8))
    except Exception:
        return '00:00:00:00:00:00'


def _get_static_context() -> dict:
    return {
        'mac_address': _get_mac_address(),
        'ip_address': _get_primary_ip(),
    }