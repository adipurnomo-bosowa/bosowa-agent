"""GET_HARDWARE_INFO command — returns detailed hardware specs via WMI."""
from __future__ import annotations

import logging
import platform as _platform

logger = logging.getLogger(__name__)

try:
    import wmi as _wmi
    _WMI = _wmi.WMI()
    _WMI_OK = True
except Exception:
    _WMI = None
    _WMI_OK = False

try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    psutil = None  # type: ignore
    _PSUTIL_OK = False


def _wmi_attr(obj, *attrs, default='Unknown'):
    """Safely get first matching attribute from a WMI object."""
    for a in attrs:
        try:
            v = getattr(obj, a, None)
            if v and str(v).strip():
                return str(v).strip()
        except Exception:
            pass
    return default


# ── CPU ──────────────────────────────────────────────────────────────────────

def _get_cpu() -> dict:
    info = {
        'model': _platform.processor() or 'Unknown',
        'cores': 0,
        'threads': 0,
    }
    if _PSUTIL_OK:
        info['cores'] = psutil.cpu_count(logical=False) or 0
        info['threads'] = psutil.cpu_count(logical=True) or 0
    if _WMI_OK:
        try:
            cpu = _WMI.Win32_Processor()[0]
            info['model'] = _wmi_attr(cpu, 'Name')
            info['cores'] = int(getattr(cpu, 'NumberOfCores', 0) or 0)
            info['threads'] = int(getattr(cpu, 'NumberOfLogicalProcessors', 0) or 0)
        except Exception as e:
            logger.warning('CPU WMI error: %s', e)
    return info


# ── RAM ──────────────────────────────────────────────────────────────────────

def _get_ram() -> dict:
    total_gb = 0
    slots_used = 0
    slots_total = 0
    sticks: list[dict] = []

    if _PSUTIL_OK:
        total_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)

    if _WMI_OK:
        try:
            # Physical memory slots
            for m in _WMI.Win32_PhysicalMemory():
                cap = int(getattr(m, 'Capacity', 0) or 0)
                cap_gb = round(cap / (1024 ** 3), 1)
                speed = _wmi_attr(m, 'Speed', default='')
                mem_type_map = {
                    20: 'DDR', 21: 'DDR2', 22: 'DDR2 FB-DIMM',
                    24: 'DDR3', 26: 'DDR4', 34: 'DDR5',
                }
                mem_type_code = int(getattr(m, 'SMBIOSMemoryType', 0) or 0)
                mem_type = mem_type_map.get(mem_type_code, f'Type{mem_type_code}')
                sticks.append({
                    'slot': _wmi_attr(m, 'DeviceLocator', default='Unknown'),
                    'capacity_gb': cap_gb,
                    'type': mem_type,
                    'speed_mhz': speed,
                    'manufacturer': _wmi_attr(m, 'Manufacturer', default=''),
                    'part_number': _wmi_attr(m, 'PartNumber', default='').strip(),
                    'serial_number': _wmi_attr(m, 'SerialNumber', default='').strip(),
                })
            slots_used = len(sticks)
        except Exception as e:
            logger.warning('RAM PhysicalMemory WMI error: %s', e)

        try:
            # Total physical slots (may include empty)
            for arr in _WMI.Win32_PhysicalMemoryArray():
                slots_total = int(getattr(arr, 'MemoryDevices', 0) or 0)
                break
        except Exception as e:
            logger.warning('RAM MemoryArray WMI error: %s', e)

    return {
        'total_gb': total_gb,
        'slots_used': slots_used,
        'slots_total': slots_total,
        'sticks': sticks,
    }


# ── Motherboard ───────────────────────────────────────────────────────────────

def _get_motherboard() -> dict:
    info = {'model': 'Unknown', 'manufacturer': 'Unknown', 'serial': '', 'bios_version': '', 'bios_serial': ''}
    if not _WMI_OK:
        return info
    try:
        board = _WMI.Win32_BaseBoard()[0]
        info['model'] = _wmi_attr(board, 'Product')
        info['manufacturer'] = _wmi_attr(board, 'Manufacturer')
        info['serial'] = _wmi_attr(board, 'SerialNumber', default='')
    except Exception as e:
        logger.warning('Motherboard WMI error: %s', e)
    try:
        bios = _WMI.Win32_BIOS()[0]
        info['bios_version'] = _wmi_attr(bios, 'SMBIOSBIOSVersion', default='')
        info['bios_serial'] = _wmi_attr(bios, 'SerialNumber', default='')
    except Exception as e:
        logger.warning('BIOS WMI error: %s', e)
    return info


# ── GPU ──────────────────────────────────────────────────────────────────────

def _get_gpus() -> list[dict]:
    gpus = []
    if not _WMI_OK:
        return gpus
    try:
        for g in _WMI.Win32_VideoController():
            ram_bytes = int(getattr(g, 'AdapterRAM', 0) or 0)
            gpus.append({
                'model': _wmi_attr(g, 'Name'),
                'driver_version': _wmi_attr(g, 'DriverVersion', default=''),
                'vram_gb': round(ram_bytes / (1024 ** 3), 1) if ram_bytes else None,
            })
    except Exception as e:
        logger.warning('GPU WMI error: %s', e)
    return gpus


# ── Storage ──────────────────────────────────────────────────────────────────

def _get_storage() -> list[dict]:
    disks = []
    if _WMI_OK:
        try:
            for d in _WMI.Win32_DiskDrive():
                size_bytes = int(getattr(d, 'Size', 0) or 0)
                disks.append({
                    'model': _wmi_attr(d, 'Model'),
                    'serial': _wmi_attr(d, 'SerialNumber', default='').strip(),
                    'interface': _wmi_attr(d, 'InterfaceType', default=''),
                    'firmware': _wmi_attr(d, 'FirmwareRevision', default=''),
                    'size_gb': round(size_bytes / (1024 ** 3), 1) if size_bytes else 0,
                    'media_type': _wmi_attr(d, 'MediaType', default=''),
                })
        except Exception as e:
            logger.warning('Storage WMI error: %s', e)
    return disks


def _get_partitions() -> list[dict]:
    """Return logical drive info (C:, D:, etc.) with used/free space."""
    partitions = []
    if not _PSUTIL_OK:
        return partitions
    try:
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                # Normalise drive letter: 'C:\\' -> 'C:'
                drive = part.mountpoint.rstrip('\\').rstrip('/')
                partitions.append({
                    'drive': drive,
                    'fstype': part.fstype,
                    'total_gb': round(usage.total / (1024 ** 3), 1),
                    'used_gb': round(usage.used / (1024 ** 3), 1),
                    'free_gb': round(usage.free / (1024 ** 3), 1),
                    'percent': round(usage.percent, 1),
                })
            except (PermissionError, OSError):
                pass
    except Exception as e:
        logger.warning('Partitions error: %s', e)
    return partitions


# ── License ───────────────────────────────────────────────────────────────────

def _decode_product_key(dpid: bytearray) -> str:
    """Decode 25-char product key from DigitalProductId registry bytes."""
    try:
        KEY_OFFSET = 52
        CHARS = 'BCDFGHJKMPQRTVWXY2346789'
        # Windows 8+ flag
        is_win8plus = (dpid[66] >> 3) & 1
        dpid[66] = (dpid[66] & 0xF7) | ((is_win8plus & 2) << 2)
        key = ''
        for i in range(24, -1, -1):
            cur = 0
            for j in range(14, -1, -1):
                cur = cur * 256 ^ dpid[j + KEY_OFFSET]
                dpid[j + KEY_OFFSET] = cur // 24
                cur %= 24
            key = CHARS[cur] + key
            if i % 5 == 0 and i != 0:
                key = '-' + key
        if is_win8plus:
            key = key[-1] + key[:-1]
        return key
    except Exception:
        return ''


_LICENSE_STATUS_MAP = {
    0: 'Unlicensed', 1: 'Licensed', 2: 'OOBGrace',
    3: 'OOTGrace', 4: 'NonGenuineGrace', 5: 'Notification', 6: 'ExtendedGrace',
}

_WINDOWS_APP_ID = '55c92734-d682-4d71-983e-d6ec3f16059f'
_OFFICE_APP_ID  = '0ff1ce15-a989-479d-af46-f275c6370663'


def _get_windows_license() -> dict:
    info: dict = {
        'edition': 'Unknown',
        'build': '',
        'activation_status': 'Unknown',
        'partial_key': '',
        'product_key': '',
        'channel': '',
    }

    # Edition & build
    if _WMI_OK:
        try:
            os_obj = _WMI.Win32_OperatingSystem()[0]
            info['edition'] = _wmi_attr(os_obj, 'Caption')
            info['build'] = _wmi_attr(os_obj, 'BuildNumber', default='')
        except Exception as e:
            logger.warning('OS WMI error: %s', e)

    # Activation status + partial key via SoftwareLicensingProduct
    if _WMI_OK:
        try:
            for p in _WMI.SoftwareLicensingProduct(ApplicationId=_WINDOWS_APP_ID):
                partial = getattr(p, 'PartialProductKey', None)
                if not partial:
                    continue
                info['partial_key'] = str(partial)
                status_code = int(getattr(p, 'LicenseStatus', 0) or 0)
                info['activation_status'] = _LICENSE_STATUS_MAP.get(status_code, f'Code{status_code}')
                channel = getattr(p, 'ProductKeyChannel', None)
                if channel:
                    info['channel'] = str(channel)
                break
        except Exception as e:
            logger.warning('Windows license WMI error: %s', e)

    # Full product key from DigitalProductId registry
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\Windows NT\CurrentVersion') as k:
            dpid, _ = winreg.QueryValueEx(k, 'DigitalProductId')
            info['product_key'] = _decode_product_key(bytearray(dpid))
    except Exception as e:
        logger.warning('Windows product key registry error: %s', e)

    return info


_C2R_PRODUCT_MAP = {
    'O365HomePrem': 'Microsoft 365 Personal/Home',
    'O365Business': 'Microsoft 365 Business',
    'O365SmallBus': 'Microsoft 365 Business',
    'O365ProPlus': 'Microsoft 365 Apps for Enterprise',
    'ProPlus': 'Office Professional Plus',
    'Standard': 'Office Standard',
    'HomeBusiness': 'Office Home & Business',
    'HomeStudent': 'Office Home & Student',
    'Home': 'Office Home',
    'Personal': 'Office Personal',
    'ProjectPro': 'Project Professional',
    'VisioPro': 'Visio Professional',
    'Access': 'Office Access',
    'Word': 'Microsoft Word',
    'Excel': 'Microsoft Excel',
    'Outlook': 'Microsoft Outlook',
    'PowerPoint': 'Microsoft PowerPoint',
}

_C2R_CHANNEL_MAP = {
    '492350f6-3a01-4f97-b9c0-c7c6ddf67d60': 'Current Channel',
    '7ffbc6bf-bc32-4f92-8982-f9dd17fd3114': 'Semi-Annual Channel',
    'b8f9b850-328d-4355-9145-c59439a0c4cf': 'Semi-Annual (Preview)',
    '64256afe-f5d9-4f86-8936-8840a6a4f5be': 'Current Channel (Preview)',
    '55336b82-a18d-4dd6-b5f6-9e5095c314a6': 'Monthly Enterprise Channel',
    'c2affa96-051d-4e88-b53e-1cc06624f17a': 'Monthly Enterprise (Preview)',
}


def _parse_c2r_product(prod_ids: str) -> str:
    """Convert ClickToRun ProductReleaseIds to friendly name."""
    for key, name in _C2R_PRODUCT_MAP.items():
        if key.lower() in prod_ids.lower():
            # Detect year suffix: ProPlus2021Retail, ProPlus2019Volume, etc.
            import re
            m = re.search(r'(\d{4})', prod_ids)
            year = f' {m.group(1)}' if m else ''
            return name + year
    # Fallback: strip Retail/Volume/Preview suffix
    import re
    cleaned = re.sub(r'(Retail|Volume|Preview|\d{4})', '', prod_ids.split(',')[0]).strip()
    return cleaned or prod_ids.split(',')[0]


def _parse_c2r_channel(channel: str) -> str:
    """Convert ClickToRun UpdateChannel URL/GUID to friendly name."""
    # Could be full URL like http://officecdn.microsoft.com/pr/{GUID}
    for guid, name in _C2R_CHANNEL_MAP.items():
        if guid.lower() in channel.lower():
            return name
    # If it's a URL, extract last path segment
    if '/' in channel:
        return channel.rstrip('/').split('/')[-1]
    return channel


# Products that are NOT considered "Microsoft Office" (standalone free apps)
_C2R_EXCLUDE = {'onenotefree', 'onenote', 'onedrive', 'teams', 'skype'}


def _is_office_product(prod_ids: str) -> bool:
    """Return True only if ProductReleaseIds contains a real Office product."""
    ids_lower = prod_ids.lower()
    # Must contain at least one known Office product key
    office_keys = list(_C2R_PRODUCT_MAP.keys())
    has_office = any(k.lower() in ids_lower for k in office_keys)
    if has_office:
        return True
    # Exclude if only free/standalone apps
    parts = [p.strip().lower() for p in prod_ids.split(',')]
    real_parts = [p for p in parts if not any(ex in p for ex in _C2R_EXCLUDE)]
    return len(real_parts) > 0 and any(
        p for p in real_parts if p not in ('', 'access', 'publisher')
        and not any(ex in p for ex in _C2R_EXCLUDE)
        and any(kw in p for kw in ('proplus', 'standard', 'homebusiness', 'homestudent',
                                    'home', 'personal', 'project', 'visio', 'o365', 'word',
                                    'excel', 'outlook', 'powerpoint'))
    )


def _get_office_license() -> dict:
    info: dict = {
        'edition': '',
        'version': '',
        'activation_status': 'Unknown',
        'partial_key': '',
        'product_key': '',
        'channel': '',
        'install_type': '',
    }

    try:
        import winreg

        # Method 1: Click-to-Run (covers Store M365, modern Office 2019/2021/365)
        c2r_paths = [
            r'SOFTWARE\Microsoft\Office\ClickToRun\Configuration',
            r'SOFTWARE\WOW6432Node\Microsoft\Office\ClickToRun\Configuration',
        ]
        for c2r_path in c2r_paths:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, c2r_path) as k:
                    # Version
                    try:
                        ver_full, _ = winreg.QueryValueEx(k, 'VersionToReport')
                        info['version'] = '16.0'
                        info['install_type'] = 'Click-to-Run'
                    except FileNotFoundError:
                        info['version'] = '16.0'
                        info['install_type'] = 'Click-to-Run'

                    # Product name — skip if only free standalone apps (e.g. OneNoteFree)
                    try:
                        prod_ids, _ = winreg.QueryValueEx(k, 'ProductReleaseIds')
                        if not _is_office_product(str(prod_ids)):
                            # Not a real Office suite, reset and skip
                            info['version'] = ''
                            info['install_type'] = ''
                            break
                        info['edition'] = _parse_c2r_product(str(prod_ids))
                    except FileNotFoundError:
                        pass

                    # Update channel
                    try:
                        ch, _ = winreg.QueryValueEx(k, 'UpdateChannel')
                        info['channel'] = _parse_c2r_channel(str(ch))
                    except FileNotFoundError:
                        # Try UpdateChannelChanged
                        try:
                            ch, _ = winreg.QueryValueEx(k, 'UpdateChannelChanged')
                            info['channel'] = _parse_c2r_channel(str(ch))
                        except FileNotFoundError:
                            pass

                    # Platform (32/64-bit)
                    try:
                        plat, _ = winreg.QueryValueEx(k, 'Platform')
                        info['install_type'] = f'Click-to-Run ({plat})'
                    except FileNotFoundError:
                        pass

                    break
            except FileNotFoundError:
                continue

        # Method 2: MSI install (Office 2010/2013/2016 traditional installer)
        if not info['version']:
            version_names = {
                '16.0': 'Office 2016/2019/2021/365',
                '15.0': 'Office 2013',
                '14.0': 'Office 2010',
            }
            for ver, name in version_names.items():
                try:
                    key_path = rf'SOFTWARE\Microsoft\Office\{ver}\Common\InstallRoot'
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as k:
                        path, _ = winreg.QueryValueEx(k, 'Path')
                        if path:
                            info['version'] = ver
                            info['edition'] = name
                            info['install_type'] = 'MSI'
                            break
                except FileNotFoundError:
                    continue

    except Exception as e:
        logger.warning('Office detection error: %s', e)

    if not info['version']:
        return info  # Office tidak terinstall

    # Activation status + partial key via WMI SoftwareLicensingProduct
    if _WMI_OK:
        try:
            for p in _WMI.SoftwareLicensingProduct(ApplicationId=_OFFICE_APP_ID):
                partial = getattr(p, 'PartialProductKey', None)
                if not partial:
                    continue
                info['partial_key'] = str(partial)
                status_code = int(getattr(p, 'LicenseStatus', 0) or 0)
                info['activation_status'] = _LICENSE_STATUS_MAP.get(status_code, f'Code{status_code}')
                wmi_channel = getattr(p, 'ProductKeyChannel', None)
                if wmi_channel and not info['channel']:
                    info['channel'] = str(wmi_channel)
                break
        except Exception as e:
            logger.warning('Office license WMI error: %s', e)

    # Full product key from DigitalProductId (MSI installs only — C2R uses account activation)
    if info['install_type'] == 'MSI':
        try:
            import winreg
            reg_bases = [
                rf'SOFTWARE\Microsoft\Office\{info["version"]}\Registration',
                rf'SOFTWARE\WOW6432Node\Microsoft\Office\{info["version"]}\Registration',
            ]
            for base_path in reg_bases:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_path) as base_key:
                        idx = 0
                        while True:
                            try:
                                sub_name = winreg.EnumKey(base_key, idx)
                                with winreg.OpenKey(base_key, sub_name) as sub:
                                    try:
                                        dpid, _ = winreg.QueryValueEx(sub, 'DigitalProductId')
                                        decoded = _decode_product_key(bytearray(dpid))
                                        if decoded:
                                            info['product_key'] = decoded
                                            try:
                                                prod, _ = winreg.QueryValueEx(sub, 'ProductName')
                                                if prod:
                                                    info['edition'] = str(prod)
                                            except Exception:
                                                pass
                                            break
                                    except FileNotFoundError:
                                        pass
                                idx += 1
                            except OSError:
                                break
                    if info['product_key']:
                        break
                except FileNotFoundError:
                    continue
        except Exception as e:
            logger.warning('Office product key registry error: %s', e)

    return info


# ── Handler ───────────────────────────────────────────────────────────────────

async def handle_get_hardware_info(payload: dict) -> dict:
    """Return detailed hardware info: CPU, RAM, Motherboard, GPU, Storage, License."""
    return {
        'cpu': _get_cpu(),
        'ram': _get_ram(),
        'motherboard': _get_motherboard(),
        'gpus': _get_gpus(),
        'storage': _get_storage(),
        'partitions': _get_partitions(),
        'windows_license': _get_windows_license(),
        'office_license': _get_office_license(),
    }
