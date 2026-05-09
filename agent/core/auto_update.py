"""Agent auto-update — cek versi di server, download dan ganti exe jika lebih baru."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import certifi
import requests

from agent import config
from agent.utils.logger import logger


def is_newer_version(server_version: str, current_version: str) -> bool:
    """Return True jika server_version > current_version (format major.minor.patch)."""
    def parse(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.strip().split('.')[:3])
        except ValueError:
            return (0, 0, 0)
    return parse(server_version) > parse(current_version)


def fetch_latest_version(token: str) -> Optional[dict]:
    """Fetch versi terbaru dari portal. Return None jika gagal."""
    try:
        r = requests.get(
            f'{config.API_BASE}/agent/version',
            headers={'Authorization': f'Bearer {token}'},
            timeout=10,
            verify=certifi.where(),
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning('fetch_latest_version failed: %s', e)
        return None


def download_update(download_url: str, token: str) -> Optional[Path]:
    """Download exe baru ke AGENT_DIR/update/. Return path, None jika gagal."""
    update_dir = config.AGENT_DIR / 'update'
    update_dir.mkdir(parents=True, exist_ok=True)
    target = update_dir / 'BosowAgent_new.exe'

    try:
        r = requests.get(
            download_url,
            headers={'Authorization': f'Bearer {token}'},
            stream=True,
            timeout=120,
            verify=certifi.where(),
        )
        r.raise_for_status()
        with open(target, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info('Update downloaded to %s (%d bytes)', target, target.stat().st_size)
        return target
    except Exception as e:
        logger.warning('download_update failed: %s', e)
        return None


def apply_update_and_relaunch(new_exe_path: Path) -> None:
    """
    Ganti exe saat ini dengan yang baru via PowerShell helper script, lalu exit.
    Task Scheduler (ONLOGON) akan otomatis relaunch agent.
    Di dev mode (tidak frozen), hanya log dan skip replacement.
    """
    if not getattr(sys, 'frozen', False):
        logger.info('Dev mode — skip exe replacement. New exe: %s', new_exe_path)
        return

    current_exe = Path(sys.executable)
    ps_script = config.AGENT_DIR / 'do_update.ps1'
    ps_content = (
        f'Start-Sleep -Seconds 4\n'
        f'Copy-Item -Force "{new_exe_path}" "{current_exe}"\n'
        f'Start-Process "{current_exe}"\n'
    )
    ps_script.write_text(ps_content, encoding='utf-8')

    try:
        subprocess.Popen(
            ['powershell', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(ps_script)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        logger.info('Update script launched — agent exiting for replacement')
        os._exit(0)
    except Exception as e:
        logger.error('apply_update_and_relaunch failed: %s', e)
