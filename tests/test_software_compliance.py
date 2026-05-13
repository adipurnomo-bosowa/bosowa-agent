"""Tests for software_compliance module."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch
import pytest


def test_load_whitelist_returns_lowercase_names(tmp_path):
    csv_content = 'category,name\nOffice,Microsoft 365\nBrowser,Google Chrome\n'
    wl_file = tmp_path / 'whitelist.csv'
    wl_file.write_text(csv_content, encoding='utf-8')

    from agent.core.software_compliance import load_whitelist
    with patch('agent.core.software_compliance._get_whitelist_path', return_value=wl_file):
        result = load_whitelist()

    assert 'microsoft 365' in result
    assert 'google chrome' in result
    assert len(result) == 2


def test_load_whitelist_missing_file(tmp_path):
    from agent.core.software_compliance import load_whitelist
    missing = tmp_path / 'no_file.csv'
    with patch('agent.core.software_compliance._get_whitelist_path', return_value=missing):
        result = load_whitelist()
    assert result == []


def test_check_compliance_all_matched():
    from agent.core.software_compliance import check_compliance
    whitelist = ['microsoft edge', 'google chrome', 'zoom']
    installed = ['Microsoft Edge', 'Google Chrome', 'Zoom']
    with patch('agent.core.software_compliance.get_installed_programs', return_value=installed):
        result = check_compliance(whitelist=whitelist)
    assert result.status == 'OK'
    assert result.score == 100.0
    assert result.total == 3
    assert len(result.unmatched) == 0


def test_check_compliance_error_threshold():
    from agent.core.software_compliance import check_compliance
    whitelist = ['microsoft edge']
    # 1 matched out of 10 = 10% -> ERROR
    installed = ['Microsoft Edge'] + [f'Unknown App {i}' for i in range(9)]
    with patch('agent.core.software_compliance.get_installed_programs', return_value=installed):
        result = check_compliance(whitelist=whitelist)
    assert result.status == 'ERROR'
    assert result.score == pytest.approx(10.0)
    assert len(result.unmatched) == 9


def test_check_compliance_warn_threshold():
    from agent.core.software_compliance import check_compliance
    whitelist = ['app a', 'app b', 'app c', 'app d', 'app e',
                 'app f', 'app g']
    # 7 out of 10 = 70% -> WARN
    installed = [f'App {c.upper()}' for c in 'abcdefg'] + [f'Unknown {i}' for i in range(3)]
    with patch('agent.core.software_compliance.get_installed_programs', return_value=installed):
        result = check_compliance(whitelist=whitelist)
    assert result.status == 'WARN'
    assert 60 <= result.score <= 80


def test_get_installed_programs_non_windows():
    import sys
    from agent.core.software_compliance import get_installed_programs
    with patch.object(sys, 'platform', 'linux'):
        result = get_installed_programs()
    assert result == []


def test_check_compliance_empty_installed():
    from agent.core.software_compliance import check_compliance
    with patch('agent.core.software_compliance.get_installed_programs', return_value=[]):
        result = check_compliance(whitelist=['zoom'])
    assert result.status == 'WARN'
    assert result.total == 0


def test_partial_match_works():
    from agent.core.software_compliance import check_compliance
    whitelist = ['microsoft 365']
    # "Microsoft 365 Apps for enterprise" should match "microsoft 365"
    installed = ['Microsoft 365 Apps for enterprise']
    with patch('agent.core.software_compliance.get_installed_programs', return_value=installed):
        result = check_compliance(whitelist=whitelist)
    assert result.status == 'OK'
    assert len(result.matched) == 1
