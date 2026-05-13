from unittest.mock import patch, MagicMock
import importlib
import sys


def _reload_main():
    """Force fresh import of agent.main to avoid cached state between tests."""
    for key in list(sys.modules.keys()):
        if key == 'agent.main':
            del sys.modules[key]


def test_auto_login_skips_overlay_when_session_valid():
    """When token and user session are valid, _try_auto_login returns True."""
    _reload_main()
    mock_user = {'email': 'adi@bosowa.co.id', 'name': 'Adi'}
    # append_login_log is imported inside _try_auto_login() from agent.auth.login,
    # so patch it at the source module.
    with patch('agent.main._try_restore_session', return_value='valid_token'), \
         patch('agent.auth.token_store.get_user_session', return_value=mock_user), \
         patch('agent.main._start_tray') as mock_tray, \
         patch('agent.main._run_agent_service') as mock_svc, \
         patch('agent.auth.login.append_login_log') as mock_log:
        from agent.main import _try_auto_login
        result = _try_auto_login()
        assert result is True
        mock_tray.assert_called_once_with(mock_user)
        mock_svc.assert_called_once()
        mock_log.assert_called_once()


def test_auto_login_returns_false_when_no_token():
    """When no stored token, _try_auto_login returns False."""
    _reload_main()
    with patch('agent.main._try_restore_session', return_value=None), \
         patch('agent.auth.login.check_and_refresh_token', return_value=None):
        from agent.main import _try_auto_login
        result = _try_auto_login()
        assert result is False


def test_auto_login_returns_false_when_no_user_session():
    """When token exists but no user session, _try_auto_login returns False."""
    _reload_main()
    with patch('agent.main._try_restore_session', return_value='valid_token'), \
         patch('agent.auth.token_store.get_user_session', return_value=None), \
         patch('agent.main.get_user_session', return_value=None, create=True):
        from agent.main import _try_auto_login
        result = _try_auto_login()
        assert result is False
