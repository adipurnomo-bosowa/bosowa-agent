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

from agent.api.tickets import create_ticket, list_my_tickets
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
        from agent.api.tickets import get_messages, send_message, start_chat as api_start_chat

        dlg = QtWidgets.QDialog()
        dlg.setWindowTitle(ticket.get('title', 'Detail Tiket'))
        dlg.setWindowIcon(self._make_icon())
        dlg.setMinimumSize(500, 580)
        dlg.resize(540, 640)
        dlg.setStyleSheet('background: #0F1729; color: #E2E8F0;')

        outer = QtWidgets.QVBoxLayout(dlg)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        # ── Header ───────────────────────────────────────────────
        header_w = QtWidgets.QWidget()
        header_w.setStyleSheet('background: #1A2744; border-bottom: 1px solid rgba(255,255,255,0.1);')
        header_lay = QtWidgets.QVBoxLayout(header_w)
        header_lay.setContentsMargins(16, 14, 16, 12)
        header_lay.setSpacing(6)

        title_lbl = QtWidgets.QLabel(ticket.get('title', ''))
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet('color: #E2E8F0; font-size: 14px; font-weight: 700;')
        header_lay.addWidget(title_lbl)

        meta_row = QtWidgets.QHBoxLayout()
        meta_row.setSpacing(8)
        status_val = ticket.get('status', '')
        status_colors = {'OPEN': '#34D399', 'IN_PROGRESS': '#FBBF24', 'CLOSED': '#94A3B8', 'RESOLVED': '#60A5FA'}
        s_color = status_colors.get(status_val, '#94A3B8')
        status_badge = QtWidgets.QLabel(status_val)
        status_badge.setStyleSheet(
            f'color: {s_color}; background: rgba(255,255,255,0.07); font-size: 10px; font-weight: 700;'
            f' padding: 2px 8px; border-radius: 10px; border: 1px solid {s_color}44;'
        )
        meta_row.addWidget(status_badge)
        cat_lbl = QtWidgets.QLabel(ticket.get('category', ''))
        cat_lbl.setStyleSheet('color: #94A3B8; font-size: 11px;')
        meta_row.addWidget(cat_lbl)
        pri_val = ticket.get('priority', '')
        pri_colors = {'HIGH': '#F87171', 'MEDIUM': '#FBBF24', 'LOW': '#60A5FA'}
        p_color = pri_colors.get(pri_val, '#94A3B8')
        pri_lbl = QtWidgets.QLabel(f'● {pri_val}')
        pri_lbl.setStyleSheet(f'color: {p_color}; font-size: 11px; font-weight: 600;')
        meta_row.addWidget(pri_lbl)
        meta_row.addStretch()
        header_lay.addLayout(meta_row)
        outer.addWidget(header_w)

        # ── Chat bubble area ──────────────────────────────────────
        bubble_scroll = QtWidgets.QScrollArea()
        bubble_scroll.setWidgetResizable(True)
        bubble_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        bubble_scroll.setStyleSheet('background: #0F1729; border: none;')
        bubble_container = QtWidgets.QWidget()
        bubble_container.setStyleSheet('background: #0F1729;')
        bubble_vbox = QtWidgets.QVBoxLayout(bubble_container)
        bubble_vbox.setContentsMargins(12, 12, 12, 12)
        bubble_vbox.setSpacing(8)
        bubble_vbox.addStretch(1)
        bubble_scroll.setWidget(bubble_container)
        outer.addWidget(bubble_scroll, 1)

        # ── Mulai Chat button (only when chatStarted=False) ───────
        start_chat_btn = QtWidgets.QPushButton('Mulai Chat')
        start_chat_btn.setStyleSheet(
            'QPushButton { background: #2563EB; color: white; border: none;'
            ' border-radius: 8px; padding: 10px 24px; font-size: 13px; font-weight: 600; }'
            ' QPushButton:hover { background: #1D4ED8; }'
        )
        start_chat_btn.setVisible(False)
        outer.addWidget(start_chat_btn, 0, QtCore.Qt.AlignCenter)

        # ── Input area ────────────────────────────────────────────
        input_w = QtWidgets.QWidget()
        input_w.setStyleSheet('background: #1A2235; border-top: 1px solid rgba(255,255,255,0.08);')
        input_lay = QtWidgets.QHBoxLayout(input_w)
        input_lay.setContentsMargins(12, 10, 12, 10)
        input_lay.setSpacing(8)

        chat_input = QtWidgets.QPlainTextEdit()
        chat_input.setMaximumHeight(64)
        chat_input.setPlaceholderText('Tulis pesan (Enter kirim, Shift+Enter baris baru)...')
        chat_input.setStyleSheet(
            'background: rgba(255,255,255,0.05); color: #E2E8F0; font-size: 12px;'
            ' border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 4px 8px;'
        )
        send_btn = QtWidgets.QPushButton('Kirim')
        send_btn.setFixedWidth(72)
        send_btn.setStyleSheet(
            'QPushButton { background: #2563EB; color: white; border: none;'
            ' border-radius: 6px; padding: 6px 12px; font-size: 12px; font-weight: 600; }'
            ' QPushButton:hover { background: #1D4ED8; }'
            ' QPushButton:disabled { background: #1e3a6e; color: #64748B; }'
        )
        input_lay.addWidget(chat_input, 1)
        input_lay.addWidget(send_btn)
        outer.addWidget(input_w)

        # ── State ─────────────────────────────────────────────────
        _t = [dict(ticket)]
        _msg_count = [0]

        def _make_bubble(msg: dict) -> QtWidgets.QWidget:
            is_admin = msg.get('senderType') == 'admin'
            try:
                dt = datetime.fromisoformat(str(msg.get('createdAt', '')).replace('Z', '+00:00'))
                time_str = dt.strftime('%H:%M')
            except Exception:
                time_str = ''
            container = QtWidgets.QWidget()
            container.setStyleSheet('background: transparent;')
            h = QtWidgets.QHBoxLayout(container)
            h.setContentsMargins(0, 0, 0, 0)
            bubble = QtWidgets.QWidget()
            bubble.setMaximumWidth(380)
            b = QtWidgets.QVBoxLayout(bubble)
            b.setContentsMargins(10, 7, 10, 7)
            b.setSpacing(3)
            sender_lbl = QtWidgets.QLabel(f"{msg.get('senderName', '?')} · {time_str}")
            sender_lbl.setStyleSheet(
                ('color: #67E8F9;' if is_admin else 'color: #86EFAC;') +
                ' font-size: 10px; font-weight: 700;'
            )
            b.addWidget(sender_lbl)
            msg_lbl = QtWidgets.QLabel(msg.get('content', ''))
            msg_lbl.setWordWrap(True)
            msg_lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            msg_lbl.setStyleSheet('color: #CBD5E1; font-size: 12px;')
            b.addWidget(msg_lbl)
            if is_admin:
                bubble.setStyleSheet(
                    'background: rgba(6,182,212,0.12); border: 1px solid rgba(6,182,212,0.25); border-radius: 10px;'
                )
                h.addWidget(bubble)
                h.addStretch()
            else:
                bubble.setStyleSheet(
                    'background: rgba(34,197,94,0.12); border: 1px solid rgba(34,197,94,0.25); border-radius: 10px;'
                )
                h.addStretch()
                h.addWidget(bubble)
            return container

        def _clear_bubbles() -> None:
            while bubble_vbox.count() > 1:
                item = bubble_vbox.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()

        def _scroll_bottom() -> None:
            QtCore.QTimer.singleShot(50, lambda: bubble_scroll.verticalScrollBar().setValue(
                bubble_scroll.verticalScrollBar().maximum()
            ))

        def _load() -> None:
            t = _t[0]
            if not t.get('chatStarted'):
                start_chat_btn.setVisible(True)
                chat_input.setEnabled(False)
                send_btn.setEnabled(False)
                return
            start_chat_btn.setVisible(False)
            chat_input.setEnabled(True)
            send_btn.setEnabled(True)
            msgs = get_messages(t['id'])
            if len(msgs) != _msg_count[0]:
                _clear_bubbles()
                for m in msgs:
                    bubble_vbox.insertWidget(bubble_vbox.count() - 1, _make_bubble(m))
                _msg_count[0] = len(msgs)
                _scroll_bottom()

        def _on_start() -> None:
            ok = api_start_chat(_t[0]['id'])
            if ok:
                _t[0] = {**_t[0], 'chatStarted': True}
                _load()

        def _on_send() -> None:
            content = chat_input.toPlainText().strip()
            if not content or not _t[0].get('chatStarted'):
                return
            send_btn.setEnabled(False)
            try:
                msg = send_message(_t[0]['id'], content)
                if msg:
                    bubble_vbox.insertWidget(bubble_vbox.count() - 1, _make_bubble(msg))
                    _msg_count[0] += 1
                    chat_input.clear()
                    _scroll_bottom()
            except Exception as exc:
                logger.warning('send_message failed: %s', exc)
            finally:
                send_btn.setEnabled(True)

        class _EF(QtCore.QObject):
            def eventFilter(self, obj, event):  # type: ignore[override]
                if obj is chat_input and event.type() == QtCore.QEvent.KeyPress:
                    if event.key() == QtCore.Qt.Key_Return and not (event.modifiers() & QtCore.Qt.ShiftModifier):
                        _on_send()
                        return True
                return super().eventFilter(obj, event)

        _ef = _EF(chat_input)
        chat_input.installEventFilter(_ef)
        start_chat_btn.clicked.connect(_on_start)
        send_btn.clicked.connect(_on_send)

        poll_timer = QtCore.QTimer(dlg)
        poll_timer.setInterval(5000)
        poll_timer.timeout.connect(_load)
        poll_timer.start()

        def _on_close() -> None:
            poll_timer.stop()
            if _t[0].get('chatStarted'):
                self.notify_chat_viewed(_t[0]['id'], _msg_count[0])
            if refresh_callback:
                refresh_callback()

        dlg.finished.connect(lambda _: _on_close())
        _load()
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
                    'PIN belum pernah diset untuk perangkat ini.\n'
                    'Perangkat sedang OFFLINE — tidak bisa membuat tiket.\n\n'
                    'Hubungi IT Admin / IT Staff secara langsung\n'
                    'untuk meminta PIN diset via Portal Bosowa.\n\n'
                    'Catatan: setelah PIN diset dan perangkat online 1x,\n'
                    'PIN tersimpan lokal dan bisa dipakai tanpa internet.',
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
