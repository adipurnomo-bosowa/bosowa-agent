"""System tray app + ticket dialogs for Bosowa Agent."""
from __future__ import annotations

import os
import platform
import socket
import sys
import threading
from datetime import datetime
from typing import Any, Callable

from PyQt5 import QtCore, QtGui, QtWidgets

from agent.api.tickets import create_ticket, list_my_tickets, update_ticket_note
from agent.auth.token_store import get_pin_hash_and_expiry
from agent.core.commands.usb_control import get_usb_locked_sync, set_usb_enabled_sync
from agent.core.hardware import get_mac_address
from agent.utils.logger import logger
import bcrypt

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency safety
    psutil = None


TICKET_CATEGORIES = ['Hardware', 'Software', 'Jaringan', 'Akses/Login', 'Lainnya']
TICKET_PRIORITIES = ['LOW', 'MEDIUM', 'HIGH']


class AgentTrayApp:
    """Tray UI that runs in a dedicated Qt thread."""

    def __init__(self, user: dict[str, Any] | None = None, stop_callback: Callable[[], None] | None = None):
        self.user = user or {}
        self._stop_callback = stop_callback
        self._thread: threading.Thread | None = None
        self._app: QtWidgets.QApplication | None = None
        self._tray: QtWidgets.QSystemTrayIcon | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_qt, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._app:
            QtCore.QMetaObject.invokeMethod(self._app, 'quit', QtCore.Qt.QueuedConnection)

    def _run_qt(self) -> None:
        self._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self._app.setQuitOnLastWindowClosed(False)

        icon = self._make_icon()
        self._app.setWindowIcon(icon)
        self._tray = QtWidgets.QSystemTrayIcon(icon)
        self._tray.setToolTip('Bosowa Portal Agent')

        menu = QtWidgets.QMenu()
        show_dashboard = menu.addAction('Desktop App')
        show_info = menu.addAction('Status Agent')
        menu.addSeparator()
        create_ticket_action = menu.addAction('Buat Tiket IT')
        list_ticket_action = menu.addAction('Tiket Saya')
        menu.addSeparator()
        usb_unlock_action = menu.addAction('🔑 Buka USB (PIN)')
        menu.addSeparator()
        exit_action = menu.addAction('Keluar Agent')

        show_dashboard.triggered.connect(self._show_desktop_app)
        show_info.triggered.connect(self._show_status)
        create_ticket_action.triggered.connect(self._show_create_ticket_dialog)
        list_ticket_action.triggered.connect(self._show_list_tickets_dialog)
        usb_unlock_action.triggered.connect(self._show_usb_pin_unlock)
        exit_action.triggered.connect(self._exit_agent)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        self._tray.showMessage(
            'Bosowa Portal Agent aktif',
            'Agent berjalan di background. Klik kanan icon untuk menu.',
            QtWidgets.QSystemTrayIcon.Information,
            3500,
        )

        self._app.exec_()

    def _make_icon(self) -> QtGui.QIcon:
        logo = self._resolve_asset_path('assets/PORTAL.png')
        if logo and os.path.exists(logo):
            pix = QtGui.QPixmap(logo)
            if not pix.isNull():
                return QtGui.QIcon(
                    pix.scaled(
                        64, 64,
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    )
                )
        pix = QtGui.QPixmap(64, 64)
        pix.fill(QtGui.QColor('#0A1628'))
        painter = QtGui.QPainter(pix)
        painter.setRenderHints(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor('#1E88E5'))
        painter.drawRoundedRect(6, 6, 52, 52, 10, 10)
        painter.setPen(QtGui.QColor('#FFFFFF'))
        font = QtGui.QFont('Segoe UI', 24, QtGui.QFont.Bold)
        painter.setFont(font)
        painter.drawText(pix.rect(), QtCore.Qt.AlignCenter, 'B')
        painter.end()
        return QtGui.QIcon(pix)

    @staticmethod
    def _resolve_asset_path(relative_path: str) -> str | None:
        try:
            base = getattr(sys, '_MEIPASS', None)
            if base:
                return os.path.join(base, relative_path)
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
            return os.path.join(root, relative_path.replace('/', os.sep))
        except Exception:
            return None

    def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.DoubleClick:
            self._show_desktop_app()

    def _detect_primary_ip(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(('8.8.8.8', 80))
                return s.getsockname()[0]
        except Exception:
            return '-'

    def _detect_windows_display(self) -> str:
        if os.name != 'nt':
            return f'{platform.system()} {platform.release()}'
        release = platform.release()
        version = platform.version()
        try:
            build = int(version.split('.')[-1])
            if build >= 22000:
                return f'Windows 11 (build {build})'
            return f'Windows 10 (build {build})'
        except Exception:
            return f'Windows {release}'

    def _collect_health_checks(self) -> list[dict[str, str]]:
        checks: list[dict[str, str]] = []
        if not psutil:
            return [{'name': 'System metrics', 'status': 'WARN', 'detail': 'psutil tidak tersedia'}]

        try:
            cpu_pct = float(psutil.cpu_percent(interval=0.1))
            checks.append({
                'name': 'CPU load',
                'status': 'OK' if cpu_pct < 85 else 'WARN',
                'detail': f'{cpu_pct:.0f}% digunakan',
            })
        except Exception:
            checks.append({'name': 'CPU load', 'status': 'WARN', 'detail': 'Tidak terbaca'})

        try:
            mem = psutil.virtual_memory()
            checks.append({
                'name': 'Memory',
                'status': 'OK' if mem.percent < 90 else 'WARN',
                'detail': f'{mem.percent:.0f}% ({round(mem.available / (1024**3), 1)} GB available)',
            })
        except Exception:
            checks.append({'name': 'Memory', 'status': 'WARN', 'detail': 'Tidak terbaca'})

        try:
            disk = psutil.disk_usage('C:\\')
            checks.append({
                'name': 'Disk C:',
                'status': 'OK' if disk.percent < 90 else 'WARN',
                'detail': f'{disk.percent:.0f}% terpakai, free {round(disk.free / (1024**3), 1)} GB',
            })
        except Exception:
            checks.append({'name': 'Disk C:', 'status': 'WARN', 'detail': 'Tidak terbaca'})

        ip = self._detect_primary_ip()
        checks.append({
            'name': 'Network',
            'status': 'OK' if ip != '-' else 'WARN',
            'detail': f'IP {ip}' if ip != '-' else 'Koneksi jaringan tidak terdeteksi',
        })

        try:
            boot = datetime.fromtimestamp(psutil.boot_time())
            uptime_h = (datetime.now() - boot).total_seconds() / 3600
            checks.append({
                'name': 'System uptime',
                'status': 'OK',
                'detail': f'{uptime_h:.1f} jam sejak boot',
            })
        except Exception:
            checks.append({'name': 'System uptime', 'status': 'WARN', 'detail': 'Tidak terbaca'})

        return checks

    def _collect_device_summary(self) -> dict[str, str]:
        cpu = '-'
        ram = '-'
        disk = '-'
        if psutil:
            try:
                cpu = f'{psutil.cpu_percent(interval=0.1):.0f}%'
            except Exception:
                cpu = '-'
            try:
                vm = psutil.virtual_memory()
                ram = f'{vm.percent:.0f}% ({round(vm.used / (1024**3), 1)} / {round(vm.total / (1024**3), 1)} GB)'
            except Exception:
                ram = '-'
            try:
                du = psutil.disk_usage('C:\\')
                disk = f'{du.percent:.0f}% ({round(du.used / (1024**3), 1)} / {round(du.total / (1024**3), 1)} GB)'
            except Exception:
                disk = '-'

        return {
            'Hostname': socket.gethostname() or '-',
            'OS': self._detect_windows_display(),
            'MAC': get_mac_address(),
            'IP': self._detect_primary_ip(),
            'CPU': cpu,
            'RAM': ram,
            'Disk C:': disk,
            'Last update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    def _show_desktop_app(self) -> None:
        dlg = QtWidgets.QDialog()
        dlg.setWindowTitle('Bosowa Portal Desktop')
        icon = self._make_icon()
        dlg.setWindowIcon(icon)
        dlg.resize(1040, 640)
        dlg.setStyleSheet(
            '''
            QDialog { background: #0B1220; color: #E2E8F0; }
            QLabel { color: #E2E8F0; }
            QFrame#Sidebar { background: #0F172A; border-right: 1px solid #1F2937; }
            QFrame#MainPanel { background: #0B1220; }
            QFrame#Card { background: #111827; border: 1px solid #1F2937; border-radius: 10px; }
            QPushButton { background: #1D4ED8; color: white; border: none; border-radius: 6px; padding: 8px 12px; }
            QPushButton:hover { background: #2563EB; }
            QListWidget { background: transparent; border: none; color: #CBD5E1; }
            QListWidget::item { padding: 11px 10px; border-radius: 7px; }
            QListWidget::item:selected { background: #1E293B; color: #FFFFFF; }
            QGroupBox { border: 1px solid #1F2937; border-radius: 8px; margin-top: 10px; padding-top: 14px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #93C5FD; }
            '''
        )

        root = QtWidgets.QHBoxLayout(dlg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName('Sidebar')
        sidebar.setFixedWidth(220)
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 16, 14, 16)
        sidebar_layout.setSpacing(12)

        app_title = QtWidgets.QLabel('Bosowa Portal')
        app_title.setFont(QtGui.QFont('Segoe UI', 12, QtGui.QFont.Bold))
        sidebar_layout.addWidget(app_title)
        app_sub = QtWidgets.QLabel('Endpoint Desktop Console')
        app_sub.setStyleSheet('color: #94A3B8; font-size: 11px;')
        sidebar_layout.addWidget(app_sub)

        nav_titles = ['Dashboard', 'Device', 'User', 'Health Check', 'Tickets']
        nav_subtitles = [
            'Ringkasan endpoint, user aktif, dan health check (auto refresh 5 detik)',
            'Detail perangkat dan spesifikasi sistem',
            'Profil pengguna yang sedang login',
            'Pemeriksaan kesehatan sistem secara berkala',
            'Daftar tiket IT yang Anda buat',
        ]

        nav = QtWidgets.QListWidget()
        nav.addItems(nav_titles)
        nav.setCurrentRow(0)
        sidebar_layout.addWidget(nav, 1)

        close_btn = QtWidgets.QPushButton('Tutup')
        sidebar_layout.addWidget(close_btn)
        root.addWidget(sidebar)

        panel = QtWidgets.QFrame()
        panel.setObjectName('MainPanel')
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 16, 18, 16)
        panel_layout.setSpacing(12)

        # Header row: back button (visible on non-Dashboard pages) + dynamic title
        header_row = QtWidgets.QHBoxLayout()
        header_row.setSpacing(8)
        back_btn = QtWidgets.QPushButton('← Kembali')
        back_btn.setStyleSheet(
            'QPushButton { background: #334155; color: #E2E8F0; border-radius: 6px; padding: 6px 12px; }'
            'QPushButton:hover { background: #475569; }'
        )
        back_btn.setVisible(False)
        header_row.addWidget(back_btn)

        header = QtWidgets.QLabel(nav_titles[0])
        header.setFont(QtGui.QFont('Segoe UI', 14, QtGui.QFont.Bold))
        header_row.addWidget(header)
        header_row.addStretch(1)
        panel_layout.addLayout(header_row)

        header_sub = QtWidgets.QLabel(nav_subtitles[0])
        header_sub.setStyleSheet('color: #94A3B8;')
        panel_layout.addWidget(header_sub)

        cards_row = QtWidgets.QHBoxLayout()
        panel_layout.addLayout(cards_row)

        online_card = QtWidgets.QFrame()
        online_card.setObjectName('Card')
        online_layout = QtWidgets.QVBoxLayout(online_card)
        online_layout.addWidget(QtWidgets.QLabel('Status Agent'))
        status_value = QtWidgets.QLabel('ONLINE')
        status_value.setFont(QtGui.QFont('Segoe UI', 16, QtGui.QFont.Bold))
        status_value.setStyleSheet('color: #22C55E;')
        online_layout.addWidget(status_value)
        cards_row.addWidget(online_card, 1)

        uptime_card = QtWidgets.QFrame()
        uptime_card.setObjectName('Card')
        uptime_layout = QtWidgets.QVBoxLayout(uptime_card)
        uptime_layout.addWidget(QtWidgets.QLabel('Sumber Data'))
        source_value = QtWidgets.QLabel('Local Device')
        source_value.setFont(QtGui.QFont('Segoe UI', 16, QtGui.QFont.Bold))
        uptime_layout.addWidget(source_value)
        cards_row.addWidget(uptime_card, 1)

        action_card = QtWidgets.QFrame()
        action_card.setObjectName('Card')
        action_layout = QtWidgets.QVBoxLayout(action_card)
        action_layout.addWidget(QtWidgets.QLabel('Quick Actions'))
        quick_ticket_btn = QtWidgets.QPushButton('Buat Tiket IT')
        quick_list_btn = QtWidgets.QPushButton('Lihat Tiket Saya')
        quick_list_btn.setStyleSheet('QPushButton { background: #334155; color: #E2E8F0; border-radius: 6px; padding: 8px 12px; } QPushButton:hover { background: #475569; }')
        action_layout.addWidget(quick_ticket_btn)
        action_layout.addWidget(quick_list_btn)
        action_layout.addStretch(1)
        cards_row.addWidget(action_card, 1)

        content_stack = QtWidgets.QStackedWidget()
        panel_layout.addWidget(content_stack, 1)

        dashboard_page = QtWidgets.QWidget()
        dashboard_layout = QtWidgets.QVBoxLayout(dashboard_page)
        dashboard_layout.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.setSpacing(10)

        user_group = QtWidgets.QGroupBox('Summary User')
        user_form = QtWidgets.QFormLayout(user_group)
        user_form.addRow('Nama', QtWidgets.QLabel(str(self.user.get('name') or '-')))
        user_form.addRow('Email', QtWidgets.QLabel(str(self.user.get('email') or '-')))
        user_form.addRow('Employee ID', QtWidgets.QLabel(str(self.user.get('employeeId') or '-')))
        user_form.addRow('Business Unit', QtWidgets.QLabel(str(self.user.get('businessUnit') or '-')))
        dashboard_layout.addWidget(user_group)

        device_group = QtWidgets.QGroupBox('Summary Laptop')
        device_form = QtWidgets.QFormLayout(device_group)
        summary_labels: dict[str, QtWidgets.QLabel] = {}
        for key in ['Hostname', 'OS', 'MAC', 'IP', 'CPU', 'RAM', 'Disk C:', 'Last update']:
            lbl = QtWidgets.QLabel('-')
            summary_labels[key] = lbl
            device_form.addRow(key, lbl)
        dashboard_layout.addWidget(device_group)

        health_group = QtWidgets.QGroupBox('Quick Health Check')
        health_layout = QtWidgets.QVBoxLayout(health_group)
        health_summary_label = QtWidgets.QLabel('-')
        health_summary_label.setStyleSheet('color: #93C5FD;')
        health_layout.addWidget(health_summary_label)
        dashboard_layout.addWidget(health_group)
        content_stack.addWidget(dashboard_page)

        device_page = QtWidgets.QWidget()
        device_layout = QtWidgets.QVBoxLayout(device_page)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(10)
        device_card = QtWidgets.QGroupBox('Detail Device')
        device_card_form = QtWidgets.QFormLayout(device_card)
        device_labels: dict[str, QtWidgets.QLabel] = {}
        for key in ['Hostname', 'OS', 'MAC', 'IP', 'CPU', 'RAM', 'Disk C:', 'Last update']:
            lbl = QtWidgets.QLabel('-')
            device_labels[key] = lbl
            device_card_form.addRow(key, lbl)
        device_layout.addWidget(device_card)
        device_layout.addStretch(1)
        content_stack.addWidget(device_page)

        user_page = QtWidgets.QWidget()
        user_layout = QtWidgets.QVBoxLayout(user_page)
        user_layout.setContentsMargins(0, 0, 0, 0)
        user_layout.setSpacing(10)
        user_card = QtWidgets.QGroupBox('Detail User')
        user_card_form = QtWidgets.QFormLayout(user_card)
        user_card_form.addRow('Nama', QtWidgets.QLabel(str(self.user.get('name') or '-')))
        user_card_form.addRow('Email', QtWidgets.QLabel(str(self.user.get('email') or '-')))
        user_card_form.addRow('Employee ID', QtWidgets.QLabel(str(self.user.get('employeeId') or '-')))
        user_card_form.addRow('Business Unit', QtWidgets.QLabel(str(self.user.get('businessUnit') or '-')))
        user_card_form.addRow('Role', QtWidgets.QLabel(str(self.user.get('role') or '-')))
        user_layout.addWidget(user_card)
        user_layout.addStretch(1)
        content_stack.addWidget(user_page)

        health_page = QtWidgets.QWidget()
        health_layout_page = QtWidgets.QVBoxLayout(health_page)
        health_layout_page.setContentsMargins(0, 0, 0, 0)
        health_layout_page.setSpacing(10)
        health_header = QtWidgets.QLabel('General Health Check')
        health_header.setStyleSheet('color: #93C5FD;')
        health_layout_page.addWidget(health_header)
        health_table = QtWidgets.QTableWidget()
        health_table.setColumnCount(3)
        health_table.setHorizontalHeaderLabels(['Check', 'Status', 'Detail'])
        health_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        health_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        health_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        health_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        health_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        health_layout_page.addWidget(health_table)
        content_stack.addWidget(health_page)

        tickets_page = QtWidgets.QWidget()
        tickets_layout = QtWidgets.QVBoxLayout(tickets_page)
        tickets_layout.setContentsMargins(0, 0, 0, 0)
        tickets_layout.setSpacing(10)
        ticket_header = QtWidgets.QLabel('Tiket Saya')
        ticket_header.setStyleSheet('color: #93C5FD; font-weight: 700;')
        tickets_layout.addWidget(ticket_header)

        ticket_desc = QtWidgets.QLabel('Kelola tiket IT langsung dari desktop agent.')
        ticket_desc.setStyleSheet('color: #94A3B8; font-size: 11px;')
        tickets_layout.addWidget(ticket_desc)

        ticket_actions = QtWidgets.QHBoxLayout()
        ticket_create_btn = QtWidgets.QPushButton('Buat Tiket IT')
        ticket_refresh_btn = QtWidgets.QPushButton('Refresh')
        ticket_refresh_btn.setStyleSheet('QPushButton { background: #334155; color: #E2E8F0; border-radius: 6px; padding: 8px 12px; } QPushButton:hover { background: #475569; }')
        ticket_actions.addWidget(ticket_create_btn)
        ticket_actions.addWidget(ticket_refresh_btn)
        ticket_actions.addStretch(1)
        tickets_layout.addLayout(ticket_actions)

        ticket_table = QtWidgets.QTableWidget()
        ticket_table.setColumnCount(5)
        ticket_table.setHorizontalHeaderLabels(['Status', 'Prioritas', 'Kategori', 'Judul', 'Dibuat'])
        ticket_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        ticket_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        ticket_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        ticket_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        ticket_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        ticket_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        ticket_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        ticket_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        ticket_table.setToolTip('Klik baris untuk lihat detail / balas catatan admin')
        tickets_layout.addWidget(ticket_table)
        ticket_data_desktop: list[dict] = []

        content_stack.addWidget(tickets_page)

        refresh_btn = QtWidgets.QPushButton('Refresh Sekarang')
        panel_layout.addWidget(refresh_btn, 0, QtCore.Qt.AlignRight)
        root.addWidget(panel, 1)

        def refresh_summary() -> None:
            data = self._collect_device_summary()
            for key, lbl in summary_labels.items():
                lbl.setText(data.get(key, '-'))
            for key, lbl in device_labels.items():
                lbl.setText(data.get(key, '-'))
            checks = self._collect_health_checks()
            ok_count = sum(1 for c in checks if c['status'] == 'OK')
            health_summary_label.setText(f'{ok_count}/{len(checks)} checks OK')

            health_table.setRowCount(len(checks))
            for i, c in enumerate(checks):
                health_table.setItem(i, 0, QtWidgets.QTableWidgetItem(c['name']))
                status_item = QtWidgets.QTableWidgetItem(c['status'])
                if c['status'] == 'OK':
                    status_item.setForeground(QtGui.QColor('#22C55E'))
                elif c['status'] == 'WARN':
                    status_item.setForeground(QtGui.QColor('#F59E0B'))
                else:
                    status_item.setForeground(QtGui.QColor('#EF4444'))
                health_table.setItem(i, 1, status_item)
                health_table.setItem(i, 2, QtWidgets.QTableWidgetItem(c['detail']))

        def on_nav_changed(row: int) -> None:
            if 0 <= row < content_stack.count():
                content_stack.setCurrentIndex(row)
                header.setText(nav_titles[row])
                header_sub.setText(nav_subtitles[row])
                back_btn.setVisible(row != 0)
                # Auto-load tickets when entering the Tickets tab
                if nav_titles[row] == 'Tickets':
                    load_tickets()

        def load_tickets() -> None:
            try:
                tickets = list_my_tickets()
                ticket_data_desktop.clear()
                ticket_data_desktop.extend(tickets)
                ticket_table.setRowCount(len(tickets))
                for i, t in enumerate(tickets):
                    created = t.get('createdAt')
                    try:
                        created_fmt = datetime.fromisoformat(str(created).replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        created_fmt = str(created or '-')
                    values = [
                        str(t.get('status', '-')),
                        str(t.get('priority', '-')),
                        str(t.get('category', '-')),
                        str(t.get('title', '-')),
                        created_fmt,
                    ]
                    for col, val in enumerate(values):
                        item = QtWidgets.QTableWidgetItem(val)
                        if t.get('adminNote'):
                            item.setForeground(QtGui.QColor('#64B5F6'))
                        ticket_table.setItem(i, col, item)
            except Exception as e:
                logger.warning('Load desktop tickets failed: %s', e)
                QtWidgets.QMessageBox.warning(dlg, 'Tickets', str(e))

        def go_to_tickets() -> None:
            tickets_index = nav_titles.index('Tickets')
            nav.setCurrentRow(tickets_index)

        def go_back_to_dashboard() -> None:
            nav.setCurrentRow(0)

        nav.currentRowChanged.connect(on_nav_changed)
        refresh_btn.clicked.connect(refresh_summary)
        back_btn.clicked.connect(go_back_to_dashboard)
        quick_ticket_btn.clicked.connect(self._show_create_ticket_dialog)
        # Quick "Lihat Tiket" now navigates internally instead of opening a separate window
        quick_list_btn.clicked.connect(go_to_tickets)
        ticket_create_btn.clicked.connect(self._show_create_ticket_dialog)
        ticket_refresh_btn.clicked.connect(load_tickets)

        def on_desktop_ticket_clicked(row: int) -> None:
            if row < 0 or row >= len(ticket_data_desktop):
                return
            self._show_ticket_detail(ticket_data_desktop[row], load_tickets)

        ticket_table.cellClicked.connect(on_desktop_ticket_clicked)
        close_btn.clicked.connect(dlg.accept)

        timer = QtCore.QTimer(dlg)
        timer.setInterval(5000)
        timer.timeout.connect(refresh_summary)
        timer.start()

        refresh_summary()
        load_tickets()
        dlg.exec_()

    def _show_status(self) -> None:
        mac = get_mac_address()
        name = self.user.get('name') or '-'
        emp = self.user.get('employeeId') or '-'
        msg = f'User: {name}\nEmployee ID: {emp}\nMAC: {mac}'
        QtWidgets.QMessageBox.information(None, 'Status BosowAgent', msg)

    def _show_create_ticket_dialog(self) -> None:
        dlg = QtWidgets.QDialog()
        dlg.setWindowTitle('Buat Tiket IT')
        dlg.setWindowIcon(self._make_icon())
        dlg.setMinimumWidth(460)
        layout = QtWidgets.QVBoxLayout(dlg)

        title_edit = QtWidgets.QLineEdit()
        title_edit.setPlaceholderText('Judul masalah')
        category_box = QtWidgets.QComboBox()
        category_box.addItems(TICKET_CATEGORIES)
        priority_box = QtWidgets.QComboBox()
        priority_box.addItems(TICKET_PRIORITIES)
        desc_edit = QtWidgets.QTextEdit()
        desc_edit.setPlaceholderText('Jelaskan detail masalah...')
        desc_edit.setMinimumHeight(120)
        mac_label = QtWidgets.QLabel(f'Device: {get_mac_address()}')

        form = QtWidgets.QFormLayout()
        form.addRow('Judul', title_edit)
        form.addRow('Kategori', category_box)
        form.addRow('Prioritas', priority_box)
        form.addRow('Deskripsi', desc_edit)
        form.addRow('', mac_label)
        layout.addLayout(form)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        submit_btn = QtWidgets.QPushButton('Kirim Tiket')
        cancel_btn = QtWidgets.QPushButton('Batal')
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(submit_btn)
        layout.addLayout(btn_row)

        cancel_btn.clicked.connect(dlg.reject)

        def submit() -> None:
            title = title_edit.text().strip()
            desc = desc_edit.toPlainText().strip()
            if not title or not desc:
                QtWidgets.QMessageBox.warning(dlg, 'Validasi', 'Judul dan deskripsi wajib diisi.')
                return
            submit_btn.setEnabled(False)
            try:
                t = create_ticket(
                    title=title,
                    category=category_box.currentText(),
                    description=desc,
                    priority=priority_box.currentText(),
                    device_mac=get_mac_address(),
                )
                QtWidgets.QMessageBox.information(
                    dlg,
                    'Berhasil',
                    f"Tiket berhasil dibuat.\nID: {t.get('id', '-')}",
                )
                dlg.accept()
            except Exception as e:
                logger.warning('Create ticket failed: %s', e)
                QtWidgets.QMessageBox.critical(dlg, 'Gagal', str(e))
            finally:
                submit_btn.setEnabled(True)

        submit_btn.clicked.connect(submit)
        dlg.exec_()

    def _show_list_tickets_dialog(self) -> None:
        dlg = QtWidgets.QDialog()
        dlg.setWindowTitle('Tiket Saya')
        dlg.setWindowIcon(self._make_icon())
        dlg.resize(780, 460)
        layout = QtWidgets.QVBoxLayout(dlg)

        table = QtWidgets.QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(['Status', 'Prioritas', 'Kategori', 'Judul', 'Dibuat'])
        table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setToolTip('Klik baris untuk lihat detail / balas catatan admin')
        layout.addWidget(table)

        ticket_data: list[dict] = []

        btn_row = QtWidgets.QHBoxLayout()
        refresh_btn = QtWidgets.QPushButton('Refresh')
        close_btn = QtWidgets.QPushButton('Tutup')
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        close_btn.clicked.connect(dlg.accept)

        def load() -> None:
            try:
                tickets = list_my_tickets()
                ticket_data.clear()
                ticket_data.extend(tickets)
                table.setRowCount(len(tickets))
                for i, t in enumerate(tickets):
                    created = t.get('createdAt')
                    try:
                        created_fmt = datetime.fromisoformat(str(created).replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        created_fmt = str(created or '-')
                    values = [
                        str(t.get('status', '-')),
                        str(t.get('priority', '-')),
                        str(t.get('category', '-')),
                        str(t.get('title', '-')),
                        created_fmt,
                    ]
                    for col, val in enumerate(values):
                        item = QtWidgets.QTableWidgetItem(val)
                        # Highlight rows with adminNote
                        if t.get('adminNote'):
                            item.setForeground(QtGui.QColor('#64B5F6'))
                        table.setItem(i, col, item)
            except Exception as e:
                logger.warning('List tickets failed: %s', e)
                QtWidgets.QMessageBox.critical(dlg, 'Gagal', str(e))

        def on_row_clicked(row: int) -> None:
            if row < 0 or row >= len(ticket_data):
                return
            self._show_ticket_detail(ticket_data[row], load)

        table.cellClicked.connect(on_row_clicked)
        refresh_btn.clicked.connect(load)
        load()
        dlg.exec_()

    def _show_ticket_detail(self, ticket: dict, refresh_callback=None) -> None:
        dlg = QtWidgets.QDialog()
        dlg.setWindowTitle(f'Detail Tiket — {ticket.get("title", "")}')
        dlg.setWindowIcon(self._make_icon())
        dlg.resize(520, 420)
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.setSpacing(10)

        def row(label: str, value: str) -> QtWidgets.QWidget:
            w = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            lbl = QtWidgets.QLabel(label + ':')
            lbl.setFixedWidth(90)
            lbl.setStyleSheet('color: #90CAF9; font-size: 12px; font-weight: 600;')
            val = QtWidgets.QLabel(value or '—')
            val.setWordWrap(True)
            val.setStyleSheet('color: #E2E8F0; font-size: 12px;')
            h.addWidget(lbl)
            h.addWidget(val, 1)
            return w

        layout.addWidget(row('Status', ticket.get('status', '')))
        layout.addWidget(row('Prioritas', ticket.get('priority', '')))
        layout.addWidget(row('Kategori', ticket.get('category', '')))
        layout.addWidget(row('Judul', ticket.get('title', '')))

        desc_label = QtWidgets.QLabel('Deskripsi:')
        desc_label.setStyleSheet('color: #90CAF9; font-size: 12px; font-weight: 600;')
        layout.addWidget(desc_label)
        desc_text = QtWidgets.QPlainTextEdit(ticket.get('description', ''))
        desc_text.setReadOnly(True)
        desc_text.setMaximumHeight(70)
        desc_text.setStyleSheet('background: rgba(255,255,255,0.04); color: #CBD5E1; font-size: 12px; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px;')
        layout.addWidget(desc_text)

        admin_note = ticket.get('adminNote') or ''
        if admin_note:
            sep = QtWidgets.QFrame()
            sep.setFrameShape(QtWidgets.QFrame.HLine)
            sep.setStyleSheet('background: rgba(100,181,246,0.3);')
            layout.addWidget(sep)
            admin_lbl = QtWidgets.QLabel('📋 Catatan Admin:')
            admin_lbl.setStyleSheet('color: #64B5F6; font-size: 12px; font-weight: 700;')
            layout.addWidget(admin_lbl)
            admin_text = QtWidgets.QPlainTextEdit(admin_note)
            admin_text.setReadOnly(True)
            admin_text.setMaximumHeight(70)
            admin_text.setStyleSheet('background: rgba(30,136,229,0.08); color: #90CAF9; font-size: 12px; border: 1px solid rgba(30,136,229,0.2); border-radius: 6px;')
            layout.addWidget(admin_text)

        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.HLine)
        sep2.setStyleSheet('background: rgba(255,255,255,0.1);')
        layout.addWidget(sep2)

        reply_lbl = QtWidgets.QLabel('💬 Pesan kamu ke admin:')
        reply_lbl.setStyleSheet('color: #A5B4FC; font-size: 12px; font-weight: 600;')
        layout.addWidget(reply_lbl)

        existing_note = ticket.get('userNote') or ''
        reply_input = QtWidgets.QPlainTextEdit(existing_note)
        reply_input.setPlaceholderText('Tulis pesan atau update ke admin di sini…')
        reply_input.setMaximumHeight(80)
        reply_input.setStyleSheet('background: rgba(255,255,255,0.06); color: #E2E8F0; font-size: 12px; border: 1px solid rgba(255,255,255,0.15); border-radius: 6px; padding: 4px 8px;')
        layout.addWidget(reply_input)

        status_lbl = QtWidgets.QLabel('')
        status_lbl.setStyleSheet('font-size: 11px; color: #90CAF9;')
        layout.addWidget(status_lbl)

        btn_row = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton('Kirim Pesan')
        close_btn2 = QtWidgets.QPushButton('Tutup')
        btn_row.addWidget(save_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn2)
        layout.addLayout(btn_row)
        close_btn2.clicked.connect(dlg.accept)

        def save_note() -> None:
            note = reply_input.toPlainText().strip()
            save_btn.setEnabled(False)
            status_lbl.setText('Mengirim…')

            def do_save():
                ok = False
                try:
                    ok = update_ticket_note(ticket['id'], note)
                except Exception as e:
                    logger.warning('update_ticket_note failed: %s', e)
                QtCore.QMetaObject.invokeMethod(
                    dlg, '_on_save_done',
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(bool, ok),
                )

            dlg._on_save_done = lambda ok: (  # type: ignore[attr-defined]
                status_lbl.setText('✅ Pesan terkirim.' if ok else '❌ Gagal mengirim.'),
                save_btn.setEnabled(True),
                (refresh_callback() if refresh_callback and ok else None),
            )

            threading.Thread(target=do_save, daemon=True).start()

        save_btn.clicked.connect(save_note)
        dlg.exec_()

    def _show_usb_pin_unlock(self) -> None:
        """Allow user to unlock USB mass storage using cached PIN (works offline)."""
        locked = get_usb_locked_sync()
        if locked is None:
            QtWidgets.QMessageBox.warning(
                None,
                'USB Status',
                'Tidak dapat membaca status USB dari registry.\n'
                'Pastikan agent berjalan sebagai Administrator.',
            )
            return
        if not locked:
            QtWidgets.QMessageBox.information(
                None,
                'USB Sudah Aktif',
                'USB mass storage sudah aktif — tidak perlu dibuka.',
            )
            return

        pin_data = get_pin_hash_and_expiry()
        if not pin_data:
            # Cek apakah online
            online = self._detect_primary_ip() != '-'
            if online:
                answer = QtWidgets.QMessageBox.question(
                    None,
                    'PIN Tidak Tersedia',
                    'PIN belum diset atau sudah expired.\n\n'
                    'Ingin membuat tiket IT untuk meminta admin set PIN / buka USB?',
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.Yes,
                )
                if answer == QtWidgets.QMessageBox.Yes:
                    self._show_create_ticket_dialog()
            else:
                QtWidgets.QMessageBox.warning(
                    None,
                    'PIN Tidak Tersedia — Offline',
                    'PIN belum diset atau sudah expired.\n'
                    'Perangkat sedang OFFLINE — tidak bisa membuat tiket.\n\n'
                    'Hubungi IT Admin / IT Staff secara langsung\n'
                    'untuk meminta PIN perangkat Anda diset melalui Portal Bosowa.',
                )
            return

        pin_hash, valid_until = pin_data
        pin, ok = QtWidgets.QInputDialog.getText(
            None,
            'Buka USB dengan PIN',
            f'Masukkan PIN untuk mengaktifkan USB mass storage:\n(PIN berlaku hingga {valid_until.strftime("%Y-%m-%d %H:%M")})',
            QtWidgets.QLineEdit.Password,
        )
        if not ok:
            return

        try:
            valid_pin = bool(pin.strip()) and bcrypt.checkpw(pin.strip().encode(), pin_hash)
        except Exception as e:
            logger.warning('PIN check failed: %s', e)
            valid_pin = False

        if not valid_pin:
            QtWidgets.QMessageBox.critical(None, 'PIN Salah', 'PIN tidak valid.')
            return

        success = set_usb_enabled_sync()
        if success:
            QtWidgets.QMessageBox.information(
                None,
                'USB Diaktifkan',
                'USB mass storage berhasil diaktifkan.\n'
                'Restart mungkin diperlukan agar perubahan berlaku penuh.',
            )
        else:
            answer = QtWidgets.QMessageBox.question(
                None,
                'Gagal Mengaktifkan USB',
                'Gagal mengubah registry.\n'
                'Agent mungkin tidak memiliki hak administrator.\n\n'
                'Ingin membuat tiket IT untuk meminta bantuan admin?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if answer == QtWidgets.QMessageBox.Yes:
                self._show_create_ticket_dialog()

    def _exit_agent(self) -> None:
        # Require PIN to stop agent (prevents users from killing background service).
        pin_data = get_pin_hash_and_expiry()
        if not pin_data:
            QtWidgets.QMessageBox.warning(
                None,
                'PIN belum tersedia',
                'PIN belum diset / sudah expired. Hubungi IT untuk set PIN perangkat sebelum agent bisa ditutup.',
            )
            return
        pin_hash, _valid_until = pin_data
        pin, ok = QtWidgets.QInputDialog.getText(
            None,
            'Konfirmasi PIN',
            'Masukkan PIN untuk menutup BosowAgent:',
            QtWidgets.QLineEdit.Password,
        )
        if not ok:
            return
        try:
            valid_pin = bool(pin.strip()) and bcrypt.checkpw(pin.strip().encode(), pin_hash)
        except Exception as e:
            logger.warning('PIN check failed: %s', e)
            valid_pin = False
        if not valid_pin:
            QtWidgets.QMessageBox.critical(None, 'PIN salah', 'PIN tidak valid.')
            return

        answer = QtWidgets.QMessageBox.question(
            None,
            'Keluar Agent',
            'Yakin ingin menutup BosowAgent?',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        logger.info('Exit requested from tray menu')
        if self._tray:
            try:
                self._tray.showMessage(
                    'Menutup BosowAgent',
                    'Sedang menghentikan service…',
                    QtWidgets.QSystemTrayIcon.Information,
                    2000,
                )
            except Exception:
                pass
        if self._stop_callback:
            try:
                self._stop_callback()
                # allow graceful shutdown
                QtCore.QTimer.singleShot(3500, lambda: os._exit(0))
                return
            except Exception as e:
                logger.warning('stop_callback failed: %s', e)
        os._exit(0)
