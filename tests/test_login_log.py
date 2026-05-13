import sys
import os
from unittest.mock import patch, MagicMock, mock_open
import pytest

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
