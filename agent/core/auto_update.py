"""Agent auto-update — cek versi di server, download dan ganti exe jika lebih baru."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

import certifi
import requests

from agent import config
from agent.utils.logger import logger
from agent.utils.update_exit_marker import write_update_replace_marker


def is_newer_version(server_version: str, current_version: str) -> bool:
    """Return True jika server_version > current_version (format major.minor.patch; prefix v/V diabaikan)."""
    def parse(v: str) -> tuple[int, int, int]:
        s = (v or '').strip().lstrip('vV')
        parts = (s.split('.') + ['0', '0', '0'])[:3]
        out: list[int] = []
        for p in parts:
            num = ''
            for ch in p:
                if ch.isdigit():
                    num += ch
                else:
                    break
            try:
                out.append(int(num) if num else 0)
            except ValueError:
                out.append(0)
        return (out[0], out[1], out[2])

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
    return download_update_with_progress(download_url, token, lambda _: None)


def _is_public_exe_download(url: str) -> bool:
    """Static /downloads/ URLs are usually served without Bearer auth; some proxies reject unknown auth."""
    u = (url or '').lower()
    return '/downloads/' in u or u.endswith('.exe')


def download_update_with_progress(
    download_url: str,
    token: str,
    progress_cb: Callable[[int], None],
) -> Optional[Path]:
    """Download exe baru dengan laporan progress setiap ~5%. Return path, None jika gagal."""
    update_dir = config.AGENT_DIR / 'update'
    update_dir.mkdir(parents=True, exist_ok=True)
    target = update_dir / 'BosowAgent_new.exe'

    try:
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        r = requests.get(
            download_url,
            headers=headers,
            stream=True,
            timeout=120,
            verify=certifi.where(),
        )
        if r.status_code in (401, 403) and token and _is_public_exe_download(download_url):
            r.close()
            logger.info('Download retry without Authorization (public exe URL)')
            r = requests.get(
                download_url,
                stream=True,
                timeout=120,
                verify=certifi.where(),
            )
        r.raise_for_status()
        total = int(r.headers.get('Content-Length', 0))
        downloaded = 0
        last_reported = -1

        with open(target, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = min(99, int(downloaded * 100 / total))
                    if pct >= last_reported + 5:
                        progress_cb(pct)
                        last_reported = pct

        progress_cb(100)
        logger.info('Update downloaded to %s (%d bytes)', target, target.stat().st_size)
        return target
    except Exception as e:
        logger.warning('download_update_with_progress failed: %s', e)
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
    # Root-cause: the watchdog is ALSO BosowAgent.exe (same file).
    # As long as the watchdog process is alive it holds the exe file open,
    # so Copy-Item always fails — even with many retries.
    # Fix: force-kill ALL BosowAgent instances (main already exited via os._exit(0),
    # but watchdog is still running) before attempting the copy.
    # Only launch the new binary when the copy is confirmed successful.
    # PowerShell double-quoted strings treat backslash as literal (NOT escape char).
    # Do NOT escape backslashes — str(Path) on Windows already gives single backslashes.
    log_path = config.AGENT_DIR / 'update_debug.log'
    ps_content = f"""\
$src = "{new_exe_path}"
$dst = "{current_exe}"
$backup = "$dst.old"
$log = "{log_path}"

function Log($msg) {{ Add-Content $log "$(Get-Date -Format 'HH:mm:ss') $msg" }}

Log "=== UPDATE START src=$src dst=$dst ==="
Start-Sleep -Seconds 2

# Kill ALL BosowAgent instances (main + watchdog) to release exe file lock
Get-Process -Name "BosowAgent" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Log "Killed BosowAgent processes, waiting..."
Start-Sleep -Seconds 3

Log "src_exists=$(Test-Path $src)  dst_exists=$(Test-Path $dst)"

$replaced = $false

# Strategy 1: rename old -> .old, copy new -> original path (avoids overwrite lock)
try {{
    if (Test-Path $backup) {{ Remove-Item $backup -Force -ErrorAction SilentlyContinue }}
    if (Test-Path $dst)    {{ Rename-Item $dst $backup -Force -ErrorAction Stop }}
    Copy-Item -Force $src $dst -ErrorAction Stop
    $replaced = $true
    Log "Strategy-1 OK (rename+copy)"
}} catch {{
    Log "Strategy-1 FAIL: $_"
    # Rollback: restore backup if dst is missing
    if (-not (Test-Path $dst) -and (Test-Path $backup)) {{
        Rename-Item $backup $dst -Force -ErrorAction SilentlyContinue
    }}
}}

# Strategy 2: direct overwrite fallback (8 attempts x 2 s)
if (-not $replaced) {{
    $attempt = 0
    while ($attempt -lt 8 -and -not $replaced) {{
        $attempt++
        try {{
            Copy-Item -Force $src $dst -ErrorAction Stop
            $replaced = $true
            Log "Strategy-2 OK attempt=$attempt"
        }} catch {{
            Log "Strategy-2 attempt=$attempt FAIL: $_"
            Start-Sleep -Seconds 2
        }}
    }}
}}

if ($replaced) {{
    Log "Launching new exe $dst"
    Start-Process $dst
}} else {{
    Log "All copy strategies failed — relaunching existing binary"
    if (Test-Path $dst)    {{ Start-Process $dst }}
    elseif (Test-Path $backup) {{ Rename-Item $backup $dst -Force -ErrorAction SilentlyContinue; Start-Process $dst }}
}}

Log "=== UPDATE DONE replaced=$replaced ==="
"""
    ps_script.write_text(ps_content, encoding='utf-8')

    try:
        subprocess.Popen(
            ['powershell', '-NonInteractive', '-WindowStyle', 'Hidden',
             '-ExecutionPolicy', 'Bypass', '-File', str(ps_script)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        logger.info('Update script launched — agent exiting for replacement')
        write_update_replace_marker()
        os._exit(0)
    except Exception as e:
        logger.error('apply_update_and_relaunch failed: %s', e)
