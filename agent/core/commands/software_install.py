"""Remote software install command handler."""
from __future__ import annotations

import asyncio
import os
import pathlib
import subprocess
import tempfile
import urllib.parse

from agent.utils.logger import logger

MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
DOWNLOAD_TIMEOUT = 120                   # seconds for HTTP connection + first byte
INSTALL_TIMEOUT = 300                    # 5-minute hard timeout for the installer process


def _build_install_cmd(path: str, extra_args: list[str]) -> list[str]:
    """Return the command list to run the installer silently."""
    lower = path.lower()
    if lower.endswith('.msi'):
        log_path = os.path.join(tempfile.gettempdir(), 'bosow_install.log')
        cmd = ['msiexec', '/i', path, '/qn', '/norestart', f'/l*v{log_path}']
    else:
        # EXE: common NSIS / InnoSetup / generic silent flags
        cmd = [path, '/S', '/silent', '/quiet', '/norestart']
    if extra_args:
        # User-supplied override replaces automatic silent flags entirely
        cmd = [cmd[0]] + extra_args
    return cmd


def _run_installer_sync(path: str, extra_args: list[str]) -> tuple[int, str]:
    """Run installer synchronously. Returns (exit_code, combined output)."""
    cmd = _build_install_cmd(path, extra_args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        raise TimeoutError('Installer tidak selesai dalam 5 menit')
    except PermissionError:
        raise PermissionError('Akses ditolak: jalankan agent sebagai Administrator untuk install software')


async def handle_install_software(payload: dict) -> dict:
    """Download and silently install software from a URL.

    Payload keys:
        url  (str)            : https URL of the .exe or .msi installer
        name (str, optional)  : display name for logging/result
        args (str, optional)  : override silent install flags (space-separated)
    """
    import requests
    import certifi

    url: str = str(payload.get('url', '')).strip()
    name: str = str(payload.get('name') or '').strip() or 'software'
    args_override: str = str(payload.get('args') or '').strip()

    if not url:
        raise ValueError('url diperlukan')

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError('URL harus menggunakan http atau https')

    url_path = parsed.path.rstrip('/')
    filename = pathlib.PurePosixPath(url_path).name or 'installer'
    if not (filename.lower().endswith('.exe') or filename.lower().endswith('.msi')):
        filename += '.exe'

    tmp_dir = pathlib.Path(tempfile.gettempdir()) / 'bosow_install'
    tmp_dir.mkdir(exist_ok=True)
    tmp_file = tmp_dir / filename

    logger.info('INSTALL_SOFTWARE: downloading %s → %s', url, tmp_file)

    def _download() -> int:
        resp = requests.get(
            url,
            stream=True,
            timeout=DOWNLOAD_TIMEOUT,
            verify=certifi.where(),
        )
        resp.raise_for_status()

        content_length = int(resp.headers.get('content-length', 0))
        if content_length > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f'File terlalu besar: {content_length // 1024 // 1024} MB (max 500 MB)'
            )

        downloaded = 0
        with open(tmp_file, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise ValueError('File terlalu besar: melebihi 500 MB')
                    f.write(chunk)
        return downloaded

    downloaded = await asyncio.to_thread(_download)
    logger.info('INSTALL_SOFTWARE: %d bytes downloaded, starting installer', downloaded)

    extra_args = args_override.split() if args_override else []
    exit_code, output = await asyncio.to_thread(_run_installer_sync, str(tmp_file), extra_args)

    try:
        tmp_file.unlink(missing_ok=True)
    except Exception:
        pass

    # 0 = success, 3010 = success + reboot required (Windows standard)
    success = exit_code in (0, 3010)
    reboot_required = exit_code == 3010

    logger.info('INSTALL_SOFTWARE: %s exit_code=%d reboot=%s', name, exit_code, reboot_required)

    if not success:
        raise RuntimeError(
            f'Installer selesai dengan kode error {exit_code}'
            + (f': {output[:400]}' if output else '')
        )

    return {
        'name': name,
        'exit_code': exit_code,
        'reboot_required': reboot_required,
        'output': output[:1000],
    }
