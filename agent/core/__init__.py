"""Core module for Bosowa Agent."""
from agent.core.hardware import get_hardware_snapshot
from agent.core.socket_client import AgentSocketClient
from agent.core.heartbeat import heartbeat_loop
from agent.core.uptime import (
    send_power_on,
    send_power_off,
    get_last_power_on,
    clear_power_on,
    register_shutdown_hooks,
)

__all__ = [
    'get_hardware_snapshot',
    'AgentSocketClient',
    'heartbeat_loop',
    'send_power_on',
    'send_power_off',
    'get_last_power_on',
    'clear_power_on',
    'register_shutdown_hooks',
]