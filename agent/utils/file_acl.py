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


def _deny_user_write(path: str) -> bool:
    """Deny FILE_GENERIC_WRITE and DELETE for the 'Users' group on *path*.

    Returns True on success, False if not on Windows, missing pywin32, or no admin.
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

        # Add DENY ACE for Users: no write, no delete, no change permissions
        deny_mask = con.FILE_GENERIC_WRITE | con.DELETE | con.WRITE_DAC | con.WRITE_OWNER
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
    """Apply deny-write ACL to all agent data directories."""
    from agent import config
    dirs = [str(config.AGENT_DIR)]
    # Also protect the exe directory when frozen
    import os, sys, pathlib
    if getattr(sys, 'frozen', False):
        exe_dir = pathlib.Path(sys.executable).parent
        if exe_dir.exists():
            dirs.append(str(exe_dir))

    for d in dirs:
        try:
            if os.path.isdir(d):
                _deny_user_write(d)
        except Exception as e:
            logger.debug('protect_agent_directories: %s -> %s', d, e)
