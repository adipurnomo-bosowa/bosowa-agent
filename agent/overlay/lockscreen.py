"""Full-screen lock screen overlay using PyQt5."""
from __future__ import annotations

import json
import os
import platform as _platform
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import bcrypt
import certifi

from PyQt5 import QtCore, QtGui, QtWidgets

from agent import config
from agent.auth.token_store import (
    get_pin_hash_and_expiry,
    store_pin_hash,
)
from agent.core.hardware import get_mac_address
from agent.utils.logger import logger


# ---------------------------------------------------------------------------
# Result / config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class LoginResult:
    success: bool
    token: str | None = None
    refresh_token: str | None = None
    user: dict | None = None
    error: str | None = None


@dataclass
class OverlayConfig:
    on_authenticated: Callable[[str, str, dict], None]
    on_web_login_start: Callable[[str], None] | None = None
    on_error: Callable[[str], None] | None = None


class _AuthResultEvent(QtCore.QEvent):
    TYPE = QtCore.QEvent.Type(QtCore.QEvent.User + 1)

    def __init__(self, success: bool, token: str, refresh: str, error: str):
        super().__init__(self.TYPE)
        self.success = success
        self.token = token
        self.refresh = refresh
        self.error = error


class _UserResultEvent(QtCore.QEvent):
    TYPE = QtCore.QEvent.Type(QtCore.QEvent.User + 2)

    def __init__(self, user_json: str):
        super().__init__(self.TYPE)
        self.user_json = user_json


class _OverlayEventFilter(QtCore.QObject):
    """Filter events on the Qt app to dispatch auth/user events to the overlay."""

    def __init__(self, overlay: 'LockScreenOverlay'):
        super().__init__()
        self._overlay = overlay

    def eventFilter(self, obj, event: QtCore.QEvent) -> bool:
        etype = event.type()
        if etype == _AuthResultEvent.TYPE:
            ev: _AuthResultEvent = event
            logger.info('Received _AuthResultEvent: success=%s', ev.success)
            self._overlay._handle_auth_result(ev.success, ev.token, ev.refresh, ev.error)
            return True
        if etype == _UserResultEvent.TYPE:
            ev: _UserResultEvent = event
            logger.info('Received _UserResultEvent')
            self._overlay._handle_user_result(ev.user_json)
            return True
        return super().eventFilter(obj, event)


# ---------------------------------------------------------------------------
# LockScreenOverlay
# ---------------------------------------------------------------------------

class LockScreenOverlay:
    """PyQt5 full-screen lock screen that blocks PC access until authenticated."""

    class Signals(QtCore.QObject):
        authenticated = QtCore.pyqtSignal(str, str, dict)
        error = QtCore.pyqtSignal(str)
        status_update = QtCore.pyqtSignal(str, str)
        auth_result = QtCore.pyqtSignal(bool, str, str, str)
        user_result = QtCore.pyqtSignal(str)

    def __init__(self, cfg: OverlayConfig):
        self.cfg = cfg
        self.signals = self.Signals()
        self._thread: threading.Thread | None = None
        self._window: QtWidgets.QWidget | None = None
        self._pending_session_code: str | None = None
        self._auth_done = False
        self._last_token = ''
        self._last_refresh = ''
        self._last_user: dict = {}
        self._event_filter: _OverlayEventFilter | None = None
        self._web_login_cancel = threading.Event()
        self._web_login_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self) -> None:
        """Show the overlay in a background thread. Blocks caller until window is ready."""
        self._thread = threading.Thread(target=self._run_qt, daemon=True)
        self._thread.start()
        while self._window is None:
            import time; time.sleep(0.05)

    def close(self) -> None:
        if self._window:
            QtCore.QMetaObject.invokeMethod(
                self._window, '_do_close',
                QtCore.Qt.QueuedConnection,
            )

    def wait_until_closed(self) -> None:
        if self._thread and self._thread.is_alive():
            self._thread.join()

    # ------------------------------------------------------------------
    # Qt thread
    # ------------------------------------------------------------------

    def _run_qt(self) -> None:
        app = QtWidgets.QApplication([])
        app.setQuitOnLastWindowClosed(False)
        window = self._build_window()
        self._window = window
        # Thread-safe worker -> UI bridges.
        self.signals.auth_result.connect(self._handle_auth_result)
        self.signals.user_result.connect(self._handle_user_result)
        self.signals.status_update.connect(self._update_status)
        # If a lock message was queued (via force_lock from server), show it.
        try:
            from agent.auth.token_store import consume_lock_message
            lock_msg = consume_lock_message()
            if lock_msg:
                self._update_status(f'🔒 {lock_msg}', '#EF5350')
        except Exception as e:
            logger.debug('lock message check failed: %s', e)
        window.showFullScreen()
        # Install event filter on app to receive cross-thread events
        self._event_filter = _OverlayEventFilter(self)
        app.installEventFilter(self._event_filter)
        app.exec_()

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build_window(self) -> QtWidgets.QWidget:
        window = QtWidgets.QWidget()
        window.setWindowFlags(
            QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.FramelessWindowHint
        )
        window.setStyleSheet(f'background-color: {config.OVERLAY_LOCK_COLOR};')
        window.setContextMenuPolicy(QtCore.Qt.NoContextMenu)

        def ignore_close(e):
            e.ignore()
        window.closeEvent = ignore_close
        window._do_close = self._do_close

        # Attach overlay handlers so slots can update UI via invokeMethod
        window._update_status = self._update_status
        window._handle_auth_result = self._handle_auth_result
        window._handle_user_result = self._handle_user_result

        layout = QtWidgets.QVBoxLayout(window)
        layout.setContentsMargins(60, 40, 60, 40)
        layout.setSpacing(14)
        layout.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)

        # ── Header ───────────────────────────────────────────────────
        logo = QtWidgets.QLabel()
        logo.setAlignment(QtCore.Qt.AlignHCenter)
        logo.setStyleSheet('background: transparent; border: none;')
        logo_path = self._resolve_asset_path('assets/PORTAL.png')
        logo_loaded = False
        if logo_path and os.path.exists(logo_path):
            pix = QtGui.QPixmap(logo_path)
            if not pix.isNull():
                logo.setPixmap(
                    pix.scaledToWidth(
                        320,
                        QtCore.Qt.SmoothTransformation,
                    )
                )
                logo_loaded = True

        title = QtWidgets.QLabel('BOSOWA PORTAL')
        title.setAlignment(QtCore.Qt.AlignHCenter)
        title.setStyleSheet('''
            color: #1E88E5;
            font-size: 30px;
            font-weight: 800;
            letter-spacing: 3px;
            font-family: "Segoe UI", Arial;
        ''')

        subtitle = QtWidgets.QLabel('Endpoint Security Agent')
        subtitle.setAlignment(QtCore.Qt.AlignHCenter)
        subtitle.setStyleSheet('''
            color: #64B5F6;
            font-size: 16px;
            font-weight: 300;
            letter-spacing: 2px;
            font-family: "Segoe UI", Arial;
        ''')

        divider_h = QtWidgets.QFrame()
        divider_h.setFrameShape(QtWidgets.QFrame.HLine)
        divider_h.setStyleSheet('background-color: #1E88E5; max-width: 200px; margin: 8px auto;')

        instruction = QtWidgets.QLabel('Silakan login untuk melanjutkan')
        instruction.setAlignment(QtCore.Qt.AlignHCenter)
        instruction.setStyleSheet('color: #90CAF9; font-size: 14px; font-family: "Segoe UI";')

        # ── Login card ───────────────────────────────────────────────
        card = QtWidgets.QWidget()
        card.setStyleSheet('''
            background-color: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 12px;
        ''')
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(40, 30, 40, 30)
        card_layout.setSpacing(14)

        self._email_input = QtWidgets.QLineEdit()
        self._email_input.setPlaceholderText('Email / ID Karyawan')
        self._email_input.setMaxLength(120)
        self._style_input(self._email_input)

        self._password_input = QtWidgets.QLineEdit()
        self._password_input.setPlaceholderText('Password')
        self._password_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self._password_input.setMaxLength(128)
        self._style_input(self._password_input)
        self._password_input.returnPressed.connect(self._on_login_clicked)

        self._login_btn = QtWidgets.QPushButton('Masuk')
        self._style_button(self._login_btn, accent=True)
        self._login_btn.clicked.connect(self._on_login_clicked)

        card_layout.addWidget(self._email_input)
        card_layout.addWidget(self._password_input)
        card_layout.addWidget(self._login_btn, 0, QtCore.Qt.AlignHCenter)

        # ── Status area ──────────────────────────────────────────────
        self._status_label = QtWidgets.QLabel('🔴 Offline – Menunggu autentikasi...')
        self._status_label.setAlignment(QtCore.Qt.AlignHCenter)
        self._status_label.setStyleSheet('color: #EF5350; font-size: 13px; font-family: "Segoe UI";')

        # Big readable session-code label (visible only during web-login flow).
        self._session_label = QtWidgets.QLabel('')
        self._session_label.setAlignment(QtCore.Qt.AlignHCenter)
        self._session_label.setStyleSheet('''
            color: #93C5FD;
            font-size: 28px;
            font-weight: 800;
            letter-spacing: 6px;
            font-family: Consolas, "Segoe UI";
            padding: 10px 0;
        ''')

        # Small caption explaining what to do with the code.
        self._session_help = QtWidgets.QLabel('')
        self._session_help.setAlignment(QtCore.Qt.AlignHCenter)
        self._session_help.setWordWrap(True)
        self._session_help.setStyleSheet(
            'color: #94A3B8; font-size: 12px; font-family: "Segoe UI";'
        )

        self._welcome_label = QtWidgets.QLabel('')
        self._welcome_label.setAlignment(QtCore.Qt.AlignHCenter)
        self._welcome_label.setStyleSheet('''
            color: #43A047;
            font-size: 16px;
            font-weight: bold;
            font-family: "Segoe UI";
        ''')

        # ── Divider + web login ──────────────────────────────────────
        hr = QtWidgets.QFrame()
        hr.setFrameShape(QtWidgets.QFrame.HLine)
        hr.setStyleSheet('background-color: rgba(255,255,255,0.15);')

        or_label = QtWidgets.QLabel('atau')
        or_label.setAlignment(QtCore.Qt.AlignHCenter)
        or_label.setStyleSheet('color: #90CAF9; font-size: 13px; font-family: "Segoe UI";')

        self._web_login_btn = QtWidgets.QPushButton('Buka di Browser')
        self._style_button(self._web_login_btn, accent=False)
        self._web_login_btn.clicked.connect(self._on_web_login_clicked)

        # Cancel button shown only while web-login is pending
        self._web_cancel_btn = QtWidgets.QPushButton('← Kembali ke Login')
        self._style_button(self._web_cancel_btn, accent=False)
        self._web_cancel_btn.clicked.connect(self._on_web_login_cancel)
        self._web_cancel_btn.setVisible(False)

        # ── PIN fallback ─────────────────────────────────────────────
        self._pin_frame = QtWidgets.QFrame()
        self._pin_frame.setStyleSheet('''
            background-color: rgba(255,200,0,0.05);
            border: 1px solid rgba(255,200,0,0.2);
            border-radius: 8px;
        ''')
        pin_layout = QtWidgets.QVBoxLayout(self._pin_frame)
        pin_layout.setContentsMargins(20, 12, 20, 12)
        pin_layout.setSpacing(10)

        pin_header = QtWidgets.QLabel('🔒 Login dengan PIN')
        pin_header.setStyleSheet('color: #FFD54F; font-size: 13px; font-family: "Segoe UI";')

        self._pin_input = QtWidgets.QLineEdit()
        self._pin_input.setPlaceholderText('Masukkan PIN 6 digit')
        self._pin_input.setMaxLength(6)
        self._pin_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self._pin_input.setAlignment(QtCore.Qt.AlignHCenter)
        self._pin_input.setStyleSheet('''
            QLineEdit {
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.2);
                border-radius: 6px;
                color: white;
                padding: 8px 16px;
                font-size: 16px;
                letter-spacing: 4px;
                font-family: "Segoe UI";
            }
            QLineEdit:focus {
                border: 1px solid #1E88E5;
                background: rgba(30,136,229,0.1);
            }
            QLineEdit::placeholder { color: rgba(255,255,255,0.4); }
        ''')
        self._pin_input.returnPressed.connect(self._on_pin_entered)

        self._pin_btn = QtWidgets.QPushButton('Gunakan PIN')
        self._style_button(self._pin_btn, accent=False, warn=True)
        self._pin_btn.clicked.connect(self._on_pin_entered)

        pin_layout.addWidget(pin_header)
        pin_layout.addWidget(self._pin_input)
        pin_layout.addWidget(self._pin_btn, 0, QtCore.Qt.AlignHCenter)
        self._pin_frame.setVisible(False)

        # ── Machine ID ────────────────────────────────────────────────
        mac = get_mac_address()
        short_mac = mac.replace(':', '')[-6:]
        machine_label = QtWidgets.QLabel(f'Kode mesin: BSW-{short_mac}')
        machine_label.setAlignment(QtCore.Qt.AlignHCenter)
        machine_label.setStyleSheet('''
            color: rgba(255,255,255,0.35);
            font-size: 11px;
            font-family: Consolas, monospace;
        ''')

        # ── Assemble ──────────────────────────────────────────────────
        layout.addStretch(1)
        layout.addWidget(logo)
        if not logo_loaded:
            layout.addWidget(title)
            layout.addWidget(subtitle)
            layout.addWidget(divider_h)
            layout.addWidget(instruction)
        layout.addSpacing(10)
        layout.addWidget(card)
        layout.addWidget(self._status_label)
        layout.addWidget(self._session_label)
        layout.addWidget(self._session_help)
        layout.addWidget(self._welcome_label)
        layout.addWidget(hr)
        layout.addWidget(or_label)
        layout.addWidget(self._web_login_btn)
        layout.addWidget(self._web_cancel_btn)
        layout.addSpacing(6)
        layout.addWidget(self._pin_frame)
        layout.addStretch(1)
        layout.addWidget(machine_label)

        return window

    # ------------------------------------------------------------------
    # Style helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _style_input(widget: QtWidgets.QLineEdit) -> None:
        widget.setStyleSheet('''
            QLineEdit {
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.2);
                border-radius: 8px;
                color: white;
                padding: 12px 16px;
                font-size: 15px;
                font-family: "Segoe UI", Arial;
                min-width: 320px;
            }
            QLineEdit:focus {
                border: 1px solid #1E88E5;
                background: rgba(30,136,229,0.1);
            }
            QLineEdit::placeholder { color: rgba(255,255,255,0.4); }
        ''')

    @staticmethod
    def _style_button(
        widget: QtWidgets.QPushButton,
        accent: bool = False,
        warn: bool = False,
    ) -> None:
        if accent:
            widget.setStyleSheet('''
                QPushButton {
                    background-color: #1E88E5;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 12px 48px;
                    font-size: 15px;
                    font-weight: 600;
                    font-family: "Segoe UI", Arial;
                    min-width: 200px;
                }
                QPushButton:hover { background-color: #1976D2; }
                QPushButton:pressed { background-color: #1565C0; }
                QPushButton:disabled { background-color: #1E88E5; opacity: 0.4; }
            ''')
        elif warn:
            widget.setStyleSheet('''
                QPushButton {
                    background-color: rgba(255,213,79,0.15);
                    color: #FFD54F;
                    border: 1px solid rgba(255,213,79,0.3);
                    border-radius: 8px;
                    padding: 10px 32px;
                    font-size: 14px;
                    font-weight: 600;
                    font-family: "Segoe UI", Arial;
                }
                QPushButton:hover { background-color: rgba(255,213,79,0.25); }
                QPushButton:pressed { background-color: rgba(255,213,79,0.35); }
            ''')
        else:
            widget.setStyleSheet('''
                QPushButton {
                    background-color: rgba(255,255,255,0.1);
                    color: #90CAF9;
                    border: 1px solid rgba(255,255,255,0.2);
                    border-radius: 8px;
                    padding: 12px 48px;
                    font-size: 15px;
                    font-weight: 500;
                    font-family: "Segoe UI", Arial;
                    min-width: 200px;
                }
                QPushButton:hover { background-color: rgba(255,255,255,0.18); }
                QPushButton:pressed { background-color: rgba(255,255,255,0.25); }
            ''')

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_login_clicked(self) -> None:
        email = self._email_input.text().strip()
        password = self._password_input.text()
        if not email or not password:
            self._update_status('Mohon isi email dan password', '#EF5350')
            return
        self._set_inputs_enabled(False)
        self._update_status('🟡 Mengautentikasi… (timeout 20 detik)', '#FFD54F')
        # Safety net: if the login thread hangs longer than 25s without dispatching
        # an auth result, re-enable inputs so the user is never stuck.
        QtCore.QTimer.singleShot(25000, self._login_timeout_guard)
        threading.Thread(
            target=self._do_direct_login,
            args=(email, password),
            daemon=True,
        ).start()

    def _login_timeout_guard(self) -> None:
        """Re-enable the form if no auth result has arrived after the timeout."""
        if self._auth_done:
            return
        # _set_inputs_enabled is idempotent
        self._set_inputs_enabled(True)
        self._update_status(f'🔴 Server tidak merespons. ({config.SERVER_URL})', '#EF5350')

    def _do_direct_login(self, email: str, password: str) -> None:
        logger.info('_do_direct_login: email=%s', email)
        import requests as _requests
        import threading

        result = {'success': False, 'token': '', 'refresh': '', 'user': None, 'error': ''}

        def _http_request():
            try:
                logger.info('POST to %s/auth/agent-login', config.API_BASE)
                resp = _requests.post(
                    f'{config.API_BASE}/auth/agent-login',
                    json={
                        'email': email,
                        'password': password,
                        'device_mac': get_mac_address(),
                        'hostname': _platform.node(),
                    },
                    timeout=config.HTTP_TIMEOUT,
                    verify=certifi.where(),
                )
                logger.info('Got status=%d', resp.status_code)
                if resp.status_code in (401, 403):
                    from agent.auth.login import message_for_agent_login_failure
                    custom = message_for_agent_login_failure(resp.status_code, resp)
                    result['error'] = custom or (
                        'Email atau password salah' if resp.status_code == 401 else 'Akses ditolak'
                    )
                else:
                    resp.raise_for_status()
                    data = resp.json()
                    result['success'] = True
                    result['token'] = data.get('token', '')
                    result['refresh'] = data.get('refresh_token', '')
                    result['user'] = data.get('user')
                    logger.info('Login OK, token=%s...', result['token'][:15] if result['token'] else 'EMPTY')
            except _requests.exceptions.ConnectionError as e:
                logger.error('ConnectionError: %s', e)
                result['error'] = f'Server tidak dapat dijangkau ({config.SERVER_URL})'
            except _requests.exceptions.Timeout as e:
                logger.error('Timeout: %s', e)
                result['error'] = f'Server timeout ({config.SERVER_URL})'
            except Exception as e:
                logger.error('Login error: %s', e)
                result['error'] = str(e)
            result['_done'] = True

        # Run HTTP in thread with timeout
        t = threading.Thread(target=_http_request, daemon=True)
        t.start()
        t.join(timeout=20)
        if not result.get('_done'):
            logger.error('HTTP timeout after 20s — forcing failure')
            result['error'] = 'Request timeout (>20s)'
            result['_done'] = True

        success = result['success']
        token = result['token']
        refresh = result['refresh']
        user = result['user']
        error = result['error']

        logger.info('Delivering direct-login result via signal(success=%s)', success)
        self.signals.auth_result.emit(success, token or '', refresh or '', error or '')
        if success and user:
            self.signals.user_result.emit(json.dumps(user))

    def _on_web_login_clicked(self) -> None:
        session_code = str(uuid.uuid4())[:8].upper()
        self._pending_session_code = session_code
        self._session_label.setText(session_code)
        self._session_help.setText(
            'Browser akan terbuka otomatis di tab baru. Login portal lalu klik "Tautkan Agent".'
        )
        self._update_status('🟡 Membuka browser portal…', '#FFD54F')
        self._email_input.setVisible(False)
        self._password_input.setVisible(False)
        self._login_btn.setVisible(False)
        self._web_login_btn.setVisible(False)
        self._pin_frame.setVisible(False)
        self._web_cancel_btn.setVisible(True)
        if self.cfg.on_web_login_start:
            try:
                self.cfg.on_web_login_start(session_code)
            except Exception as e:
                logger.warning('on_web_login_start callback raised: %s', e)

        # Start the background flow: register code → open browser → poll
        self._web_login_cancel.clear()
        self._web_login_thread = threading.Thread(
            target=self._web_login_flow,
            args=(session_code,),
            daemon=True,
        )
        self._web_login_thread.start()

    def _on_web_login_cancel(self) -> None:
        """User clicked Kembali while waiting for browser login."""
        logger.info('Web-login cancelled by user')
        self._web_login_cancel.set()
        self._pending_session_code = None
        self._session_label.setText('')
        self._session_help.setText('')
        self._web_cancel_btn.setVisible(False)
        # Restore primary login UI
        self._email_input.setVisible(True)
        self._password_input.setVisible(True)
        self._login_btn.setVisible(True)
        self._web_login_btn.setVisible(True)
        # PIN frame visibility is controlled elsewhere; only force-show if a PIN exists.
        try:
            if get_pin_hash_and_expiry() is not None:
                self._pin_frame.setVisible(True)
        except Exception:
            pass
        self._update_status('🔴 Offline – Menunggu autentikasi...', '#EF5350')
        self._email_input.setFocus()

    def _web_login_flow(self, session_code: str) -> None:
        """Init session on server, open browser to /agent-link, then poll for claim."""
        import requests as _requests
        # Step 1: register the code with the server so /agent-link can resolve it
        try:
            resp = _requests.post(
                f'{config.API_BASE}/auth/agent-session/init',
                json={
                    'code': session_code,
                    'device_mac': get_mac_address(),
                    'hostname': _platform.node(),
                },
                timeout=config.HTTP_TIMEOUT,
                verify=certifi.where(),
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning('agent-session/init failed: %s', e)
            self._invoke_status('🔴 Tidak dapat menghubungi server. Coba lagi.', '#EF5350')
            self._invoke_restore_after_failure()
            return

        if self._web_login_cancel.is_set():
            return

        # Step 2: open default browser to the link page
        link_url = f'{config.SERVER_URL}/agent-link?code={session_code}'
        try:
            webbrowser.open(link_url, new=2)
            try:
                from agent.core.audit_client import record_link_open
                record_link_open(link_url, sample_detail=link_url)
            except Exception:
                pass
        except Exception as e:
            logger.warning('webbrowser.open failed: %s', e)
        self._invoke_status(
            f'🟡 Buka {link_url} di browser dan login. Kode: {session_code}',
            '#FFD54F',
        )

        # Step 3: poll for claim. Up to 5 minutes, every 2 seconds.
        deadline = time.time() + 300
        while time.time() < deadline:
            if self._web_login_cancel.is_set():
                logger.info('Web-login polling cancelled')
                return
            try:
                pr = _requests.get(
                    f'{config.API_BASE}/auth/agent-session/poll',
                    params={'code': session_code},
                    timeout=8,
                    verify=certifi.where(),
                )
                if pr.status_code == 200:
                    data = pr.json()
                    if data.get('status') == 'ok' and data.get('token'):
                        self._deliver_web_login_result(
                            token=data['token'],
                            refresh=data.get('refresh_token') or '',
                            user=data.get('user') or {},
                        )
                        return
                elif pr.status_code == 410:
                    self._invoke_status('🔴 Sesi browser kedaluwarsa. Coba lagi.', '#EF5350')
                    self._invoke_restore_after_failure()
                    return
            except Exception as e:
                logger.debug('poll error: %s', e)
            # Wait 2s, but break early on cancel
            for _ in range(20):
                if self._web_login_cancel.is_set():
                    return
                time.sleep(0.1)

        self._invoke_status('🔴 Waktu menunggu habis. Coba lagi.', '#EF5350')
        self._invoke_restore_after_failure()

    def _deliver_web_login_result(self, token: str, refresh: str, user: dict) -> None:
        """Hand the polling result back to the Qt main thread via the event filter."""
        logger.info('Web-login: delivering auth result to Qt thread (token_len=%d)', len(token or ''))
        if self._window is None:
            logger.error('Web-login: Qt window unavailable, cannot deliver auth result')
            return
        if user:
            self.signals.user_result.emit(json.dumps(user))
        self.signals.auth_result.emit(True, token or '', refresh or '', '')

    def _invoke_status(self, msg: str, color: str) -> None:
        if self._window is None:
            return
        try:
            self.signals.status_update.emit(msg, color)
        except Exception as e:
            logger.warning('status_update emit failed: %s', e)

    def _invoke_restore_after_failure(self) -> None:
        """Restore the primary login form after a web-login failure (cross-thread safe)."""
        if self._window is None:
            return
        # Use a queued lambda via singleShot since invokeMethod requires registered slot signatures
        QtCore.QTimer.singleShot(0, self._on_web_login_cancel)

    def _on_pin_entered(self) -> None:
        pin = self._pin_input.text().strip()
        if len(pin) != 6 or not pin.isdigit():
            self._update_status('PIN harus 6 digit angka', '#EF5350')
            return
        self._set_inputs_enabled(False)
        self._update_status('🟡 Memverifikasi PIN...', '#FFD54F')
        threading.Thread(target=self._do_pin_verify, args=(pin,), daemon=True).start()

    def _do_pin_verify(self, pin: str) -> None:
        pin_data = get_pin_hash_and_expiry()
        if pin_data is None:
            success, token, refresh, error = self._request_pin_from_server()
        else:
            pin_hash, valid_until = pin_data
            try:
                valid = bcrypt.checkpw(pin.encode(), pin_hash)
            except Exception:
                valid = False
            if valid:
                success, token, refresh, error = True, '_pin_auth_', None, ''
            else:
                success, token, refresh, error = False, '', '', 'PIN tidak valid'

        self._last_user = {'name': 'PIN User'} if success else {}
        if self._window is None:
            logger.error('Qt window unavailable while returning PIN result')
            return
        self.signals.auth_result.emit(success, token or '', refresh or '', error or '')

    def _request_pin_from_server(self) -> tuple[bool, str, str, str]:
        import requests as _requests
        try:
            resp = _requests.get(
                f'{config.API_BASE}/auth/agent-pin',
                params={'device_mac': get_mac_address()},
                timeout=config.PIN_SETUP_TIMEOUT,
                verify=certifi.where(),
            )
            resp.raise_for_status()
            data = resp.json()
            pin_hash = data['pin_hash'].encode('latin-1')
            valid_until = datetime.fromisoformat(data['valid_until']).replace(tzinfo=timezone.utc)
            store_pin_hash(pin_hash, valid_until)
            return True, '_pin_auth_', '', ''
        except Exception as e:
            logger.warning('PIN server request failed: %s', e)
            return False, '', '', 'PIN server tidak dapat dijangkau'

    # ------------------------------------------------------------------
    # Qt slots (called via invokeMethod from background threads)
    # ------------------------------------------------------------------

    def _update_status(self, msg: str, color: str) -> None:
        self._status_label.setText(msg)
        self._status_label.setStyleSheet(
            f'color: {color}; font-size: 13px; font-family: "Segoe UI";'
        )

    def _handle_auth_result(
        self,
        success: bool,
        token: str,
        refresh_token: str,
        error: str,
    ) -> None:
        logger.info('_handle_auth_result ENTRY: success=%s', success)
        self._set_inputs_enabled(True)
        if success:
            self._last_token = token
            self._last_refresh = refresh_token
            self._auth_done = True
            self._welcome_label.setText('✔ Login berhasil!')
            self._welcome_label.setStyleSheet(
                'color: #43A047; font-size: 18px; font-weight: bold; font-family: "Segoe UI";'
            )
            logger.info('Scheduling _close_and_continue in 1500ms')
            QtCore.QTimer.singleShot(1500, self._close_and_continue)
        else:
            self._update_status(f'🔴 {error}', '#EF5350')
            self._email_input.setFocus()

    def _handle_user_result(self, user_json: str) -> None:
        logger.info('_handle_user_result: %s', user_json[:100])
        user = json.loads(user_json)
        self._last_user = user
        name = user.get('name', '')
        if name:
            self._welcome_label.setText(f'Selamat datang, {name}!')

    def _close_and_continue(self) -> None:
        logger.info('_close_and_continue called, token=%s', self._last_token[:20] if self._last_token else 'empty')
        logger.info('_close_and_continue: on_authenticated=%s, last_token=%s', bool(self.cfg.on_authenticated), bool(self._last_token))
        if self.cfg.on_authenticated and self._last_token:
            logger.info('Calling on_authenticated callback')
            self.cfg.on_authenticated(self._last_token, self._last_refresh, self._last_user)
        else:
            logger.warning('_close_and_continue: no callback or no token')
        logger.info('Calling _do_close()')
        self._do_close()

    def _do_close(self) -> None:
        logger.info('_do_close: window=%s', bool(self._window))
        if self._window:
            logger.info('Closing window...')
            try:
                # Strip WindowStaysOnTopHint + FramelessWindowHint before closing.
                # On Windows, a topmost frameless window can hold mouse/keyboard
                # input focus even after hide(), leaving the desktop unresponsive.
                if sys.platform == 'win32':
                    try:
                        import ctypes
                        hwnd = int(self._window.winId())
                        # Remove WS_EX_TOPMOST (8) and WS_EX_NOACTIVATE (0x8000000)
                        GWL_EXSTYLE = -20
                        cur = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                        ctypes.windll.user32.SetWindowLongW(
                            hwnd, GWL_EXSTYLE,
                            cur & ~0x00000008 & ~0x08000000
                        )
                        # Move to non-topmost z-order
                        HWND_NOTOPMOST = -2
                        ctypes.windll.user32.SetWindowPos(
                            hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, 0x0003  # SWP_NOMOVE|SWP_NOSIZE
                        )
                    except Exception as e:
                        logger.debug('Win32 topmost clear failed: %s', e)

                self._window.closeEvent = lambda e: e.accept()
                self._window.hide()
                self._window.close()
            except Exception as e:
                logger.warning('Error while closing window: %s', e)
            self._window = None
            logger.info('Window closed')

        # Restore focus to the Windows shell so the desktop is interactive
        if sys.platform == 'win32':
            try:
                import ctypes
                shell = ctypes.windll.user32.GetShellWindow()
                if shell:
                    ctypes.windll.user32.SetForegroundWindow(shell)
            except Exception as e:
                logger.debug('Shell focus restore failed: %s', e)

        # Force-quit Qt event loop (setQuitOnLastWindowClosed=False so we must quit manually)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.exit(0)
                QtCore.QTimer.singleShot(100, app.quit)
            except Exception as e:
                logger.warning('Error while exiting Qt app: %s', e)
        logger.info('Qt event loop exit requested')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_asset_path(relative_path: str) -> str | None:
        """Resolve bundled asset path (PyInstaller) or local dev path."""
        try:
            base = getattr(sys, '_MEIPASS', None)
            if base:
                return os.path.join(base, relative_path)
            # dev: .../bosowa-agent/agent/overlay -> .../portal_bosowa
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
            return os.path.join(root, relative_path.replace('/', os.sep))
        except Exception:
            return None

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self._email_input.setEnabled(enabled)
        self._password_input.setEnabled(enabled)
        self._login_btn.setEnabled(enabled)
        self._web_login_btn.setEnabled(enabled)
        self._pin_input.setEnabled(enabled)
        self._pin_btn.setEnabled(enabled)