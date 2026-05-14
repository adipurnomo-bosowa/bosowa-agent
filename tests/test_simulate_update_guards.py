"""Simulate / verify update-loop guards (silent update off, marker, watchdog paths)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


def test_silent_update_flag_can_be_toggled(monkeypatch):
    from agent import config

    monkeypatch.setattr(config, 'SILENT_AGENT_UPDATE', False)
    assert config.SILENT_AGENT_UPDATE is False
    monkeypatch.setattr(config, 'SILENT_AGENT_UPDATE', True)
    assert config.SILENT_AGENT_UPDATE is True


def test_simulate_watchdog_skips_relaunch_when_mutex_appears(tmp_path, monkeypatch):
    """If marker is fresh and mutex exists during wait, _relaunch_agent must not run."""
    import agent.utils.watchdog as w
    import agent.utils.update_exit_marker as uem

    monkeypatch.setattr(w, 'POLL_INTERVAL_SECS', 0)
    monkeypatch.setattr(w, 'UPDATE_WAIT_POLL_SECS', 0)
    monkeypatch.setattr(w, 'UPDATE_WAIT_MAX_SECS', 10)
    monkeypatch.setattr(w, 'RESTART_DELAY_SECS', 0)

    relaunch_calls: list[int] = []

    def fake_relaunch():
        relaunch_calls.append(1)

    monkeypatch.setattr(w, '_relaunch_agent', fake_relaunch)
    monkeypatch.setattr(uem, 'update_replace_marker_fresh', lambda: True)
    monkeypatch.setattr(uem, 'another_agent_mutex_exists', lambda: True)
    cleared: list[int] = []

    def fake_clear():
        cleared.append(1)

    monkeypatch.setattr(uem, 'clear_update_replace_marker', fake_clear)

    pid_file = tmp_path / 'watchdog.pid'
    with patch.object(w, '_is_pid_running', return_value=False):
        with patch.object(w, '_write_watchdog_pid'):
            w.run_watchdog(12345, pid_file)

    assert not relaunch_calls
    assert cleared == [1]


def test_simulate_watchdog_relaunches_when_marker_stale(tmp_path, monkeypatch):
    """No marker → normal crash path → one relaunch."""
    import agent.utils.watchdog as w
    import agent.utils.update_exit_marker as uem

    monkeypatch.setattr(w, 'POLL_INTERVAL_SECS', 0)
    monkeypatch.setattr(w, 'UPDATE_WAIT_POLL_SECS', 0)
    monkeypatch.setattr(w, 'UPDATE_WAIT_MAX_SECS', 10)
    monkeypatch.setattr(w, 'RESTART_DELAY_SECS', 0)

    relaunch_calls: list[int] = []

    def fake_relaunch():
        relaunch_calls.append(1)

    monkeypatch.setattr(w, '_relaunch_agent', fake_relaunch)
    monkeypatch.setattr(uem, 'update_replace_marker_fresh', lambda: False)

    pid_file = tmp_path / 'watchdog2.pid'
    with patch.object(w, '_is_pid_running', return_value=False):
        with patch.object(w, '_write_watchdog_pid'):
            w.run_watchdog(99999, pid_file)

    assert relaunch_calls == [1]


@pytest.mark.asyncio
async def test_agent_service_no_version_poll_when_silent_off(monkeypatch):
    from agent import config
    from agent.auth.login import AuthTokens
    from agent.core.agent_service import AgentService

    names: list[str] = []
    real_create = asyncio.create_task

    def capture(coro, **kwargs):
        code = getattr(coro, 'cr_code', None)
        names.append(code.co_name if code else type(coro).__name__)
        return real_create(coro, **kwargs)

    monkeypatch.setattr(asyncio, 'create_task', capture)
    monkeypatch.setattr(config, 'SILENT_AGENT_UPDATE', False)

    svc = AgentService(AuthTokens(token='t', refresh_token=None, user={'name': 'Test'}))
    svc._running = True
    await svc._start_background_tasks()

    assert '_version_check_loop' not in names
    for t in svc._tasks:
        t.cancel()
    await asyncio.gather(*svc._tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_service_schedules_version_poll_when_silent_on(monkeypatch):
    from agent import config
    from agent.auth.login import AuthTokens
    from agent.core.agent_service import AgentService

    names: list[str] = []
    real_create = asyncio.create_task

    def capture(coro, **kwargs):
        code = getattr(coro, 'cr_code', None)
        names.append(code.co_name if code else type(coro).__name__)
        return real_create(coro, **kwargs)

    monkeypatch.setattr(asyncio, 'create_task', capture)
    monkeypatch.setattr(config, 'SILENT_AGENT_UPDATE', True)

    svc = AgentService(AuthTokens(token='t', refresh_token=None, user={'name': 'Test'}))
    svc._running = True
    await svc._start_background_tasks()

    assert '_version_check_loop' in names
    for t in svc._tasks:
        t.cancel()
    await asyncio.gather(*svc._tasks, return_exceptions=True)
