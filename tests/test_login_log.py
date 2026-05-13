import requests
import pytest
from unittest.mock import patch, MagicMock

# Test append_login_log writes correctly
def test_append_login_log_creates_new_file(tmp_path):
    log_file = tmp_path / 'login_history.log'
    with patch('agent.auth.login._LOGIN_LOG_FILE', log_file):
        from agent.auth.login import append_login_log
        append_login_log('adi@bosowa.co.id', 'Adi', 'LOGIN', 'direct', 'OK')
        import time; time.sleep(0.1)  # let daemon thread finish
        assert log_file.exists()
        content = log_file.read_text()
        assert 'LOGIN' in content
        assert 'adi@bosowa.co.id' in content
        assert 'direct' in content
        assert 'OK' in content

def test_append_login_log_rotates_at_max_lines(tmp_path):
    from agent.auth.login import _MAX_LOG_LINES
    log_file = tmp_path / 'login_history.log'
    # Pre-fill with _MAX_LOG_LINES lines
    existing = '2026-01-01 00:00:00 | line\n' * _MAX_LOG_LINES
    log_file.write_text(existing, encoding='utf-8')
    with patch('agent.auth.login._LOGIN_LOG_FILE', log_file):
        from agent.auth.login import append_login_log
        append_login_log('adi@bosowa.co.id', 'Adi', 'LOGIN', 'direct', 'OK')
        import time; time.sleep(0.1)
        lines = log_file.read_text(encoding='utf-8').splitlines()
        assert len(lines) == _MAX_LOG_LINES  # rotated to keep at most 1000


def test_direct_login_success_stores_session_and_logs(tmp_path):
    """direct_login success: store_user_session and append_login_log('OK') both called."""
    mock_user = {'email': 'adi@bosowa.co.id', 'name': 'Adi', 'token': 'tok', 'refreshToken': 'ref'}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {'user': mock_user, 'token': 'tok', 'refreshToken': 'ref'}
    mock_resp.raise_for_status.return_value = None

    with patch('agent.auth.login.requests.post', return_value=mock_resp), \
         patch('agent.auth.login.store_user_session') as mock_store, \
         patch('agent.auth.login.append_login_log') as mock_log:
        from agent.auth.login import direct_login
        direct_login('adi@bosowa.co.id', 'password123')
        mock_store.assert_called_once()
        stored_user = mock_store.call_args[0][0]
        assert stored_user.get('email') == 'adi@bosowa.co.id'
        mock_log.assert_called_once()
        call_args = mock_log.call_args[0]
        assert call_args[2] == 'LOGIN'
        assert call_args[3] == 'direct'
        assert call_args[4] == 'OK'


def test_direct_login_failure_logs_fail(tmp_path):
    """direct_login ConnectionError: append_login_log('FAIL') called, store_user_session NOT called."""
    with patch('agent.auth.login.requests.post', side_effect=requests.exceptions.ConnectionError), \
         patch('agent.auth.login.store_user_session') as mock_store, \
         patch('agent.auth.login.append_login_log') as mock_log:
        from agent.auth.login import direct_login
        direct_login('adi@bosowa.co.id', 'wrongpass')
        mock_store.assert_not_called()
        mock_log.assert_called_once()
        call_args = mock_log.call_args[0]
        assert call_args[4] == 'FAIL'


def test_logout_logs_logout_event(tmp_path):
    """logout() logs LOGOUT event."""
    with patch('agent.auth.login.clear_all_credentials'), \
         patch('agent.auth.login.append_login_log') as mock_log:
        from agent.auth.login import logout
        logout('adi@bosowa.co.id', 'Adi')
        mock_log.assert_called_once()
        call_args = mock_log.call_args[0]
        assert call_args[2] == 'LOGOUT'
        assert call_args[3] == 'manual'
        assert call_args[4] == 'OK'
