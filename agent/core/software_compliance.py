"""Software compliance checker — compares installed programs against whitelist."""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import NamedTuple

from agent.utils.logger import logger


class ComplianceResult(NamedTuple):
    status: str           # 'OK', 'WARN', 'ERROR'
    score: float          # 0-100
    matched: list[str]    # programs in whitelist
    unmatched: list[str]  # programs NOT in whitelist
    total: int


def _get_whitelist_path() -> Path:
    base = getattr(sys, '_MEIPASS', None)
    if base:
        return Path(base) / 'config' / 'whitelist.csv'
    return Path(__file__).resolve().parents[2] / 'config' / 'whitelist.csv'


def load_whitelist() -> list[str]:
    """Return lowercase whitelist names from whitelist.csv."""
    path = _get_whitelist_path()
    if not path.exists():
        logger.warning('whitelist.csv not found at %s', path)
        return []
    names: list[str] = []
    try:
        with open(path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                name = (row.get('name') or '').strip().lower()
                if name:
                    names.append(name)
    except Exception as e:
        logger.error('Failed to read whitelist.csv: %s', e)
    return names


def _scan_registry_key(hive: int, path: str) -> list[str]:
    """Scan one Uninstall registry key, return DisplayName values."""
    import winreg
    names: list[str] = []
    try:
        key = winreg.OpenKey(hive, path)
        i = 0
        while True:
            try:
                sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                try:
                    name, _ = winreg.QueryValueEx(sub, 'DisplayName')
                    if isinstance(name, str) and name.strip():
                        names.append(name.strip())
                except FileNotFoundError:
                    pass
                finally:
                    sub.Close()
                i += 1
            except OSError:
                break
        key.Close()
    except Exception as e:
        logger.debug('Registry scan skipped %s: %s', path, e)
    return names


def get_installed_programs() -> list[str]:
    """Return deduplicated installed program names from Windows registry."""
    if sys.platform != 'win32':
        return []
    import winreg
    _KEY32 = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    _KEY64 = r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    seen: set[str] = set()
    result: list[str] = []
    for hive, path in [
        (winreg.HKEY_LOCAL_MACHINE, _KEY32),
        (winreg.HKEY_LOCAL_MACHINE, _KEY64),
        (winreg.HKEY_CURRENT_USER, _KEY32),
    ]:
        for name in _scan_registry_key(hive, path):
            lower = name.lower()
            if lower not in seen:
                seen.add(lower)
                result.append(name)
    return result


def check_compliance(whitelist: list[str] | None = None) -> ComplianceResult:
    """
    Compare installed programs against whitelist.
    Matching is case-insensitive partial string (either direction).
    Status: OK >= 80%, WARN 60-79%, ERROR < 60%.
    """
    if whitelist is None:
        whitelist = load_whitelist()

    installed = get_installed_programs()
    if not installed:
        return ComplianceResult('WARN', 0.0, [], [], 0)

    matched: list[str] = []
    unmatched: list[str] = []
    for prog in installed:
        prog_lower = prog.lower()
        hit = any(w in prog_lower or prog_lower in w for w in whitelist)
        (matched if hit else unmatched).append(prog)

    total = len(installed)
    score = len(matched) / total * 100 if total else 0.0
    status = 'OK' if score >= 80 else ('WARN' if score >= 60 else 'ERROR')
    return ComplianceResult(status=status, score=score, matched=matched,
                            unmatched=unmatched, total=total)
