"""Geolocation helper — priority: Windows Location → WiFi BSSID → IP geo.

Windows Location Services uses WiFi triangulation (and GPS if available).
WiFi BSSID lookup (mylnikov.org, free, no key) gives ~50-100m accuracy.
IP geo is the final fallback; it is ISP-level only (city, not district).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from urllib.request import Request, urlopen

from agent.utils.logger import logger

_cache: dict = {'data': None, 'fetched_at': 0.0}
_CACHE_TTL_SEC = 600  # 10 minutes
_location_enabled_once = False  # ensure we only run the registry fix once per process


def ensure_location_services_enabled() -> None:
    """Enable Windows Location Services via registry if not already on.

    Sets the system-wide consent store to Allow so GeoCoordinateWatcher
    can get a fix without requiring manual user action in Settings.
    Only runs once per agent process (idempotent registry write).
    """
    global _location_enabled_once
    if _location_enabled_once or sys.platform != 'win32':
        return
    _location_enabled_once = True
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
# System-wide consent store (requires admin)
$consentPath = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\location'
if (Test-Path $consentPath) {
    Set-ItemProperty -Path $consentPath -Name Value -Value Allow -Type String -Force
}
# Enable location sensor (SensorPermissionState=1)
$sensorPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Sensor\Overrides\{BFA794E4-F964-4FDB-90F6-51056BFE4B44}'
if (Test-Path $sensorPath) {
    Set-ItemProperty -Path $sensorPath -Name SensorPermissionState -Value 1 -Type DWord -Force
} else {
    New-Item -Path $sensorPath -Force | Out-Null
    Set-ItemProperty -Path $sensorPath -Name SensorPermissionState -Value 1 -Type DWord -Force
}
# Remove GP disable-location flag if present
$gpPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors'
if (Test-Path $gpPath) {
    Remove-ItemProperty -Path $gpPath -Name DisableLocation -Force -ErrorAction SilentlyContinue
}
# User-level location consent
$userPath = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\DeviceAccess\Global\{BFA794E4-F964-4FDB-90F6-51056BFE4B44}'
if (-not (Test-Path $userPath)) { New-Item -Path $userPath -Force | Out-Null }
Set-ItemProperty -Path $userPath -Name Value -Value Allow -Type String -Force
"""
    try:
        from agent.utils.proc import NO_WINDOW
        subprocess.run(
            ['powershell', '-NonInteractive', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps],
            capture_output=True, timeout=10,
            creationflags=NO_WINDOW,
        )
    except Exception as e:
        logger.info('Location services enable failed: %s', e)


# PowerShell script that uses System.Device.Location.GeoCoordinateWatcher.
# GeoCoordinateWatcher is available on all Windows 10/11 machines and uses
# Windows Location Services (WiFi + IP triangulation, GPS if hardware present).
_PS_GEO_SCRIPT = r"""
try {
    Add-Type -AssemblyName System.Device -ErrorAction Stop
    $watcher = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::Default)
    $watcher.Start($false)
    $deadline = (Get-Date).AddSeconds(8)
    while ($watcher.Status -ne 'Ready' -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    $coord = $watcher.Position.Location
    $watcher.Stop()
    if ($coord.IsUnknown) { exit 1 }
    $acc = if ($coord.HorizontalAccuracy -is [double]) { [math]::Round($coord.HorizontalAccuracy, 1) } else { '' }
    Write-Output "$($coord.Latitude)|$($coord.Longitude)|$acc"
    exit 0
} catch { exit 1 }
"""


def fetch_windows_location() -> dict | None:
    """Return location from Windows Location Services or None if unavailable."""
    if sys.platform != 'win32':
        return None
    ensure_location_services_enabled()
    try:
        from agent.utils.proc import NO_WINDOW
        result = subprocess.run(
            ['powershell', '-NonInteractive', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', _PS_GEO_SCRIPT],
            capture_output=True, text=True, timeout=15,
            creationflags=NO_WINDOW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        parts = result.stdout.strip().split('|')
        if len(parts) < 2:
            return None
        lat, lon = float(parts[0]), float(parts[1])
        accuracy = float(parts[2]) if len(parts) > 2 and parts[2] else None
        return {
            'source': 'windows',
            'lat': lat,
            'lon': lon,
            'accuracy_m': accuracy,
        }
    except Exception as e:
        logger.info('Windows geolocation failed: %s', e)
        return None


def _scan_wifi_bssids() -> list[dict]:
    """Return list of {bssid, signal_pct} from nearby WiFi networks via netsh."""
    from agent.utils.proc import NO_WINDOW
    result = subprocess.run(
        ['netsh', 'wlan', 'show', 'networks', 'mode=bssid'],
        capture_output=True, timeout=10, encoding='utf-8', errors='ignore',
        creationflags=NO_WINDOW,
    )
    if result.returncode not in (0, 1):  # netsh exits 1 if no adapter, still outputs
        return []
    bssids: list[dict] = []
    current_bssid: str | None = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        m = re.match(r'BSSID\s+\d+\s*:\s*([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})', stripped)
        if m:
            current_bssid = m.group(1).upper()
            continue
        if current_bssid and re.match(r'Signal\s*:', stripped):
            try:
                pct = int(re.search(r'(\d+)%', stripped).group(1))
                bssids.append({'bssid': current_bssid, 'signal_pct': pct})
            except Exception:
                pass
            current_bssid = None
    bssids.sort(key=lambda x: x['signal_pct'], reverse=True)
    return bssids


def _wifi_via_google(bssids: list[dict], api_key: str) -> dict | None:
    """Submit BSSIDs to Google Geolocation API. Returns location or None."""
    body = json.dumps({
        'considerIp': False,
        'wifiAccessPoints': [
            {
                'macAddress': b['bssid'],
                # Convert Windows signal % to approximate dBm: pct/2 - 100
                'signalStrength': int(b['signal_pct'] / 2) - 100,
            }
            for b in bssids[:20]
        ],
    }).encode('utf-8')
    url = f'https://www.googleapis.com/geolocation/v1/geolocate?key={api_key}'
    req = Request(url, data=body, headers={
        'Content-Type': 'application/json',
        'User-Agent': 'BosowAgent/1.0',
    })
    with urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    loc = data.get('location', {})
    lat, lng = loc.get('lat'), loc.get('lng')
    if lat is None or lng is None:
        return None
    return {
        'source': 'wifi_google',
        'lat': float(lat),
        'lon': float(lng),
        'accuracy_m': data.get('accuracy'),
    }


def _wifi_via_mylnikov(bssids: list[dict]) -> dict | None:
    """Try top-5 BSSIDs against mylnikov.org (free, no key, limited coverage)."""
    headers = {'User-Agent': 'BosowAgent/1.0'}
    for b in bssids[:5]:
        try:
            url = f"https://api.mylnikov.org/geolocation/wifi?v=1.1&bssid={b['bssid']}"
            req = Request(url, headers=headers)
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            if data.get('result') == 200 and data.get('data'):
                lat = data['data'].get('lat')
                lon = data['data'].get('lon')
                if lat is not None and lon is not None:
                    logger.info('WiFi BSSID via mylnikov (%s): %.5f, %.5f', b['bssid'], lat, lon)
                    return {'source': 'wifi_bssid', 'lat': float(lat), 'lon': float(lon)}
        except Exception as e:
            logger.debug('mylnikov lookup failed for %s: %s', b['bssid'], e)
    return None


def fetch_wifi_bssid_location() -> dict | None:
    """Scan nearby WiFi and resolve position via BSSID database.

    Uses Google Geolocation API if GOOGLE_GEO_KEY is configured (best accuracy);
    otherwise falls back to mylnikov.org (free, no key, limited regional coverage).
    No admin required — uses netsh for WiFi scanning.
    """
    if sys.platform != 'win32':
        return None
    try:
        bssids = _scan_wifi_bssids()
        if not bssids:
            logger.debug('WiFi BSSID: no networks found')
            return None
        logger.info('WiFi scan: %d BSSIDs, strongest %s (%d%%)',
                    len(bssids), bssids[0]['bssid'], bssids[0]['signal_pct'])

        from agent import config
        if config.GOOGLE_GEO_KEY:
            try:
                loc = _wifi_via_google(bssids, config.GOOGLE_GEO_KEY)
                if loc:
                    logger.info('WiFi via Google (accuracy=%sm): %.5f, %.5f',
                                loc.get('accuracy_m', '?'), loc['lat'], loc['lon'])
                    return loc
                logger.info('Google Geolocation returned no location')
            except Exception as e:
                logger.info('Google Geolocation failed: %s', e)

        # Fallback: mylnikov (free, limited coverage)
        loc = _wifi_via_mylnikov(bssids)
        if loc:
            return loc

        logger.info('WiFi BSSID: no match found, falling back to IP geo')
        return None
    except Exception as e:
        logger.info('WiFi BSSID scan error: %s', e)
        return None


def fetch_ip_location() -> dict | None:
    """Return location from IP geolocation API (ISP-level accuracy)."""
    providers = [
        'https://ipapi.co/json/',
        'https://ip-api.com/json/?fields=status,message,country,regionName,city,lat,lon,query,isp',
    ]
    headers = {'User-Agent': 'BosowAgent/1.0'}
    for url in providers:
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=4) as resp:
                body = resp.read().decode('utf-8')
                data = json.loads(body)
            if 'ipapi.co' in url:
                if data.get('error'):
                    continue
                lat = data.get('latitude')
                lon = data.get('longitude')
                if lat is None or lon is None:
                    continue
                return {
                    'source': 'ipapi.co',
                    'ip': data.get('ip') or '',
                    'country': data.get('country_name') or '',
                    'region': data.get('region') or '',
                    'city': data.get('city') or '',
                    'lat': float(lat),
                    'lon': float(lon),
                    'isp': data.get('org') or '',
                }
            if data.get('status') != 'success':
                continue
            return {
                'source': 'ip-api.com',
                'ip': data.get('query') or '',
                'country': data.get('country') or '',
                'region': data.get('regionName') or '',
                'city': data.get('city') or '',
                'lat': float(data.get('lat')),
                'lon': float(data.get('lon')),
                'isp': data.get('isp') or '',
            }
        except Exception as e:
            logger.debug('geo provider %s failed: %s', url, e)
            continue
    return None


def fetch_location(force_refresh: bool = False) -> dict | None:
    """Return the best available location.

    Priority:
      1. Windows Location Services (WiFi triangulation / GPS via OS)
      2. WiFi BSSID lookup via mylnikov.org (~50-200m accuracy)
      3. IP geolocation fallback (city-level / ISP accuracy)

    Result cached for 10 minutes. Higher-accuracy sources always win on refresh.
    """
    now = time.time()
    cached = _cache['data']
    if not force_refresh and cached is not None and (now - float(_cache['fetched_at'] or 0)) < _CACHE_TTL_SEC:
        return cached

    # 1. Windows Location Services (GPS / WiFi via OS)
    win_loc = fetch_windows_location()
    if win_loc:
        _cache['data'] = win_loc
        _cache['fetched_at'] = now
        logger.info('Location from Windows (accuracy=%sm): %.5f, %.5f',
                    win_loc.get('accuracy_m', '?'), win_loc['lat'], win_loc['lon'])
        return win_loc

    # 2. WiFi BSSID triangulation
    wifi_loc = fetch_wifi_bssid_location()
    if wifi_loc:
        _cache['data'] = wifi_loc
        _cache['fetched_at'] = now
        return wifi_loc

    # 3. IP-based geolocation (city/ISP level)
    ip_loc = fetch_ip_location()
    if ip_loc:
        _cache['data'] = ip_loc
        _cache['fetched_at'] = now
        logger.info('Location from IP (%s): %.5f, %.5f', ip_loc.get('source'), ip_loc['lat'], ip_loc['lon'])
        return ip_loc

    # Keep last known value on failure
    return _cache['data']


# Keep old names as aliases so existing callers don't break
def fetch_ip_location_cached(force_refresh: bool = False) -> dict | None:
    return fetch_location(force_refresh)


def get_cached_location() -> dict | None:
    """Return whatever is currently cached without triggering a network fetch."""
    return _cache['data']
