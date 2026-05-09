"""Main agent service – orchestrates all components after authentication."""
from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime, timezone

import requests

from agent import config
from agent.auth.login import AuthTokens, check_and_refresh_token
from agent.auth.token_store import (
    store_device_token,
    store_refresh_token,
    get_device_token,
    get_refresh_token,
)
from agent.core.hardware import get_hardware_snapshot
from agent.core.socket_client import AgentSocketClient
from agent.core.heartbeat import heartbeat_loop
from agent.core.uptime import (
    register_shutdown_hooks,
    send_power_on,
)
from agent.core.audit_client import flush_audit_buffer
from agent.core.focus_poll import get_foreground_exe_path
from agent.utils.logger import logger


class AgentService:
    """Manages the post-authentication agent lifecycle."""

    def __init__(self, tokens: AuthTokens):
        self.tokens = tokens
        self._token_getter = lambda: self.tokens.token
        self._socket: AgentSocketClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._last_exe: str | None = None

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    async def start(self) -> None:
        logger.info('AgentService starting for user=%s', self.tokens.user.get('name', '?'))
        self._running = True

        register_shutdown_hooks(lambda: self.tokens.token if self._running else None)

        await self._send_initial_hardware()
        send_power_on(self.tokens.token)

        await self._start_socket()

        await self._start_background_tasks()

        logger.info('AgentService running')

    async def _send_initial_hardware(self) -> None:
        try:
            snapshot = get_hardware_snapshot()
            resp = requests.post(
                f'{config.API_BASE}/devices/register',
                json=snapshot,
                headers={'Authorization': f'Bearer {self.tokens.token}'},
                timeout=config.HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            logger.info('Hardware snapshot registered')
        except Exception as e:
            logger.warning('Failed to register hardware snapshot: %s', e)

    async def _start_socket(self) -> None:
        self._socket = AgentSocketClient(
            server_url=config.SOCKET_URL,
            token=self.tokens.token,
            on_command=self._handle_command,
        )

        async def _wait_socket():
            while not self._socket.connected:
                try:
                    await self._socket.connect()
                except Exception as e:
                    logger.warning('Socket connect failed, retry in 15s: %s', e)
                    await asyncio.sleep(15)

        self._tasks.append(asyncio.create_task(_wait_socket()))
        self._tasks.append(asyncio.create_task(heartbeat_loop(
            self._socket,
            self.tokens.token,
        )))

    async def _start_background_tasks(self) -> None:
        self._tasks.append(asyncio.create_task(self._token_refresh_loop()))
        self._tasks.append(asyncio.create_task(self._audit_flush_loop()))
        self._tasks.append(asyncio.create_task(self._focus_sample_loop()))
        self._tasks.append(asyncio.create_task(self._hardware_refresh_loop()))

    # ------------------------------------------------------------------
    # Token refresh loop
    # ------------------------------------------------------------------

    async def _token_refresh_loop(self) -> None:
        while self._running:
            await asyncio.sleep(config.TOKEN_REFRESH_INTERVAL)
            if not self._running:
                break
            new_token = check_and_refresh_token()
            if new_token and new_token != self.tokens.token:
                self.tokens.token = new_token
                logger.info('Token refreshed in service loop')

    async def _audit_flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(90)
            if not self._running:
                break
            await asyncio.to_thread(flush_audit_buffer, self.tokens.token)

    async def _focus_sample_loop(self) -> None:
        while self._running:
            await asyncio.sleep(180)
            if not self._running:
                break
            exe = await asyncio.to_thread(get_foreground_exe_path)
            if exe and exe != self._last_exe:
                self._last_exe = exe
                try:
                    from agent.core.audit_client import record_app_focus
                    record_app_focus(exe)
                except Exception:
                    pass

    async def _hardware_refresh_loop(self) -> None:
        """Kirim hardware snapshot lengkap ke server setiap 1 jam."""
        while self._running:
            await asyncio.sleep(3600)
            if not self._running:
                break
            await self._send_initial_hardware()

    # ------------------------------------------------------------------
    # Command dispatcher
    # ------------------------------------------------------------------

    async def _handle_command(self, data: dict) -> None:
        cmd = data.get('type') or data.get('command')
        logger.debug('Handling command: %s', cmd)
        if cmd == 'update_pin':
            from agent.auth.token_store import store_pin_hash
            from datetime import datetime, timezone
            pin_hash = data['pin_hash'].encode('latin-1')
            valid_until = datetime.fromisoformat(data['valid_until']).replace(tzinfo=timezone.utc)
            store_pin_hash(pin_hash, valid_until)
            logger.info('PIN updated (valid until %s)', valid_until)
        elif cmd == 'reboot':
            logger.info('Remote reboot command received')
            await self.stop()
            _trigger_reboot()
        elif cmd == 'shutdown':
            logger.info('Remote shutdown command received')
            await self.stop()
            _trigger_shutdown()
        elif cmd == 'collect_hardware':
            await self._send_initial_hardware()
            logger.info('Hardware snapshot sent on-demand')
        else:
            logger.debug('Unknown command: %s', cmd)

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        logger.info('AgentService stopping')
        self._running = False
        try:
            flush_audit_buffer(self.tokens.token)
        except Exception:
            pass

        for task in self._tasks:
            task.cancel()

        if self._socket:
            try:
                await self._socket.disconnect()
            except Exception:
                pass

        logger.info('AgentService stopped')


def _trigger_reboot() -> None:
    try:
        import subprocess
        subprocess.run(['shutdown', '/r', '/t', '10', '/c', 'Remote reboot from Bosowa Portal'],
                       capture_output=True)
    except Exception as e:
        logger.error('Reboot command failed: %s', e)


def _trigger_shutdown() -> None:
    try:
        import subprocess
        subprocess.run(['shutdown', '/s', '/t', '10', '/c', 'Remote shutdown from Bosowa Portal'],
                       capture_output=True)
    except Exception as e:
        logger.error('Shutdown command failed: %s', e)