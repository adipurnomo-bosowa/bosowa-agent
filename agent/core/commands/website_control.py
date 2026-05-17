"""BLOCK_WEBSITE, UNBLOCK_WEBSITE, GET_BLOCKED_SITES commands."""
from __future__ import annotations

import logging
import os
import re
import subprocess

from agent.utils.proc import NO_WINDOW

logger = logging.getLogger(__name__)


def _flush_dns() -> None:
    """Run ipconfig /flushdns without opening a console window."""
    try:
        subprocess.run(
            ['ipconfig', '/flushdns'],
            capture_output=True,
            timeout=10,
            creationflags=NO_WINDOW,
        )
    except Exception as e:
        logger.debug('flushdns failed: %s', e)

HOSTS_FILE = r'C:\Windows\System32\drivers\etc\hosts'
MARKER_START = '# BOSOWA_PORTAL_START'
MARKER_END = '# BOSOWA_PORTAL_END'


def _read_hosts() -> str:
    with open(HOSTS_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def _write_hosts(content: str) -> None:
    with open(HOSTS_FILE, 'w', encoding='utf-8') as f:
        f.write(content)


def _get_bosowa_blocked() -> list[str]:
    content = _read_hosts()
    pattern = rf'{re.escape(MARKER_START)}(.*?){re.escape(MARKER_END)}'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return []
    domains = []
    for line in match.group(1).strip().splitlines():
        line = line.strip()
        if line.startswith('127.0.0.1'):
            parts = line.split()
            if len(parts) > 1:
                domains.append(parts[1])
    return domains


async def handle_block_website(payload: dict) -> dict:
    """Block a domain via hosts file. Requires admin privileges."""
    domain = payload.get('domain', '').strip().lower()
    if not domain:
        raise ValueError('domain is required')

    blocked = _get_bosowa_blocked()
    if domain in blocked:
        return {'domain': domain, 'action': 'block', 'note': 'already blocked'}

    blocked.append(domain)
    new_section = (
        f'\n{MARKER_START}\n'
        + '\n'.join(f'127.0.0.1 {d}' for d in blocked)
        + f'\n{MARKER_END}\n'
    )

    content = _read_hosts()
    pattern = rf'\n?{re.escape(MARKER_START)}.*?{re.escape(MARKER_END)}\n?'
    content = re.sub(pattern, '', content, flags=re.DOTALL)
    content += new_section

    try:
        _write_hosts(content)
        _flush_dns()
    except PermissionError:
        raise PermissionError('Admin privileges required to modify hosts file')

    logger.info('Blocked domain: %s', domain)
    return {'domain': domain, 'action': 'blocked', 'total_blocked': len(blocked)}


async def handle_unblock_website(payload: dict) -> dict:
    """Unblock a domain via hosts file."""
    domain = payload.get('domain', '').strip().lower()
    blocked = _get_bosowa_blocked()

    if domain not in blocked:
        return {'domain': domain, 'action': 'unblock', 'note': 'not blocked'}

    blocked.remove(domain)
    content = _read_hosts()
    pattern = rf'\n?{re.escape(MARKER_START)}.*?{re.escape(MARKER_END)}\n?'
    content = re.sub(pattern, '', content, flags=re.DOTALL)

    if blocked:
        new_section = (
            f'\n{MARKER_START}\n'
            + '\n'.join(f'127.0.0.1 {d}' for d in blocked)
            + f'\n{MARKER_END}\n'
        )
        content += new_section

    try:
        _write_hosts(content)
        _flush_dns()
    except PermissionError:
        raise PermissionError('Admin privileges required to modify hosts file')

    logger.info('Unblocked domain: %s', domain)
    return {'domain': domain, 'action': 'unblocked', 'total_blocked': len(blocked)}


async def handle_get_blocked_sites(payload: dict) -> dict:
    """Return list of domains blocked by Bosowa Portal."""
    blocked = _get_bosowa_blocked()
    return {'blocked_domains': blocked}
