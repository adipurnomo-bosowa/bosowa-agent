"""GET_NETWORK_INFO command — adapter info, stats, connections, DNS."""
from __future__ import annotations

import logging
import socket

logger = logging.getLogger(__name__)


async def handle_get_network_info(payload: dict) -> dict:
    """Return network adapters, stats, active connections, DNS info."""
    import psutil
    import subprocess

    # Adapters with IP addresses + up/down status
    adapters = []
    if_stats = {}
    try:
        if_stats = psutil.net_if_stats()
    except Exception:
        pass
    for iface, addrs in psutil.net_if_addrs().items():
        stats = if_stats.get(iface)
        is_up = bool(stats.isup) if stats else False
        speed_mbps = int(stats.speed) if stats and stats.speed else 0
        adapter = {'name': iface, 'is_up': is_up, 'speed_mbps': speed_mbps, 'addresses': []}
        for addr in addrs:
            if addr.family == socket.AF_INET:
                adapter['addresses'].append({
                    'type': 'IPv4',
                    'address': addr.address,
                    'netmask': getattr(addr, 'netmask', None),
                })
            elif addr.family == socket.AF_INET6:
                adapter['addresses'].append({
                    'type': 'IPv6',
                    'address': addr.address,
                })
        adapters.append(adapter)

    # Network I/O stats
    stats = psutil.net_io_counters()
    net_stats = {
        'bytes_sent': stats.bytes_sent,
        'bytes_recv': stats.bytes_recv,
        'packets_sent': stats.packets_sent,
        'packets_recv': stats.packets_recv,
    }

    # Active TCP connections
    connections = []
    for conn in psutil.net_connections(kind='tcp'):
        if conn.status == 'ESTABLISHED':
            connections.append({
                'local_address': f'{conn.laddr.ip}:{conn.laddr.port}',
                'remote_address': f'{conn.raddr.ip}:{conn.raddr.port}' if conn.raddr else None,
                'status': conn.status,
                'pid': conn.pid,
            })

    # DNS servers via netsh
    try:
        result = subprocess.run(
            ['netsh', 'interface', 'ip', 'show', 'dns'],
            capture_output=True, text=True, timeout=5,
        )
        dns_raw = result.stdout
    except Exception as e:
        logger.warning('netsh dns query failed: %s', e)
        dns_raw = 'Unable to retrieve'

    return {
        'adapters': adapters,
        'stats': net_stats,
        'active_connections': connections[:20],
        'dns_info': dns_raw,
        'hostname': socket.gethostname(),
        'fqdn': socket.getfqdn(),
    }
