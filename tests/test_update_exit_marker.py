"""Tests for self-update exit marker (watchdog coordination)."""
from __future__ import annotations

import os
import time
from pathlib import Path


def test_update_replace_marker_fresh(tmp_path: Path, monkeypatch):
    from agent.utils import update_exit_marker as m

    monkeypatch.setattr(m, 'MARKER', tmp_path / '.update_replace_pending')
    assert m.update_replace_marker_fresh() is False
    m.write_update_replace_marker()
    assert m.update_replace_marker_fresh(max_age_sec=60) is True
    old = time.time() - 10
    os.utime(m.MARKER, (old, old))
    assert m.update_replace_marker_fresh(max_age_sec=1.0) is False


def test_clear_update_replace_marker(tmp_path: Path, monkeypatch):
    from agent.utils import update_exit_marker as m

    monkeypatch.setattr(m, 'MARKER', tmp_path / '.update_replace_pending')
    m.write_update_replace_marker()
    m.clear_update_replace_marker()
    assert not m.MARKER.exists()


def test_marker_stale_by_mtime(tmp_path: Path, monkeypatch):
    from agent.utils import update_exit_marker as m

    monkeypatch.setattr(m, 'MARKER', tmp_path / '.update_replace_pending')
    m.write_update_replace_marker()
    old = time.time() - 400

    os.utime(m.MARKER, (old, old))
    assert m.update_replace_marker_fresh(max_age_sec=240) is False
