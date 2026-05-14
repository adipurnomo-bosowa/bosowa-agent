import pytest
from unittest.mock import MagicMock, patch


def test_is_newer_version_server_newer():
    from agent.core.auto_update import is_newer_version
    assert is_newer_version('1.0.2', '1.0.1') is True


def test_is_newer_version_same():
    from agent.core.auto_update import is_newer_version
    assert is_newer_version('1.0.1', '1.0.1') is False


def test_is_newer_version_server_older():
    from agent.core.auto_update import is_newer_version
    assert is_newer_version('1.0.0', '1.0.1') is False


def test_is_newer_version_major():
    from agent.core.auto_update import is_newer_version
    assert is_newer_version('2.0.0', '1.9.9') is True


def test_is_newer_version_server_with_v_prefix():
    from agent.core.auto_update import is_newer_version
    assert is_newer_version('v1.0.6', '1.0.5') is True
    assert is_newer_version('V1.0.6', '1.0.5') is True


def test_is_newer_version_current_with_v_prefix():
    from agent.core.auto_update import is_newer_version
    assert is_newer_version('1.0.6', 'v1.0.5') is True


def test_fetch_latest_version_success():
    import requests
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        'version': '1.0.2',
        'download_url': 'https://example.com/BosowAgent.exe',
        'required': False,
    }
    mock_resp.raise_for_status = MagicMock()

    with patch('agent.core.auto_update.requests.get', return_value=mock_resp):
        from agent.core.auto_update import fetch_latest_version
        result = fetch_latest_version(token='tok')

    assert result is not None
    assert result['version'] == '1.0.2'


def test_fetch_latest_version_error_returns_none():
    with patch('agent.core.auto_update.requests.get', side_effect=Exception('timeout')):
        from agent.core.auto_update import fetch_latest_version
        result = fetch_latest_version(token='tok')
    assert result is None
