"""Batched audit telemetry (link opens, foreground app) — aggregated server-side."""
from __future__ import annotations

import threading
from typing import Any
from urllib.parse import urlparse

import certifi
import requests

from agent import config
from agent.core.hardware import get_mac_address
from agent.utils.logger import logger

_lock = threading.Lock()
_pending: dict[tuple[str, str], dict[str, Any]] = {}


def _normalize_link_key(url: str) -> str:
    try:
        p = urlparse(url.strip())
        host = (p.netloc or '').lower()[:120]
        path = (p.path or '')[:120]
        return f'{host}{path}' if host else url[:200]
    except Exception:
        return url[:200]


def record_link_open(url: str, sample_detail: str | None = None) -> None:
    key = _normalize_link_key(url)
    enqueue_audit('LINK', key, count=1, sample_detail=sample_detail or url)


def record_app_focus(exe_path: str, *, emit_sample: bool = True) -> None:
    exe = (exe_path or '').strip()
    if not exe:
        return
    base = exe.replace('\\', '/').split('/')[-1][:120]
    if not base:
        return
    # Only pass sampleDetail on app-change events to keep sample table sparse
    enqueue_audit('APP', base.lower(), count=1, sample_detail=exe[:400] if emit_sample else None)


def enqueue_audit(
    category: str,
    key: str,
    *,
    count: int = 1,
    sample_detail: str | None = None,
) -> None:
    if not category or not key:
        return
    ck = (category[:32], key[:256])
    with _lock:
        bucket = _pending.get(ck)
        if bucket is None:
            _pending[ck] = {'category': ck[0], 'key': ck[1], 'count': count, 'sampleDetail': sample_detail}
        else:
            bucket['count'] = int(bucket.get('count', 0)) + count
            if sample_detail and not bucket.get('sampleDetail'):
                bucket['sampleDetail'] = sample_detail


def flush_audit_buffer(token: str | None) -> None:
    if not token:
        return
    with _lock:
        if not _pending:
            return
        items = list(_pending.values())
        _pending.clear()
    try:
        r = requests.post(
            f'{config.API_BASE}/agent/audit/batch',
            json={'device_mac': get_mac_address(), 'items': items},
            headers={'Authorization': f'Bearer {token}'},
            timeout=config.HTTP_TIMEOUT,
            verify=certifi.where(),
        )
        r.raise_for_status()
        logger.debug('Audit batch flushed (%d keys)', len(items))
    except Exception as e:
        logger.warning('Audit batch flush failed: %s', e)
        with _lock:
            for it in items:
                ck = (it['category'], it['key'])
                ex = _pending.get(ck)
                if ex is None:
                    _pending[ck] = it
                else:
                    ex['count'] = int(ex.get('count', 0)) + int(it.get('count', 0))
