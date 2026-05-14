"""Persistent Socket.IO WebSocket client with auto-reconnect."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable, Awaitable

import certifi
import socketio

from agent import config
from agent.utils.logger import logger
from agent.core.commands import dispatch_command

# ---------------------------------------------------------------------------

CommandHandler = Callable[[dict], Awaitable[None]]


class AgentSocketClient:
    """Async Socket.IO client that auto-reconnects on network changes."""

    def __init__(
        self,
        server_url: str,
        token: str,
        on_command: CommandHandler | None = None,
    ):
        self.server_url = server_url
        self.token = token
        self._on_command = on_command
        self._heartbeat_queue: list[dict] = []

        self.sio = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=0,        # infinite
            reconnection_delay=5,
            reconnection_delay_max=60,
        )
        self._connected = False
        self._connect_done = asyncio.Event()
        self._setup_handlers()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _setup_handlers(self) -> None:
        sio = self.sio

        @sio.event
        async def connect():
            import platform
            logger.info('Socket.IO connected to %s', self.server_url)
            self._connected = True
            self._connect_done.set()
            # Join the default namespace
            await self.sio.emit('join_dashboard')

            # Reuse cached IP geolocation (refreshed by heartbeat loop).
            location = None
            try:
                from agent.core.geo import get_cached_location, fetch_ip_location
                location = get_cached_location()
                if location is None:
                    location = await asyncio.to_thread(fetch_ip_location)
            except Exception as e:
                logger.debug('Geo fetch on socket connect failed: %s', e)

            payload = {
                'token': self.token,
                'hostname': self._hostname(),
                'mac_address': self._mac(),
                'agent_version': config.AGENT_VERSION,
                'os': platform.platform(),
            }
            if location:
                payload['location'] = location
            await self.sio.emit('register_device', payload)
            # Flush queued heartbeats
            await self._flush_queue()
            # Sync PIN from server (handles case where PIN was set while device was offline)
            await self._sync_pin_from_server()

        @sio.event
        async def connect_error(data):
            logger.warning('Socket.IO connection error: %s', data)

        @sio.event
        async def disconnect():
            logger.warning('Socket.IO disconnected, will retry automatically')
            self._connected = False
            self._connect_done.clear()

        @sio.event
        async def reconnect():
            logger.info('Socket.IO reconnected')
            self._connected = True
            self._connect_done.set()

        @sio.on('command')
        async def on_command(data: dict):
            command_type = data.get('type', '')
            payload = data.get('payload', {})
            command_id = data.get('command_id', 'unknown')
            logger.info('Received command: %s (id=%s)', command_type, command_id)

            if command_type == 'UPDATE_AGENT':
                from agent.core.commands.update_agent import handle_update_agent

                async def emit_progress(stage: str, percent: int, message: str) -> None:
                    await self.sio.emit('update_progress', {
                        'mac_address': self._mac(),
                        'stage': stage,
                        'percent': percent,
                        'message': message,
                    })

                asyncio.create_task(handle_update_agent(payload, emit_progress, self.token))
                return  # do NOT emit command_result; agent will exit after apply

            result = await dispatch_command(command_type, payload, command_id)
            await self._emit_command_result({
                'command_id': command_id,
                'type': command_type,
                'success': result.get('success'),
                'data': result.get('data'),
                'error': result.get('error'),
                'timestamp': datetime.now(timezone.utc).isoformat(),
            })

        @sio.on('update_pin')
        async def on_update_pin(data: dict):
            logger.info('Received PIN update from server: %s', data)
            if self._on_command:
                await self._on_command({'type': 'update_pin', **data})

        @sio.on('unlock_device')
        async def on_unlock(data: dict):
            logger.debug('Received unlock signal: %s', data)

        @sio.on('force_logout')
        async def on_force_logout(data: dict):
            """Server pushes this when the user account is disabled or revoked."""
            logger.warning('force_logout from server: %s', data)
            from agent.auth.token_store import clear_all_credentials
            import os
            clear_all_credentials()
            os._exit(0)

        @sio.on('force_lock')
        async def on_force_lock(data: dict):
            """Server pushes this when an admin locks the device.

            Behaviour: clear stored token (so next login attempt requires server
            verification → server now returns 403 DEVICE_LOCKED), persist the admin
            reason, spawn a detached replacement process, then exit. Task Scheduler
            only covers logon — without an explicit relaunch the overlay may never
            return until the user logs off.
            """
            reason = (data or {}).get('reason') or 'Perangkat dikunci oleh IT Admin.'
            logger.warning('force_lock from server: %s', reason)
            try:
                from agent.auth.token_store import clear_all_credentials, store_lock_message
                from agent.utils.relaunch import relaunch_agent_process

                clear_all_credentials()
                try:
                    store_lock_message(reason)
                except Exception:
                    pass
                try:
                    relaunch_agent_process()
                except Exception as re:
                    logger.warning('force_lock: relaunch failed: %s', re)
            except Exception as e:
                logger.warning('force_lock: clear creds failed: %s', e)
            import os
            os._exit(0)

    async def _sync_pin_from_server(self) -> None:
        """Fetch PIN hash from server and persist locally. Handles offline-while-PIN-set case."""
        try:
            mac = self._mac()
            token = self.token

            def _fetch() -> dict | None:
                import requests
                resp = requests.get(
                    f'{config.API_BASE}/auth/agent-pin',
                    params={'device_mac': mac},
                    headers={'Authorization': f'Bearer {token}'},
                    timeout=10,
                )
                return resp.json() if resp.status_code == 200 else None

            data = await asyncio.to_thread(_fetch)
            if data and data.get('pin_hash') and data.get('valid_until'):
                from agent.auth.token_store import store_pin_hash
                from datetime import datetime, timezone as _tz
                pin_hash_bytes = data['pin_hash'].encode('latin-1')
                valid_until = datetime.fromisoformat(data['valid_until']).replace(tzinfo=_tz.utc)
                store_pin_hash(pin_hash_bytes, valid_until)
                logger.info('PIN synced from server on connect (valid until %s)', valid_until)
        except Exception as e:
            logger.debug('PIN sync on connect failed: %s', e)

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish WebSocket connection. Blocks until connected or fatal error."""
        try:
            await self.sio.connect(
                self.server_url,
                auth={'token': self.token},
                transports=['websocket'],
                wait_timeout=config.HTTP_TIMEOUT,
            )
            await self.sio.wait()
        except Exception as e:
            logger.error('Socket.IO connect failed: %s', e)
            raise

    async def disconnect(self) -> None:
        self._connected = False
        try:
            await self.sio.disconnect()
        except Exception as e:
            logger.debug('Socket.IO disconnect error: %s', e)

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Emit helpers
    # ------------------------------------------------------------------

    async def emit_heartbeat(self, payload: dict) -> None:
        """Emit heartbeat, queuing locally if disconnected."""
        if self._connected:
            try:
                await self.sio.emit('heartbeat', payload)
                return
            except Exception as e:
                logger.warning('Heartbeat emit failed: %s', e)
        self._heartbeat_queue.append(payload)
        if len(self._heartbeat_queue) > 100:
            self._heartbeat_queue.pop(0)
        logger.debug('Heartbeat queued (queue size=%d)', len(self._heartbeat_queue))

    async def emit_uptime(self, payload: dict) -> None:
        """Emit power-on/off event."""
        if self._connected:
            try:
                await self.sio.emit('uptime', payload)
                return
            except Exception as e:
                logger.warning('Uptime emit failed: %s', e)
        logger.debug('Uptime event queued: %s', payload)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _flush_queue(self) -> None:
        """Send any queued heartbeats after reconnection."""
        if not self._heartbeat_queue:
            return
        logger.info('Flushing %d queued heartbeats', len(self._heartbeat_queue))
        for payload in self._heartbeat_queue:
            try:
                await self.sio.emit('heartbeat', payload)
            except Exception:
                pass
        self._heartbeat_queue.clear()

    @staticmethod
    def _hostname() -> str:
        import platform
        return platform.node()

    async def _emit_command_result(self, payload: dict) -> None:
        """Send command result back to server."""
        try:
            payload['mac_address'] = self._mac()
            await self.sio.emit('command_result', payload)
        except Exception as e:
            logger.warning('command_result emit failed: %s', e)

    @staticmethod
    def _mac() -> str:
        import uuid
        mac = uuid.getnode()
        return ':'.join(f'{(mac >> i) & 0xFF:02X}' for i in range(0, 48, 8))