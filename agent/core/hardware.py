"""Platform and OS detection utilities."""
import platform as _platform
import uuid
import socket
import logging

try:
    import wmi
    _WMI_AVAILABLE = True
except ImportError:
    _WMI_AVAILABLE = False
    wmi = None

logger = logging.getLogger(__name__)


def get_mac_address() -> str:
    """Return primary NIC MAC as AA:BB:CC:DD:EE:FF."""
    mac = uuid.getnode()
    return ':'.join(f'{(mac >> i) & 0xFF:02X}' for i in range(0, 48, 8))


def get_primary_ip() -> str:
    """Return the primary IP address of this machine."""
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return '127.0.0.1'


def get_cpu_name() -> str:
    """Return CPU name via WMI."""
    if not _WMI_AVAILABLE:
        return _platform.processor() or 'Unknown'
    try:
        return wmi.WMI().Win32_Processor()[0].Name
    except Exception as e:
        logger.warning(f"Failed to get CPU name via WMI: {e}")
        return _platform.processor() or 'Unknown'


def get_gpu_info() -> str | None:
    """Return GPU name via WMI, or None if unavailable."""
    if not _WMI_AVAILABLE:
        return None
    try:
        controllers = wmi.WMI().Win32_VideoController()
        if controllers:
            return controllers[0].Name
    except Exception as e:
        logger.warning(f"Failed to get GPU info via WMI: {e}")
    return None


def get_disk_info() -> list[dict]:
    """Return list of fixed disk info."""
    import psutil  # noqa: E402
    disks = []
    for part in psutil.disk_partitions():
        if part.device.startswith('\\\\?\\') or part.fstype in ('', 'tmpfs', 'devpts', 'cdfs'):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                'drive': part.device,
                'mountpoint': part.mountpoint,
                'fstype': part.fstype,
                'total_gb': round(usage.total / (1024 ** 3), 2),
                'used_gb': round(usage.used / (1024 ** 3), 2),
                'free_gb': round(usage.free / (1024 ** 3), 2),
                'percent': usage.percent,
            })
        except PermissionError:
            continue
        except Exception as e:
            logger.warning(f"Failed to get disk info for {part.device}: {e}")
    return disks


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------

def get_battery_info() -> dict:
    """Return battery status, or {'has_battery': False} if none."""
    import psutil  # noqa: E402
    battery = psutil.sensors_battery()
    if battery is None:
        return {'has_battery': False}
    return {
        'has_battery': True,
        'percent': round(battery.percent, 1),
        'plugged_in': battery.power_plugged,
    }


# ---------------------------------------------------------------------------
# Network adapters
# ---------------------------------------------------------------------------

def get_network_adapters() -> list[dict]:
    """Return list of active network adapters with IPv4/IPv6 addresses."""
    import psutil  # noqa: E402
    import socket as _socket
    adapters = []
    for iface, addrs in psutil.net_if_addrs().items():
        adapter = {'name': iface, 'addresses': []}
        for addr in addrs:
            if addr.family == _socket.AF_INET:
                adapter['addresses'].append({
                    'type': 'IPv4',
                    'address': addr.address,
                    'netmask': getattr(addr, 'netmask', None),
                })
            elif addr.family == _socket.AF_INET6:
                adapter['addresses'].append({
                    'type': 'IPv6',
                    'address': addr.address,
                })
        if adapter['addresses']:
            adapters.append(adapter)
    return adapters


# ---------------------------------------------------------------------------
# OS info
# ---------------------------------------------------------------------------

def get_hostname() -> str:
    return _platform.node()


def get_os_info() -> str:
    """Return OS name. Handles Windows 10/11 correctly."""
    if _platform.system() != 'Windows':
        return f"{_platform.system()}-{_platform.release()}"
    # platform.release() returns "10" for both Win10 and Win11.
    # Differentiate by build number: Win11 starts at build 22000.
    try:
        ver_str = _platform.version()
        build = int(ver_str.split('.')[-1]) if ver_str else 0
        if build >= 22000:
            return 'Windows-11'
    except (ValueError, IndexError):
        pass
    return 'Windows-10'


# ---------------------------------------------------------------------------
# Full hardware snapshot
# ---------------------------------------------------------------------------

def get_hardware_snapshot() -> dict:
    """Return a full hardware snapshot dict for device registration."""
    import psutil  # noqa: E402
    from datetime import datetime, timezone
    # Best-effort IP geolocation — keeps the dashboard map populated even if the
    # WebSocket register/heartbeat path drops the location field.
    try:
        from agent.core.geo import fetch_location  # local import avoids cycles
        location = fetch_location()
    except Exception as e:
        logger.warning("Geo fetch failed during hardware snapshot: %s", e)
        location = None
    return {
        'hostname': get_hostname(),
        'mac_address': get_mac_address(),
        'ip_address': get_primary_ip(),
        'os': get_os_info(),
        'os_version': _platform.version(),
        'cpu_name': get_cpu_name(),
        'cpu_cores': psutil.cpu_count(logical=False) or 0,
        'cpu_threads': psutil.cpu_count(logical=True) or 0,
        'cpu_usage_percent': psutil.cpu_percent(interval=1),
        'ram_total_gb': round(psutil.virtual_memory().total / (1024**3), 2),
        'ram_used_gb': round(psutil.virtual_memory().used / (1024**3), 2),
        'ram_percent': psutil.virtual_memory().percent,
        'disks': get_disk_info(),
        'gpu': get_gpu_info(),
        'battery': get_battery_info(),
        'network_adapters': get_network_adapters(),
        'location': location,
        'agent_version': '1.0.0',
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Hardware fingerprint (SHA256)
# ---------------------------------------------------------------------------

def get_hardware_fingerprint(snapshot: dict) -> str:
    """Return SHA256 fingerprint of stable hardware fields."""
    import hashlib  # noqa: E402
    import json
    stable = {
        'hostname': snapshot.get('hostname'),
        'mac_address': snapshot.get('mac_address'),
        'os': snapshot.get('os'),
        'cpu_name': snapshot.get('cpu_name'),
        'cpu_cores': snapshot.get('cpu_cores'),
        'ram_total_gb': snapshot.get('ram_total_gb'),
        'disks': snapshot.get('disks'),
    }
    stable_str = json.dumps(stable, sort_keys=True)
    return hashlib.sha256(stable_str.encode()).hexdigest()