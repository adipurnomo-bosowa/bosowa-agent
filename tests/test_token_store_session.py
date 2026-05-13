"""Tests for user session persistence in token_store."""
from unittest.mock import patch, MagicMock


MODULE = 'agent.auth.token_store'


def test_store_and_get_user_session():
    """store_user_session then get_user_session returns the same dict."""
    user = {'id': 42, 'name': 'Adi', 'email': 'adi@bosowa.co.id'}

    captured = {}

    def fake_read():
        return {}

    def fake_write(data):
        captured.update(data)

    with patch(f'{MODULE}._read_token_file', side_effect=fake_read), \
         patch(f'{MODULE}._write_token_file', side_effect=fake_write):
        from agent.auth.token_store import store_user_session
        store_user_session(user)

    # Now simulate get_user_session reading back what was written
    with patch(f'{MODULE}._read_token_file', return_value=captured):
        from agent.auth.token_store import get_user_session
        result = get_user_session()

    assert result == user


def test_get_user_session_returns_none_if_no_file():
    """get_user_session returns None when TOKEN_FILE doesn't exist (empty dict)."""
    with patch(f'{MODULE}._read_token_file', return_value={}):
        from agent.auth.token_store import get_user_session
        result = get_user_session()

    assert result is None


def test_store_user_session_preserves_existing_refresh_token():
    """Storing a user session does not discard an existing refresh_token."""
    existing = {'refresh_token': 'tok_abc123'}
    user = {'id': 7, 'name': 'Test User'}

    written = {}

    def fake_write(data):
        written.update(data)

    with patch(f'{MODULE}._read_token_file', return_value=dict(existing)), \
         patch(f'{MODULE}._write_token_file', side_effect=fake_write):
        from agent.auth.token_store import store_user_session
        store_user_session(user)

    assert written.get('refresh_token') == 'tok_abc123'
    assert written.get('user') == user
