"""Set Windows ACL on agent directories to prevent unauthorized deletion/modification.

Sets DACL so that standard Users cannot delete, write, or modify agent files.
SYSTEM and Administrators retain full control.

Requires: pywin32 (win32security module).
Must run as Administrator to modify DACL on Program Files or ProgramData folders.
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger('BosowAgent.file_acl')


def _is_relative_to(path: 'pathlib.Path', parent: 'pathlib.Path') -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _deny_user_write(path: str) -> bool:
    """Deny FILE_GENERIC_WRITE and DELETE for the 'Users' group on *path*.

    Returns True on success, False if not on Windows, missing pywin32, or no admin.
    Skips if an equivalent DENY ACE already exists (prevents duplicate accumulation).
    """
    if sys.platform != 'win32':
        return False
    try:
        import win32security
        import ntsecuritycon as con
        import pywintypes

        # Resolve SID for built-in Users group (S-1-5-32-545)
        users_sid = win32security.CreateWellKnownSid(
            win32security.WinBuiltinUsersSid, None
        )

        # Get current DACL
        sd = win32security.GetFileSecurity(path, win32security.DACL_SECURITY_INFORMATION)
        dacl = sd.GetSecurityDescriptorDacl()
        if dacl is None:
            dacl = win32security.ACL()

        deny_mask = con.FILE_GENERIC_WRITE | con.DELETE | con.WRITE_DAC | con.WRITE_OWNER

        # Skip if an equivalent DENY ACE already exists to prevent duplicates
        ace_count = dacl.GetAceCount()
        for i in range(ace_count):
            ace = dacl.GetAce(i)
            ace_type = ace[0][0]
            ace_sid = ace[2]
            # ACCESS_DENIED_ACE_TYPE = 1
            if ace_type == 1 and ace_sid == users_sid:
                logger.debug('ACL already set on %s — skipping', path)
                return True

        dacl.AddAccessDeniedAceEx(
            win32security.ACL_REVISION,
            con.OBJECT_INHERIT_ACE | con.CONTAINER_INHERIT_ACE,
            deny_mask,
            users_sid,
        )

        sd.SetSecurityDescriptorDacl(True, dacl, False)
        win32security.SetFileSecurity(path, win32security.DACL_SECURITY_INFORMATION, sd)
        logger.info('ACL set: Users denied write/delete on %s', path)
        return True
    except ImportError:
        logger.warning('pywin32 / ntsecuritycon not available — file ACL not set')
        return False
    except Exception as e:
        logger.warning('Failed to set ACL on %s: %s', path, e)
        return False


def protect_agent_directories() -> None:
    """Apply deny-write ACL to the exe installation directory only.

    AGENT_DIR (C:\\ProgramData\\BosowAgent) is intentionally excluded: the agent
    writes logs, token files, and PID files there at runtime. Denying writes to
    Users on that directory would immediately break the running process.
    Only the exe dir (C:\\Program Files\\BosowAgent) is hardened — that location
    is already under Administrators control and should never be written at runtime.
    """
    import os, sys, pathlib
    dirs: list[str] = []
    # Only protect the exe installation directory when running as a frozen build
    # AND only if the exe is under a production install path (Program Files / ProgramData).
    # This prevents the dev dist/ folder from getting a DENY ACE during development.
    if getattr(sys, 'frozen', False):
        exe_dir = pathlib.Path(sys.executable).parent
        production_roots = (
            pathlib.Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')),
            pathlib.Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')),
            pathlib.Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData')),
        )
        is_production = any(
            _is_relative_to(exe_dir, root) for root in production_roots
        )
        if is_production and exe_dir.exists():
            dirs.append(str(exe_dir))

    for d in dirs:
        try:
            if os.path.isdir(d):
                _deny_user_write(d)
        except Exception as e:
            logger.debug('protect_agent_directories: %s -> %s', d, e)
