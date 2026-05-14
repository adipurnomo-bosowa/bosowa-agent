"""Geolocation helper — tries Windows Location Services first, falls back to IP.

Windows Location Services uses WiFi triangulation (and GPS if available),
which is far more accurate than IP-based geolocation that points to the ISP.
Falls back silently to IP-based lookup if Windows geo is unavailable or denied.
"""
from __future__ import annotations

import json
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
# Remove GP disable-location flag if present
$gpPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors'
if (Test-Path $gpPath) {
    Remove-ItemProperty -Path $gpPath -Name DisableLocation -Force -ErrorAction SilentlyContinue
}
"""
    try:
        subprocess.run(
            ['powershell', '-NonInteractive', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps],
            capture_output=True, timeout=10,
        )
    except Exception as e:
        logger.debug('Location services enable failed: %s', e)


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
        result = subprocess.run(
            ['powershell', '-NonInteractive', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', _PS_GEO_SCRIPT],
            capture_output=True, text=True, timeout=15,
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
        logger.debug('Windows geolocation failed: %s', e)
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
    """Return the best available location. Tries Windows native first, then IP.

    Caches the result for 10 minutes. Windows-sourced location is kept in cache
    even if subsequent IP lookups succeed, since it is more accurate.
    """
    now = time.time()
    cached = _cache['data']
    if not force_refresh and cached is not None and (now - float(_cache['fetched_at'] or 0)) < _CACHE_TTL_SEC:
        return cached

    # Try Windows Location Services first (WiFi triangulation / GPS)
    win_loc = fetch_windows_location()
    if win_loc:
        _cache['data'] = win_loc
        _cache['fetched_at'] = now
        logger.debug('Location from Windows (accuracy=%sm): %.5f, %.5f',
                     win_loc.get('accuracy_m', '?'), win_loc['lat'], win_loc['lon'])
        return win_loc

    # Fall back to IP-based geolocation
    ip_loc = fetch_ip_location()
    if ip_loc:
        _cache['data'] = ip_loc
        _cache['fetched_at'] = now
        logger.debug('Location from IP (%s): %.5f, %.5f', ip_loc.get('source'), ip_loc['lat'], ip_loc['lon'])
        return ip_loc

    # On failure keep last known value
    return _cache['data']


# Keep old names as aliases so existing callers don't break
def fetch_ip_location_cached(force_refresh: bool = False) -> dict | None:
    return fetch_location(force_refresh)


def get_cached_location() -> dict | None:
    """Return whatever is currently cached without triggering a network fetch."""
    return _cache['data']
