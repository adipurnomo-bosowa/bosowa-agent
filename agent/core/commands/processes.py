"""GET_PROCESSES and KILL_PROCESS commands."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PROTECTED_PROCESSES = {
    'System', 'smss.exe', 'csrss.exe', 'wininit.exe',
    'lsass.exe', 'svchost.exe', 'services.exe',
    'BosowAgent.exe', 'BosowAgent', 'python.exe', 'pythonw.exe',
}


async def handle_get_processes(payload: dict) -> dict:
    """Return top processes sorted by memory usage."""
    import psutil
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent',
                                      'memory_percent', 'status', 'username']):
        try:
            info = proc.info
            if info.get('memory_percent') and info['memory_percent'] > 0.01:
                processes.append({
                    'pid': info['pid'],
                    'name': info['name'],
                    'cpu_percent': round(info['cpu_percent'] or 0, 2),
                    'memory_percent': round(info['memory_percent'] or 0, 2),
                    'status': info['status'],
                    'username': info['username'],
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    processes.sort(key=lambda x: x['memory_percent'], reverse=True)
    return {'processes': processes, 'total': len(processes)}


async def handle_kill_process(payload: dict) -> dict:
    """Kill a process by PID with protected-process guard."""
    import psutil
    pid = payload.get('pid')
    if not pid:
        raise ValueError('pid is required')

    try:
        proc = psutil.Process(int(pid))
    except psutil.NoSuchProcess:
        raise ValueError(f'Process {pid} not found')

    proc_name = proc.name()
    if proc_name in PROTECTED_PROCESSES:
        raise PermissionError(f'Cannot kill protected process: {proc_name}')

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except psutil.TimeoutExpired:
        proc.kill()

    logger.info('Killed process %s (pid=%s)', proc_name, pid)
    return {'pid': pid, 'name': proc_name, 'killed': True}
