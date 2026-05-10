"""Package entry point — handles --watchdog flag before normal agent startup."""
from __future__ import annotations

import sys


def _parse_watchdog_flag() -> int | None:
    """Return main PID if --watchdog <pid> is present in argv, else None."""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == '--watchdog' and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except ValueError:
                return None
    return None


def main() -> None:
    main_pid = _parse_watchdog_flag()
    if main_pid is not None:
        from agent import config
        from agent.main import WATCHDOG_PID_FILE
        from agent.utils.watchdog import run_watchdog
        run_watchdog(main_pid, WATCHDOG_PID_FILE)
    else:
        from agent.main import main as agent_main
        agent_main()


if __name__ == '__main__':
    main()
