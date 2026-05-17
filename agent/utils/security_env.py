"""Detect host security posture that can break the agent's update / persistence path.

Run once at startup; results stored in agent_state for the UI and logged so
support can debug "agent disappeared after update" cases.

Why this matters: PyInstaller exes are unsigned and frequently flagged by
Defender, third-party AV, and (on Win11 24H2 fresh installs) Smart App Control.
SAC in Enforce mode silently blocks unsigned PE files even with admin rights —
no popup, no Event Log entry visible to the user — so we must surface this
ourselves.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agent.utils.logger import logger
from agent.utils.proc import NO_WINDOW


_SAC_MODES = {0: 'off', 1: 'eval', 2: 'on'}


def detect_smart_app_control() -> str:
    """Return 'off' | 'eval' | 'on' | 'unknown'. Reads the SAC PolicyMode value.

    Windows 11 24H2+ exposes the user's choice at:
        HKLM\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy   VerifiedAndReputablePolicyState
    The same value (0/1/2) is mirrored under several keys; CI\\Policy is the
    authoritative one. Older Windows versions don't have it → 'unknown'.
    """
    if sys.platform != 'win32':
        return 'off'
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'SYSTEM\CurrentControlSet\Control\CI\Policy',
            0,
            winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, 'VerifiedAndReputablePolicyState')
            return _SAC_MODES.get(int(value), 'unknown')
    except FileNotFoundError:
        return 'off'  # key absent on pre-24H2 → SAC not present
    except Exception as e:
        logger.debug('SAC detection error: %s', e)
        return 'unknown'


def detect_defender_exclusion_state(paths: list[str], procs: list[str]) -> bool | None:
    """Return True if all paths+processes are in Defender's exclusion list.

    Returns None if Defender / PowerShell is unavailable (don't conflate with False).
    Uses Get-MpPreference; runs hidden.
    """
    if sys.platform != 'win32':
        return None
    if not paths and not procs:
        return True
    try:
        # Build a script that prints two lines: paths|... and procs|...
        ps = (
            '$ErrorActionPreference="SilentlyContinue";'
            '$pref = Get-MpPreference;'
            '$ep = @() + $pref.ExclusionPath;'
            '$xp = @() + $pref.ExclusionProcess;'
            'Write-Output ("PATHS=" + ($ep -join "|"));'
            'Write-Output ("PROCS=" + ($xp -join "|"));'
        )
        r = subprocess.run(
            ['powershell', '-NonInteractive', '-NoProfile', '-Command', ps],
            capture_output=True, text=True, timeout=15,
            creationflags=NO_WINDOW,
        )
        if r.returncode != 0:
            return None
        excluded_paths: set[str] = set()
        excluded_procs: set[str] = set()
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith('PATHS='):
                excluded_paths = {p.lower() for p in line[6:].split('|') if p}
            elif line.startswith('PROCS='):
                excluded_procs = {p.lower() for p in line[6:].split('|') if p}

        def _norm(s: str) -> str:
            return str(Path(s)).lower()

        for p in paths:
            if _norm(p) not in {_norm(x) for x in excluded_paths}:
                return False
        for proc in procs:
            if _norm(proc) not in {_norm(x) for x in excluded_procs}:
                return False
        return True
    except Exception as e:
        logger.debug('Defender exclusion check failed: %s', e)
        return None


def snapshot_security_env() -> dict:
    """Detect SAC + Defender state, store in agent_state, return summary."""
    from agent.core import agent_state
    from agent import config

    sac = detect_smart_app_control()
    exe = sys.executable if getattr(sys, 'frozen', False) else ''
    paths = [str(config.AGENT_DIR), str(config.AGENT_DIR / 'update')]
    procs: list[str] = [exe] if exe else []
    defender_ok = detect_defender_exclusion_state(paths, procs)

    agent_state.set_environment(sac_mode=sac, defender_exclusion_ok=defender_ok)

    if sac == 'on':
        logger.warning(
            'Smart App Control is ENFORCING. Unsigned BosowAgent.exe may be '
            'silently blocked from launching or updating. Recommend disabling '
            'SAC (one-way, requires Windows reset) OR code-signing the binary.'
        )
    elif sac == 'eval':
        logger.info('Smart App Control in EVALUATION mode — may auto-enable later.')

    if defender_ok is False:
        logger.warning(
            'Defender exclusions NOT registered for %s / %s. Self-update may '
            'be blocked when Defender rescans BosowAgent_new.exe.', paths, procs,
        )

    return {'sac_mode': sac, 'defender_exclusion_ok': defender_ok}


__all__ = ['detect_smart_app_control', 'detect_defender_exclusion_state', 'snapshot_security_env']
