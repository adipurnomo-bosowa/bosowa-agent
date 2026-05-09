# Bosowa Agent

Windows endpoint management agent for Bosowa Group IT infrastructure.

## Overview

The Bosowa Agent is a Python-based background service that:
- Shows a full-screen lock screen overlay at startup
- Authenticates employees via direct login or web browser signal
- Sends hardware telemetry, heartbeats, and uptime events to Bosowa Portal
- Compiles to a standalone `.exe` via PyInstaller

## Project Structure

```
bosowa-agent/
├── agent/
│   ├── main.py                  # Entry point
│   ├── config.py                # Server URL, paths, constants
│   ├── auth/
│   │   ├── login.py             # Login orchestration (direct + web signal)
│   │   └── token_store.py       # Secure token storage (keyring + Fernet)
│   ├── overlay/
│   │   └── lockscreen.py        # PyQt5 full-screen lock screen
│   ├── core/
│   │   ├── agent_service.py     # Main service loop
│   │   ├── heartbeat.py        # 30s heartbeat sender
│   │   ├── hardware.py          # Hardware data collection
│   │   ├── socket_client.py     # Socket.IO WebSocket client
│   │   └── uptime.py           # Power-on/off tracking
│   └── utils/
│       ├── logger.py            # Rotating file logging
│       └── startup.py           # Windows startup registration
├── build/
│   └── bosowa_agent.spec        # PyInstaller spec
└── requirements.txt
```

## Requirements

- Python 3.11+
- Windows 10/11
- PyQt5, python-socketio, psutil, pywin32, keyring, cryptography, bcrypt

## Setup

```bash
cd bosowa-agent
pip install -r requirements.txt
```

## Development

```bash
python -m agent.main
```

Set `BOSOWA_DEV=1` environment variable for debug logging.

## Build .exe

```bash
pip install pyinstaller
pyinstaller build/bosowa_agent.spec --clean
```

Output: `bosowa-agent/dist/BosowAgent.exe`

## Server Configuration

Set the server URL (defaults to `https://portal.bosowagroup.co.id`):
```bash
set BOSOWA_SERVER_URL=https://your-server.com
```

## Security Notes

- Device tokens stored in Windows Credential Manager (keyring)
- Refresh tokens encrypted with machine-specific Fernet key
- PIN hashes encrypted at rest
- All HTTP requests use HTTPS with SSL verification
- No plaintext credential storage

## License

Proprietary — Bosowa Group