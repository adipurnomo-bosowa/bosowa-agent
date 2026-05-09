import asyncio
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_hardware_refresh_loop_sends_hardware_after_sleep():
    """Loop harus panggil _send_initial_hardware setelah asyncio.sleep(3600)."""
    from agent.auth.login import AuthTokens
    from agent.core.agent_service import AgentService

    tokens = AuthTokens(token='tok', refresh_token=None, user={'name': 'T'})
    svc = AgentService(tokens)
    svc._running = True

    call_count = 0

    async def fake_sleep(n):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            svc._running = False  # stop after second sleep (after first call to _send_initial_hardware)

    with patch('agent.core.agent_service.asyncio.sleep', side_effect=fake_sleep), \
         patch.object(svc, '_send_initial_hardware', new_callable=AsyncMock) as mock_hw:
        await svc._hardware_refresh_loop()

    mock_hw.assert_called_once()


@pytest.mark.asyncio
async def test_collect_hardware_command_calls_send_hardware():
    """Command collect_hardware harus panggil _send_initial_hardware."""
    from agent.auth.login import AuthTokens
    from agent.core.agent_service import AgentService

    tokens = AuthTokens(token='tok', refresh_token=None, user={'name': 'T'})
    svc = AgentService(tokens)

    with patch.object(svc, '_send_initial_hardware', new_callable=AsyncMock) as mock_hw:
        await svc._handle_command({'type': 'collect_hardware'})

    mock_hw.assert_called_once()
