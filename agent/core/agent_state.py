"""Shared, thread-safe agent state visible to the UI without an explicit handle.

The tray runs on the Qt thread; the socket client and command handlers run on
asyncio threads. Both need to publish/observe state (online flag, last sync,
last error). A module-level container with a lock is the simplest channel — no
imports cycles, no event bus.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

_lock = threading.Lock()

_state = {
    'online': False,
    'last_online_ts': 0.0,
    'last_offline_ts': 0.0,
    'last_error': '',
    # Set once at startup so the UI can show "Anti-virus might block self-update".
    'sac_mode': 'unknown',           # 'off' | 'eval' | 'on' | 'unknown'
    'defender_exclusion_ok': None,    # True/False/None
}

_listeners: list[Callable[[dict], None]] = []


def get_snapshot() -> dict:
    with _lock:
        return dict(_state)


def set_online(value: bool, error: str = '') -> None:
    now = time.time()
    with _lock:
        changed = _state['online'] != value
        _state['online'] = bool(value)
        if value:
            _state['last_online_ts'] = now
            _state['last_error'] = ''
        else:
            _state['last_offline_ts'] = now
            if error:
                _state['last_error'] = error
        snap = dict(_state)
    if changed:
        for cb in list(_listeners):
            try:
                cb(snap)
            except Exception:
                pass


def set_environment(*, sac_mode: str | None = None,
                    defender_exclusion_ok: bool | None = None) -> None:
    with _lock:
        if sac_mode is not None:
            _state['sac_mode'] = sac_mode
        if defender_exclusion_ok is not None:
            _state['defender_exclusion_ok'] = defender_exclusion_ok


def add_listener(cb: Callable[[dict], None]) -> None:
    _listeners.append(cb)


__all__ = ['get_snapshot', 'set_online', 'set_environment', 'add_listener']
