"""Subprocess helpers — primary purpose: never flash a console window.

When the agent runs as a frozen, --noconsole PyInstaller exe, any child
process spawned without an explicit `CREATE_NO_WINDOW` flag inherits no
console and Windows allocates a fresh CONHOST window for it. Even with
`-WindowStyle Hidden` on the child command line, the host window briefly
flashes before the child can hide itself.

Use `NO_WINDOW` for every `subprocess.run/Popen/check_output/check_call`
that runs synchronously (capture_output or text=True), and
`DETACHED_NO_WINDOW` for any process we fully detach from.
"""
from __future__ import annotations

import subprocess
import sys

# On Windows, CREATE_NO_WINDOW (0x08000000) tells the child it must not allocate
# a console. Safe for synchronous calls (capture_output/text). 0 on other OSes.
NO_WINDOW: int = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0

# For fully-detached background children (e.g. update PS1, relaunch).
# DETACHED_PROCESS alone already prevents a console; we do NOT OR with
# CREATE_NO_WINDOW because the two flags are mutually exclusive in
# CreateProcess and the combination causes ERROR_INVALID_PARAMETER.
DETACHED_NO_WINDOW: int = 0
if sys.platform == 'win32':
    DETACHED_NO_WINDOW = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
    )


__all__ = ['NO_WINDOW', 'DETACHED_NO_WINDOW']
