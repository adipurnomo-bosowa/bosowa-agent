"""SCREENSHOT command — capture desktop with 3 fallback methods."""
from __future__ import annotations

import base64
import io
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

from agent.utils.logger import logger

JPEG_QUALITY = 70
MAX_WIDTH = 1920


def _capture_via_mss() -> bytes:
    """Capture using mss - only works if agent has desktop session access."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        monitor = sct.monitors[0]
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def _capture_via_powershell() -> bytes:
    """Fallback: use .NET CopyFromScreen via PowerShell."""
    from PIL import Image

    base_dir = Path("C:/ProgramData/BosowAgent")
    base_dir.mkdir(parents=True, exist_ok=True)
    tmp = str(base_dir / f"ps_snap_{os.getpid()}.png")

    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        f"$bmp=New-Object System.Drawing.Bitmap(1920,1080);"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen(0,0,0,0,[System.Drawing.Size]::new(1920,1080));"
        f"$bmp.Save('{tmp}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose()"
    )

    with open(str(base_dir / f"ps_script_{os.getpid()}.ps1"), "w", encoding="utf-8") as f:
        f.write(ps_script)

    try:
        from agent.utils.proc import NO_WINDOW
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, timeout=30,
            creationflags=NO_WINDOW,
        )
        if result.returncode != 0:
            raise RuntimeError(f"PowerShell failed: {result.stderr.decode(errors='ignore')}")
    finally:
        try:
            os.unlink(str(base_dir / f"ps_script_{os.getpid()}.ps1"))
        except Exception:
            pass

    img = Image.open(tmp).convert("RGB")
    os.unlink(tmp)

    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


async def handle_screenshot(payload: dict) -> dict:
    """Capture full screen with fallback methods. Returns base64 JPEG."""
    import asyncio

    # Try mss first (fastest) — run in thread to avoid blocking event loop
    try:
        data = await asyncio.to_thread(_capture_via_mss)
        if data:
            logger.info("Screenshot captured via mss: %d bytes", len(data))
            return {'image_base64': base64.b64encode(data).decode(), 'format': 'jpeg'}
    except Exception as e:
        logger.warning("mss capture failed: %s — trying PowerShell", e)

    # Try PowerShell fallback — also in thread
    try:
        data = await asyncio.to_thread(_capture_via_powershell)
        if data:
            logger.info("Screenshot captured via PowerShell: %d bytes", len(data))
            return {'image_base64': base64.b64encode(data).decode(), 'format': 'jpeg'}
    except Exception as e:
        logger.error("PowerShell fallback also failed: %s", e)

    raise RuntimeError("All screenshot methods failed")