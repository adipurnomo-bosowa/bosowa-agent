"""Handler UPDATE_AGENT — download dan apply update binary agent."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from agent import config
from agent.utils.logger import logger


async def handle_update_agent(
    payload: dict,
    emit_progress: Callable[..., Awaitable[None]],
    token: str,
) -> None:
    """Download versi terbaru dari server dan apply. Tidak return jika berhasil (os._exit)."""
    from agent.core.auto_update import (
        fetch_latest_version,
        is_newer_version,
        download_update_with_progress,
        apply_update_and_relaunch,
    )

    await emit_progress('checking', 0, 'Memeriksa versi terbaru...')

    info = await asyncio.to_thread(fetch_latest_version, token)
    if not info:
        await emit_progress('error', 0, 'Gagal mengambil informasi versi dari server')
        return

    server_version = info.get('version', '')
    download_url = info.get('download_url', '')

    if not is_newer_version(server_version, config.AGENT_VERSION):
        await emit_progress('done', 100, f'Agent sudah versi terbaru ({config.AGENT_VERSION})')
        return

    if not download_url:
        await emit_progress('error', 0, 'URL download tidak dikonfigurasi di server')
        return

    await emit_progress('downloading', 0, f'Mengunduh v{server_version}...')
    logger.info('UPDATE_AGENT: mulai download %s -> %s', config.AGENT_VERSION, server_version)

    loop = asyncio.get_running_loop()

    def progress_cb(pct: int) -> None:
        # Fire-and-forget: progress events are non-fatal if they fail
        asyncio.run_coroutine_threadsafe(
            emit_progress('downloading', pct, f'Mengunduh... {pct}%'),
            loop,
        )

    new_exe = await asyncio.to_thread(
        download_update_with_progress, download_url, token, progress_cb
    )

    if not new_exe:
        await emit_progress('error', 0, 'Download gagal. Periksa koneksi dan URL di server.')
        return

    await emit_progress(
        'restarting',
        99,
        f'Menyiapkan restart ke v{server_version}...',
        target_version=server_version,
    )
    # Beri waktu singkat supaya event sempat terkirim sebelum proses berhenti (frozen).
    await asyncio.sleep(0.4)

    await emit_progress('replacing', 100, 'Mengganti file agent...')
    apply_update_and_relaunch(new_exe)

    # Sampai sini hanya jika dev mode (tidak frozen)
    await emit_progress('done', 100, 'Update selesai (dev mode -- file tidak diganti)')
