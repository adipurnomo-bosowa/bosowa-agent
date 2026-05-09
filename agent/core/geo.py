"""IP-based geolocation helper used by hardware snapshot, socket register, and heartbeat.

The function is intentionally synchronous and self-contained so it can be safely
called from any thread (sync HTTP register, async heartbeat, etc.). Failures are
silent and return None — callers should treat geolocation as best-effort.
"""
from __future__ import annotations

import json
import time
from urllib.request import Request, urlopen

from agent.utils.logger import logger

# Cache the result process-wide to avoid hammering external providers.
_cache: dict = {'data': None, 'fetched_at': 0.0}
_CACHE_TTL_SEC = 600  # 10 minutes


def fetch_ip_location(force_refresh: bool = False) -> dict | None:
    """Return a normalized location dict or None.

    Shape: {source, ip, country, region, city, lat, lon, isp}
    """
    now = time.time()
    if not force_refresh and _cache['data'] is not None and (now - float(_cache['fetched_at'] or 0)) < _CACHE_TTL_SEC:
        return _cache['data']

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
                result = {
                    'source': 'ipapi.co',
                    'ip': data.get('ip') or '',
                    'country': data.get('country_name') or '',
                    'region': data.get('region') or '',
                    'city': data.get('city') or '',
                    'lat': float(lat),
                    'lon': float(lon),
                    'isp': data.get('org') or '',
                }
                _cache['data'] = result
                _cache['fetched_at'] = now
                return result
            if data.get('status') != 'success':
                continue
            result = {
                'source': 'ip-api.com',
                'ip': data.get('query') or '',
                'country': data.get('country') or '',
                'region': data.get('regionName') or '',
                'city': data.get('city') or '',
                'lat': float(data.get('lat')),
                'lon': float(data.get('lon')),
                'isp': data.get('isp') or '',
            }
            _cache['data'] = result
            _cache['fetched_at'] = now
            return result
        except Exception as e:
            logger.debug('geo provider %s failed: %s', url, e)
            continue
    # Note: do NOT overwrite cache on failure — keep last known value
    return _cache['data']


def get_cached_location() -> dict | None:
    """Return whatever is currently cached without triggering a network fetch."""
    return _cache['data']
