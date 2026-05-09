"""HTTP client helpers for ticketing from desktop agent."""
from __future__ import annotations

from typing import Any

import requests
import certifi

from agent import config
from agent.auth.token_store import get_device_token
from agent.core.hardware import get_mac_address


def _headers() -> dict[str, str]:
    token = get_device_token()
    if not token:
        raise RuntimeError('Device token tidak ditemukan. Silakan login ulang.')
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }


def create_ticket(
    title: str,
    category: str,
    description: str,
    priority: str = 'MEDIUM',
    device_mac: str | None = None,
) -> dict[str, Any]:
    payload = {
        'title': title.strip(),
        'category': category,
        'description': description.strip(),
        'priority': priority,
        'device_mac': device_mac or get_mac_address(),
    }
    resp = requests.post(
        f'{config.API_BASE}/tickets',
        headers=_headers(),
        json=payload,
        timeout=config.HTTP_TIMEOUT,
        verify=certifi.where(),
    )
    if not resp.ok:
        try:
            err = resp.json().get('error')
        except Exception:
            err = resp.text
        raise RuntimeError(f'Gagal buat tiket ({resp.status_code}): {err}')
    data = resp.json()
    return data.get('ticket', data)


def list_my_tickets(status: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, str] = {}
    if status:
        params['status'] = status
    if category:
        params['category'] = category
    resp = requests.get(
        f'{config.API_BASE}/tickets',
        headers=_headers(),
        params=params or None,
        timeout=config.HTTP_TIMEOUT,
        verify=certifi.where(),
    )
    if not resp.ok:
        try:
            err = resp.json().get('error')
        except Exception:
            err = resp.text
        raise RuntimeError(f'Gagal ambil tiket ({resp.status_code}): {err}')
    body = resp.json()
    return body.get('tickets', [])
