"""REFRESH_UPDATE_SCRIPT command — rewrites do_update.ps1 with the current template."""
from __future__ import annotations


async def handle_refresh_update_script(payload: dict) -> dict:
    from agent.core.auto_update import write_update_ps1
    write_update_ps1()
    return {'message': 'update script refreshed'}
