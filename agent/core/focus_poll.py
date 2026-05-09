"""Sample foreground executable on Windows (lightweight usage signal)."""
from __future__ import annotations

import sys


def get_foreground_exe_path() -> str | None:
    if sys.platform != 'win32':
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(ctypes.sizeof(buf))
            if hasattr(kernel32, 'QueryFullProcessImageNameW'):
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    return buf.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None
    return None
