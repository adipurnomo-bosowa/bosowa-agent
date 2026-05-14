# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Bosowa Agent .exe build."""

import sys
import os
from pathlib import Path

block_cipher = None

# Absolute path to agent source (works both in dev and frozen)
ROOT = Path(r'C:\Users\adipu\Documents\WebApp\portal_bosowa\bosowa-agent').absolute()
AGENT_SRC = ROOT / 'agent'
sys.path.insert(0, str(AGENT_SRC))

a = Analysis(
    [str(AGENT_SRC / '__main__.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Bundle lockscreen branding assets
        (str(ROOT.parent / 'PORTAL.png'), 'assets'),
        # Software whitelist CSV
        (str(ROOT / 'config' / 'whitelist.csv'), 'config'),
    ],
    hiddenimports=[
        # Windows WMI
        'wmi',
        'win32api',
        'win32con',
        'win32security',
        'pywintypes',
        # PyQt5
        'PyQt5',
        'PyQt5.QtWidgets',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        # Socket.IO / engine.io
        'socketio',
        'engineio',
        'engineio.async_drivers',
        'engineio.payload',
        'aiohttp',
        # Cryptography
        'cryptography',
        'cryptography.fernet',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.ciphers',
        # Keyring
        'keyring',
        'keyring.backends',
        'keyring.backends.Windows',
        # Other stdlib (needed at frozen import time)
        'bcrypt',
        'psutil',
        'requests',
        'certifi',
        'uuid',
        'asyncio',
        'logging',
        'json',
        'threading',
        'ctypes',
        'ctypes.wintypes',
        'subprocess',
        'hashlib',
        'winreg',
        # pyautogui / PIL
        'pyautogui',
        'PIL',
        # Agent entrypoint + commands
        'agent.__main__',
        'agent.core.commands',
        'agent.core.commands.screenshot',
        'agent.core.commands.processes',
        'agent.core.commands.network',
        'agent.core.commands.usb_control',
        'agent.core.commands.system_control',
        'agent.core.commands.battery',
        'agent.core.commands.software',
        'agent.core.commands.hardware_info',
        'agent.core.commands.website_control',
        'agent.core.commands.update_agent',
        'agent.core.commands.software_install',
        # Tray + ticketing
        'agent.ui',
        'agent.ui.tray_app',
        'agent.api',
        'agent.api.tickets',
        # Software compliance
        'agent.core.software_compliance',
        # CSV
        'csv',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'tkinter',
        'test',
        'unittest',
        'pytest',
    ],
    cipher=block_cipher,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    convert_icon=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BosowAgent',
    debug=False,
    strip=False,
    upx=True,
    console=False,          # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # set icon='build/icon.ico' when available
    uac_admin=True,         # request elevated privileges
    uac_uiaccess=False,
    manifest=None,          # use default manifest
)