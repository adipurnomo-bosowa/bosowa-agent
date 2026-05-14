"""Tests untuk fitur update agent."""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch


def test_download_with_progress_reports_percent():
    """progress_cb dipanggil dengan persentase yang meningkat."""
    from agent.core.auto_update import download_update_with_progress

    chunk1 = b'x' * 5000
    chunk2 = b'x' * 5000
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.headers = {'Content-Length': '10000'}
    mock_resp.iter_content.return_value = iter([chunk1, chunk2])
    mock_resp.raise_for_status = MagicMock()

    reported = []

    with patch('requests.get', return_value=mock_resp), \
         patch('builtins.open', MagicMock(return_value=io.BytesIO())), \
         patch('pathlib.Path.mkdir'), \
         patch('pathlib.Path.stat', return_value=MagicMock(st_size=10000)):
        result = download_update_with_progress(
            'https://example.com/BosowAgent.exe',
            'token123',
            lambda pct: reported.append(pct),
        )

    assert result is not None
    assert result.name == 'BosowAgent_new.exe'
    assert 100 in reported
    for i in range(len(reported) - 1):
        assert reported[i] <= reported[i + 1]


def test_download_with_progress_returns_none_on_error():
    """Return None jika request gagal."""
    with patch('requests.get', side_effect=Exception('timeout')):
        from agent.core.auto_update import download_update_with_progress
        result = download_update_with_progress(
            'https://example.com/BosowAgent.exe',
            'token123',
            lambda pct: None,
        )
    assert result is None


def test_download_with_progress_no_content_length():
    """When Content-Length is missing, progress_cb called only once with 100."""
    mock_resp = MagicMock()
    mock_resp.headers = {}  # no Content-Length
    mock_resp.iter_content.return_value = iter([b'x' * 1000])
    mock_resp.raise_for_status = MagicMock()

    reported = []

    with patch('requests.get', return_value=mock_resp), \
         patch('builtins.open', MagicMock(return_value=io.BytesIO())), \
         patch('pathlib.Path.mkdir'), \
         patch('pathlib.Path.stat', return_value=MagicMock(st_size=1000)):
        from agent.core.auto_update import download_update_with_progress
        result = download_update_with_progress(
            'https://example.com/BosowAgent.exe',
            'token123',
            lambda pct: reported.append(pct),
        )

    assert result is not None
    assert reported == [100]  # only final 100, no intermediate


def test_is_newer_version():
    from agent.core.auto_update import is_newer_version
    assert is_newer_version('1.0.2', '1.0.1') is True
    assert is_newer_version('1.0.1', '1.0.1') is False
    assert is_newer_version('1.0.0', '1.0.1') is False
    assert is_newer_version('2.0.0', '1.9.9') is True
