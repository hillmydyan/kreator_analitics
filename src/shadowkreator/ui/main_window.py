"""
PySide6 Desktop Interface styled after the ShadowKreator modern dashboard mockup:
- Deep Navy-Indigo Sidebar (#0D1127) with glowing purple 'S' logo, 4 pill-highlighted menus,
  and bottom version card (v1.0.0 • Android 12+ ADB).
- Soft Lavender-Gray Canvas (#F4F6FB) with Top Page Header (Icon Badge + Title + Subtitle),
  live Connected Device Mint Pill, and User Profile Pill (HD • Hilmy Dyan).
- Dismissible Mint Alert Banner, Pill Action Buttons, Rounded White Cards (16px radius),
  Live Device Search & Refresh footer, Unified Scraping Workspace, and Tabbed Analytics Hub.
"""

from __future__ import annotations

import os
import re
import time
import webbrowser
from datetime import date, datetime
from typing import List, Optional, Sequence

from PySide6.QtCore import QObject, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from shadowkreator.config import APP_NAME, APP_VERSION, DEFAULT_DB_PATH, DEFAULT_EXPORT_DIR
from shadowkreator.core.analytics import AnalyticsEngine, CreatorAnalyticsReport, ProductPerformance
from shadowkreator.core.controller import ScrapingController, ScrapingProgressSnapshot
from shadowkreator.core.database import DatabaseManager, utc_now_iso
from shadowkreator.core.device_manager import DeviceManager
from shadowkreator.core.exporter import ExportManager, build_export_filename
from shadowkreator.core.models import (
    ConnectionType,
    DeviceInfo,
    DeviceStatus,
    JobStatus,
    ScrapingJobRecord,
)
from shadowkreator.core.qr_pairing import encode_qr_matrix, generate_adb_wifi_qr_credentials
from shadowkreator.core.ui_automation import AdbUiDriver, MockUiDriver, UiAutomationDriver

_VIDEO_NUM_RE = re.compile(r"Video\s*#(\d+)", re.IGNORECASE)


def _fmt_int(value: Optional[int]) -> str:
    if value is None:
        return "Tidak Tersedia"
    return f"{value:,}".replace(",", ".")


def _now_human_str() -> str:
    return datetime.now().strftime("%d %b %Y, %H:%M")


def _style_status_item(item: QTableWidgetItem, status_text: str) -> None:
    """Format status cell like a soft pill badge ('Connected', etc.)."""
    lowered = (status_text or "").strip().lower()
    font = item.font()
    font.setBold(True)
    item.setFont(font)
    item.setTextAlignment(Qt.AlignCenter)
    if lowered in ("connected", "completed", "success"):
        item.setText(status_text)
        item.setForeground(QBrush(QColor("#0D9F6E")))
        item.setBackground(QBrush(QColor("#E6F8EF")))
    elif any(
        k in lowered
        for k in ("warning", "pairing", "unauthorized", "scanning", "extracting")
    ):
        item.setText(status_text)
        item.setForeground(QBrush(QColor("#B45309")))
        item.setBackground(QBrush(QColor("#FEF3C7")))
    elif lowered in (
        "failed",
        "error",
        "disconnected",
        "offline",
        "cancelled",
        "interrupted",
    ):
        item.setText(status_text)
        item.setForeground(QBrush(QColor("#DC2626")))
        item.setBackground(QBrush(QColor("#FEE2E2")))


class QrCodeWidget(QWidget):
    """Renders an ISO/IEC 18004 QR Code matrix cleanly using native QPainter."""

    def __init__(self, payload: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._matrix: List[List[bool]] = []
        self.setFixedSize(235, 235)
        if payload:
            self.set_payload(payload)

    def set_payload(self, payload: str) -> None:
        self._matrix = encode_qr_matrix(payload) if payload else []
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if not self._matrix:
            return
        n = len(self._matrix)
        quiet_zone = 4
        total_modules = n + 2 * quiet_zone
        mod_px = max(2, min(self.width(), self.height()) // total_modules)
        offset_x = (self.width() - total_modules * mod_px) // 2
        offset_y = (self.height() - total_modules * mod_px) // 2

        black = QColor("#000000")
        for r in range(n):
            for c in range(n):
                if self._matrix[r][c]:
                    painter.fillRect(
                        offset_x + (c + quiet_zone) * mod_px,
                        offset_y + (r + quiet_zone) * mod_px,
                        mod_px,
                        mod_px,
                        black,
                    )


class QrPairingWorker(QThread):
    """Background worker thread polling mDNS `_adb-tls-pairing._tcp` without blocking the GUI."""

    status_updated = Signal(str)
    pairing_succeeded = Signal(object)

    def __init__(
        self,
        device_manager: DeviceManager,
        password: str,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.device_manager = device_manager
        self.password = password
        self._stop_flag = False
        self._attempted_ports: set[str] = set()

    def set_credentials(self, password: str) -> None:
        self.password = password
        self._attempted_ports.clear()

    def stop(self) -> None:
        self._stop_flag = True

    def run(self) -> None:
        while not self._stop_flag:
            pairing_svcs = self.device_manager.discover_pairing_services()
            for svc in pairing_svcs:
                if self._stop_flag:
                    return
                key = f"{svc.host_port}:{self.password}"
                if key in self._attempted_ports:
                    continue
                self._attempted_ports.add(key)
                self.status_updated.emit(
                    f"HP terdeteksi di {svc.host_port}! Menjalankan pairing otomatis..."
                )
                ok, msg = self.device_manager.pair_wireless_device(
                    svc.host_port, self.password
                )
                if ok:
                    self.status_updated.emit(
                        f"Pairing Berhasil ({svc.host_port})! Menghubungkan ke perangkat..."
                    )
                    devices, _ = self.device_manager.scan_wireless_devices(
                        auto_connect=True
                    )
                    connected = [
                        d
                        for d in devices
                        if d.connection_status == DeviceStatus.CONNECTED.value
                    ]
                    self.pairing_succeeded.emit(connected[0] if connected else None)
                    return
                else:
                    self.status_updated.emit(
                        f"Menunggu scan QR Code dari HP ({svc.host_port})..."
                    )
            time.sleep(0.25)


class QrPairingDialog(QDialog):
    """
    Modal dialog displaying an Android 12+ Wireless Debugging QR Code
    (`WIFI:T:ADB;S:<service_name>;P:<password>;;`) and polling mDNS in a background thread.
    """

    def __init__(
        self,
        device_manager: DeviceManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.device_manager = device_manager
        self.paired_device: Optional[DeviceInfo] = None
        self.service_name, self.password, self.payload = (
            generate_adb_wifi_qr_credentials()
        )

        self.setWindowTitle("Pair Wireless Debugging via QR Code (Android 12+)")
        self.setFixedSize(500, 520)
        self.setStyleSheet(
            "QDialog { background-color: #F4F6FB; }"
            "QFrame#QrBox { background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 16px; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        lbl_title = QLabel("Scan QR Code dari HP Android")
        lbl_title.setAlignment(Qt.AlignCenter)
        lbl_title.setStyleSheet("font-size: 18px; font-weight: 800; color: #0F172A;")
        root.addWidget(lbl_title)

        lbl_steps = QLabel(
            "1. Di HP Android, buka <b>Developer Options - Wireless Debugging</b>.<br>"
            "2. Ketuk <b>\"Pair device with QR code\"</b> (Pasangkan perangkat dengan kode QR).<br>"
            "3. Arahkan kamera HP ke QR Code di bawah — aplikasi akan <b>otomatis Pairing & Connect</b>."
        )
        lbl_steps.setWordWrap(True)
        lbl_steps.setStyleSheet(
            "color: #334155; font-size: 12px; background-color: #EEF2FF; "
            "border: 1px solid #C7D2FE; border-radius: 10px; padding: 10px 12px;"
        )
        root.addWidget(lbl_steps)

        qr_row = QHBoxLayout()
        qr_frame = QFrame()
        qr_frame.setObjectName("QrBox")
        qf_layout = QVBoxLayout(qr_frame)
        qf_layout.setContentsMargins(14, 14, 14, 14)
        self.qr_widget = QrCodeWidget(self.payload)
        qf_layout.addWidget(self.qr_widget, 0, Qt.AlignCenter)
        qr_row.addStretch()
        qr_row.addWidget(qr_frame)
        qr_row.addStretch()
        root.addLayout(qr_row)

        self.lbl_status = QLabel(
            "Menunggu HP men-scan QR Code di jaringan Wi-Fi yang sama..."
        )
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(
            "background-color: #E6F8EF; color: #065F46; border: 1px solid #A7F3D0; "
            "border-radius: 12px; padding: 8px 12px; font-size: 12px; font-weight: 700;"
        )
        root.addWidget(self.lbl_status)

        btn_row = QHBoxLayout()
        btn_regen = QPushButton("Buat Ulang QR")
        btn_regen.setProperty("class", "SecondaryBtn")
        btn_regen.clicked.connect(self._regenerate_qr)

        btn_auto_conn = QPushButton("Connect HP yang Sudah Pernah Pair")
        btn_auto_conn.setProperty("class", "IndigoBtn")
        btn_auto_conn.clicked.connect(self._connect_already_paired)

        btn_close = QPushButton("Tutup")
        btn_close.setProperty("class", "SecondaryBtn")
        btn_close.clicked.connect(self.reject)

        btn_row.addWidget(btn_regen)
        btn_row.addWidget(btn_auto_conn)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

        self._poll_timer = QTimer(self)
        self._worker: Optional[QrPairingWorker] = None
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            self._worker = QrPairingWorker(self.device_manager, self.password, self)
            self._worker.status_updated.connect(self.lbl_status.setText)
            self._worker.pairing_succeeded.connect(self._on_worker_paired)
            self._worker.start()

    def _regenerate_qr(self) -> None:
        self.service_name, self.password, self.payload = (
            generate_adb_wifi_qr_credentials()
        )
        self.qr_widget.set_payload(self.payload)
        if self._worker is not None:
            self._worker.set_credentials(self.password)
        self.lbl_status.setText(
            "QR Code baru dibuat. Silakan scan dari menu Wireless Debugging HP..."
        )

    def _connect_already_paired(self) -> None:
        self.lbl_status.setText("Mencari & menghubungkan HP di Wi-Fi...")
        QApplication.processEvents()
        devices, _ = self.device_manager.scan_wireless_devices(auto_connect=True)
        connected = [
            d for d in devices if d.connection_status == DeviceStatus.CONNECTED.value
        ]
        if connected:
            self.paired_device = connected[0]
            self._stop_worker()
            self.accept()
        else:
            self.lbl_status.setText(
                "Belum ada HP yang langsung terhubung. Silakan scan QR Code di atas dari HP."
            )

    def _on_worker_paired(self, dev: Optional[DeviceInfo]) -> None:
        self.paired_device = dev
        self._stop_worker()
        self.accept()

    def _stop_worker(self) -> None:
        self._poll_timer.stop()
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(1500)
            self._worker = None

    def reject(self) -> None:
        self._stop_worker()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_worker()
        super().closeEvent(event)


class ProductBarChartWidget(QWidget):
    """Compact horizontal bar chart visualizing Top 5 products by total views."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: List[ProductPerformance] = []
        self.setFixedHeight(112)

    def set_products(self, products: List[ProductPerformance]) -> None:
        self._items = products[:5]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(10, 4, -10, -4)

        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if not self._items:
            painter.setPen(QColor("#64748B"))
            painter.drawText(
                rect,
                Qt.AlignCenter,
                "Belum ada data performa produk. Jalankan scraping terlebih dahulu.",
            )
            return

        max_views = max((p.total_views for p in self._items), default=1) or 1
        row_h = max(20, rect.height() // max(len(self._items), 1))
        label_w = min(250, max(165, int(rect.width() * 0.32)))
        metric_w = 150
        bar_max_w = max(80, rect.width() - label_w - metric_w - 14)

        font_label = QFont()
        font_label.setPointSize(9)
        font_label.setBold(True)

        font_metric = QFont()
        font_metric.setPointSize(9)

        for idx, item in enumerate(self._items):
            y = rect.top() + idx * row_h
            rank_prefix = f"#{idx + 1} "
            name = item.product_name
            if len(name) > 28:
                name = name[:26] + "…"

            painter.setFont(font_label)
            painter.setPen(QColor("#0F172A" if idx == 0 else "#334155"))
            painter.drawText(
                rect.left(),
                y,
                label_w - 8,
                row_h,
                Qt.AlignVCenter | Qt.AlignLeft,
                rank_prefix + name,
            )

            ratio = min(1.0, max(0.0, item.total_views / max_views))
            bar_w = max(6, int(bar_max_w * ratio))
            bar_x = rect.left() + label_w
            bar_h = min(11, max(8, row_h - 8))
            bar_y = y + (row_h - bar_h) // 2

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#EEF2FF"))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_max_w, bar_h), 4.0, 4.0)

            bar_color = QColor("#4F46E5") if idx == 0 else QColor("#3B82F6")
            painter.setBrush(bar_color)
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 4.0, 4.0)

            painter.setFont(font_metric)
            painter.setPen(QColor("#0F172A"))
            metric_str = f"{_fmt_int(item.total_views)} views ({item.view_contribution_pct:.1f}%)"
            painter.drawText(
                bar_x + bar_max_w + 10,
                y,
                metric_w,
                row_h,
                Qt.AlignVCenter | Qt.AlignLeft,
                metric_str,
            )


class ScrapingWorker(QObject):
    """Background worker executing ScrapingController off the main GUI thread (PRD Section 8.1)."""

    progress_changed = Signal(object)  # ScrapingProgressSnapshot
    job_finished = Signal(object)      # ScrapingJobRecord
    job_error = Signal(str)

    def __init__(
        self,
        controller: ScrapingController,
        creator_username: str,
        period_days: int,
        device: DeviceInfo,
        driver: UiAutomationDriver,
        compliance_accepted: bool,
        resume_job_id: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.creator_username = creator_username
        self.period_days = period_days
        self.device = device
        self.driver = driver
        self.compliance_accepted = compliance_accepted
        self.resume_job_id = resume_job_id

    def run(self) -> None:
        try:
            job = self.controller.run_scraping_job(
                creator_username=self.creator_username,
                period_days=self.period_days,
                device=self.device,
                ui_driver=self.driver,
                compliance_accepted=self.compliance_accepted,
                on_progress=self.progress_changed.emit,
                resume_job_id=self.resume_job_id,
            )
            self.job_finished.emit(job)
        except Exception as exc:
            self.job_error.emit(str(exc))


class MainWindow(QMainWindow):
    """
    Main Desktop Window matching the ShadowKreator visual mockup:
    0 -> Beranda (Dashboard)
    1 -> Perangkat Android (Device Manager)
    2 -> Scraping Kreator (Unified Input Form + Live Monitor)
    3 -> Analitik & Laporan (Tabs: Performa Produk / Daftar Video / Riwayat & Export)
    """

    PAGE_DASHBOARD = 0
    PAGE_DEVICE = 1
    PAGE_NEW_SCRAPING = 2
    PAGE_MONITOR = 2
    PAGE_ANALYTICS = 3

    PAGE_PRODUCT_DETAIL = 30
    PAGE_VIDEO_DB = 31
    PAGE_HISTORY = 32

    TAB_PRODUCTS = 0
    TAB_VIDEOS = 1
    TAB_HISTORY = 2

    _HEADER_META = (
        ("", "Beranda", "Ringkasan Ekstraksi, Status Perangkat, & Top Produk"),
        ("", "Perangkat Android", "Koneksi USB & Wireless Debugging"),
        ("", "Scraping Kreator", "Konfigurasi Ekstraksi & Pemantauan Real-Time"),
        ("", "Analitik & Laporan", "Performa Produk Keranjang Kuning, Video, & Export Excel"),
    )

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        super().__init__()
        self.db = db or DatabaseManager(DEFAULT_DB_PATH)
        self.device_manager = DeviceManager(db=self.db)
        self.controller = ScrapingController(self.db)
        self.analytics = AnalyticsEngine(self.db)
        self.exporter = ExportManager(self.db)

        self.active_device: Optional[DeviceInfo] = None
        self.use_simulator_driver: bool = False
        self.current_analytics_job_id: Optional[int] = None
        self.current_report: Optional[CreatorAnalyticsReport] = None

        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[ScrapingWorker] = None
        self._job_start_epoch: Optional[float] = None
        self._discovered_during_scan: int = 0

        self._duration_timer = QTimer(self)
        self._duration_timer.setInterval(1000)
        self._duration_timer.timeout.connect(self._update_monitor_duration)

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(980, 720)
        self._apply_stylesheet()
        self._build_layout()
        self.refresh_all_views()

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#RightCanvas {
                background-color: #F4F6FB;
                color: #0F172A;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                font-size: 13px;
            }
            /* Deep Navy-Indigo Sidebar */
            QFrame#SidebarContainer {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #0E132C,
                    stop:0.5 #121836,
                    stop:1 #0B0F23
                );
                border-right: 1px solid #1E293B;
            }
            QLabel#BrandLogoBadge {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6366F1,
                    stop:1 #8B5CF6
                );
                color: #FFFFFF;
                font-size: 20px;
                font-weight: 900;
                border-radius: 11px;
            }
            QListWidget#SidebarNav {
                background: transparent;
                color: #94A3B8;
                border: none;
                padding: 4px;
                font-size: 13px;
                font-weight: 600;
                outline: none;
            }
            QListWidget#SidebarNav::item {
                padding: 12px 14px;
                border-radius: 11px;
                margin-bottom: 6px;
            }
            QListWidget#SidebarNav::item:hover {
                background-color: rgba(255, 255, 255, 0.06);
                color: #E2E8F0;
            }
            QListWidget#SidebarNav::item:selected {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #5B50FF,
                    stop:1 #7C65FF
                );
                color: #FFFFFF;
                font-weight: 700;
            }
            QFrame#SidebarFooterCard {
                background-color: #171D3B;
                border: 1px solid #283059;
                border-radius: 12px;
            }
            /* White Cards on Lavender-Gray Canvas */
            QFrame[class="WhiteCard"], QGroupBox {
                background-color: #FFFFFF;
                border: 1px solid #E8ECF4;
                border-radius: 16px;
            }
            QGroupBox {
                margin-top: 8px;
                padding: 16px 16px 14px 16px;
                font-weight: 700;
                color: #0F172A;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 16px;
                padding: 0 6px;
                color: #0F172A;
            }
            /* Pill & Rounded Buttons matching mockup */
            QPushButton {
                background-color: #1D63FF;
                color: #FFFFFF;
                border: none;
                border-radius: 15px;
                padding: 8px 18px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #1550D6;
            }
            QPushButton:disabled {
                background-color: #CBD5E1;
                color: #64748B;
            }
            QPushButton[class="IndigoBtn"] {
                background-color: #4F46E5;
                color: #FFFFFF;
                border-radius: 15px;
                padding: 8px 18px;
            }
            QPushButton[class="IndigoBtn"]:hover {
                background-color: #4338CA;
            }
            QPushButton[class="SecondaryBtn"] {
                background-color: #FFFFFF;
                color: #1E293B;
                border: 1px solid #D8DEE9;
                border-radius: 15px;
            }
            QPushButton[class="SecondaryBtn"]:hover {
                background-color: #F1F5F9;
            }
            QPushButton[class="SuccessBtn"] {
                background-color: #0D9F6E;
                color: #FFFFFF;
                border-radius: 15px;
            }
            QPushButton[class="SuccessBtn"]:hover {
                background-color: #0B875D;
            }
            QPushButton[class="DangerBtn"] {
                background-color: #EF4444;
                color: #FFFFFF;
                border-radius: 15px;
            }
            QPushButton[class="DangerBtn"]:hover {
                background-color: #DC2626;
            }
            QPushButton[class="IconCircleBtn"] {
                background-color: #F8FAFC;
                color: #334155;
                border: 1px solid #E2E8F0;
                border-radius: 15px;
                padding: 5px 10px;
                font-size: 13px;
            }
            QPushButton[class="IconCircleBtn"]:hover {
                background-color: #E2E8F0;
            }
            /* Inputs */
            QLineEdit, QComboBox, QPlainTextEdit {
                background-color: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
                padding: 7px 12px;
                color: #0F172A;
            }
            QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
                background-color: #FFFFFF;
                border: 1px solid #6366F1;
            }
            /* Tabs */
            QTabWidget::pane {
                border: 1px solid #E8ECF4;
                border-radius: 14px;
                background-color: #FFFFFF;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #E8ECF4;
                color: #475569;
                font-weight: 700;
                padding: 9px 18px;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                margin-right: 6px;
            }
            QTabBar::tab:selected {
                background-color: #4F46E5;
                color: #FFFFFF;
            }
            /* ProgressBar */
            QProgressBar {
                background-color: #E2E8F0;
                border: none;
                border-radius: 8px;
                height: 20px;
                text-align: center;
                font-weight: bold;
                color: #0F172A;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4F46E5,
                    stop:1 #0D9F6E
                );
                border-radius: 8px;
            }
            /* Clean Table matching mockup */
            QTableWidget {
                background-color: #FFFFFF;
                alternate-background-color: #FAFBFD;
                border: none;
                gridline-color: #F1F5F9;
                selection-background-color: #EEF2FF;
                selection-color: #1E1B4B;
            }
            QTableWidget::item {
                padding: 6px 10px;
                border-bottom: 1px solid #F1F5F9;
            }
            QHeaderView::section {
                background-color: #F8FAFC;
                color: #475569;
                font-weight: 700;
                font-size: 12px;
                padding: 9px 10px;
                border: none;
                border-bottom: 1px solid #E2E8F0;
            }
            """
        )

    @staticmethod
    def _configure_table(
        table: QTableWidget,
        stretch_columns: Sequence[int] = (0,),
    ) -> None:
        """Apply clean mockup table styling, full-row selection, and proportional column widths."""
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(40)

        header = table.horizontalHeader()
        col_count = table.columnCount()
        for col_idx in range(col_count):
            if col_idx in stretch_columns:
                header.setSectionResizeMode(col_idx, QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeToContents)

    def _build_layout(self) -> None:
        central = QWidget(self)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ------------------------------------------------------------------
        # Left Navigation Sidebar (Exact match to mockup)
        # ------------------------------------------------------------------
        sidebar_frame = QFrame()
        sidebar_frame.setObjectName("SidebarContainer")
        sidebar_frame.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(sidebar_frame)
        sidebar_layout.setContentsMargins(16, 22, 16, 18)
        sidebar_layout.setSpacing(12)

        # Logo + Title Row
        brand_row = QHBoxLayout()
        brand_row.setSpacing(11)
        logo_badge = QLabel("S")
        logo_badge.setObjectName("BrandLogoBadge")
        logo_badge.setFixedSize(38, 38)
        logo_badge.setAlignment(Qt.AlignCenter)

        brand_text_col = QVBoxLayout()
        brand_text_col.setSpacing(1)
        brand_title = QLabel("ShadowKreator")
        brand_title.setStyleSheet(
            "color: #FFFFFF; font-size: 16px; font-weight: 800; background: transparent;"
        )
        brand_sub = QLabel("TikTok Product Analytics")
        brand_sub.setStyleSheet(
            "color: #94A3B8; font-size: 11px; font-weight: 500; background: transparent;"
        )
        brand_text_col.addWidget(brand_title)
        brand_text_col.addWidget(brand_sub)

        brand_row.addWidget(logo_badge)
        brand_row.addLayout(brand_text_col, 1)
        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addSpacing(12)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("SidebarNav")
        nav_items = [
            "Beranda",
            "Perangkat Android",
            "Scraping Kreator",
            "Analitik & Laporan",
        ]
        for label in nav_items:
            self.sidebar.addItem(QListWidgetItem(label))
        sidebar_layout.addWidget(self.sidebar, 1)

        # Bottom Version Card inside Sidebar (v1.0.0 • Android 12+ ADB)
        footer_card = QFrame()
        footer_card.setObjectName("SidebarFooterCard")
        footer_layout = QHBoxLayout(footer_card)
        footer_layout.setContentsMargins(14, 10, 14, 10)
        footer_text_col = QVBoxLayout()
        footer_text_col.setSpacing(2)
        lbl_ver = QLabel(f"v{APP_VERSION} • Android 12+")
        lbl_ver.setStyleSheet(
            "color: #10B981; font-size: 11px; font-weight: 700; background: transparent;"
        )
        lbl_adb_tag = QLabel("By IT Kurma Group")
        lbl_adb_tag.setStyleSheet(
            "color: #CBD5E1; font-size: 11px; font-weight: 600; background: transparent;"
        )
        footer_text_col.addWidget(lbl_ver)
        footer_text_col.addWidget(lbl_adb_tag)

        footer_layout.addLayout(footer_text_col, 1)
        sidebar_layout.addWidget(footer_card)

        # ------------------------------------------------------------------
        # Right Canvas: Top Header (Title/Subtitle + Device Pill + User Pill)
        # ------------------------------------------------------------------
        right_container = QWidget()
        right_container.setObjectName("RightCanvas")
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        top_bar = QWidget()
        top_bar.setStyleSheet("background: transparent;")
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(26, 18, 26, 8)
        top_bar_layout.setSpacing(14)

        self.lbl_topbar_icon = QLabel("")
        self.lbl_topbar_icon.setVisible(False)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(2)
        self.lbl_topbar_title = QLabel(self._HEADER_META[0][1])
        self.lbl_topbar_title.setStyleSheet(
            "font-size: 21px; font-weight: 800; color: #0F172A; background: transparent;"
        )
        self.lbl_topbar_subtitle = QLabel(self._HEADER_META[0][2])
        self.lbl_topbar_subtitle.setStyleSheet(
            "font-size: 12px; font-weight: 500; color: #64748B; background: transparent;"
        )
        title_stack.addWidget(self.lbl_topbar_title)
        title_stack.addWidget(self.lbl_topbar_subtitle)

        # Connected Device Mint Pill
        self.lbl_topbar_device_badge = QLabel("Belum Terhubung")
        self.lbl_topbar_device_badge.setStyleSheet(
            "background-color: #FEE2E2; color: #991B1B; border: 1px solid #FCA5A5; "
            "border-radius: 18px; padding: 7px 16px; font-size: 12px; font-weight: 700;"
        )

        top_bar_layout.addLayout(title_stack)
        top_bar_layout.addStretch()
        top_bar_layout.addWidget(self.lbl_topbar_device_badge)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._create_dashboard_page())        # index 0
        self.stack.addWidget(self._create_device_page())           # index 1
        self.stack.addWidget(self._create_unified_scraping_page()) # index 2
        self.stack.addWidget(self._create_analytics_hub_page())    # index 3

        right_layout.addWidget(top_bar)
        right_layout.addWidget(self.stack, 1)

        self.sidebar.currentRowChanged.connect(self._on_nav_changed)
        self.sidebar.setCurrentRow(self.PAGE_DASHBOARD)

        root_layout.addWidget(sidebar_frame)
        root_layout.addWidget(right_container, 1)
        self.setCentralWidget(central)

    def navigate_to(self, page_id: int) -> None:
        """Navigate to any top-level menu or sub-tab (Products, Detail, Videos, History)."""
        if page_id == self.PAGE_PRODUCT_DETAIL:
            self.sidebar.setCurrentRow(self.PAGE_ANALYTICS)
            self.analytics_tabs.setCurrentIndex(self.TAB_PRODUCTS)
            self.products_sub_stack.setCurrentIndex(1)
        elif page_id == self.PAGE_VIDEO_DB:
            self.sidebar.setCurrentRow(self.PAGE_ANALYTICS)
            self.analytics_tabs.setCurrentIndex(self.TAB_VIDEOS)
        elif page_id == self.PAGE_HISTORY:
            self.sidebar.setCurrentRow(self.PAGE_ANALYTICS)
            self.analytics_tabs.setCurrentIndex(self.TAB_HISTORY)
        elif page_id == self.PAGE_ANALYTICS:
            self.sidebar.setCurrentRow(self.PAGE_ANALYTICS)
            self.analytics_tabs.setCurrentIndex(self.TAB_PRODUCTS)
            self.products_sub_stack.setCurrentIndex(0)
        else:
            self.sidebar.setCurrentRow(page_id)

    def _on_nav_changed(self, idx: int) -> None:
        if idx < 0:
            return
        if idx >= self.stack.count():
            self.navigate_to(idx)
            return
        if 0 <= idx < len(self._HEADER_META):
            icon_char, title_str, sub_str = self._HEADER_META[idx]
            self.lbl_topbar_icon.setText(icon_char)
            self.lbl_topbar_title.setText(title_str)
            self.lbl_topbar_subtitle.setText(sub_str)
        if idx == self.PAGE_ANALYTICS:
            self.products_sub_stack.setCurrentIndex(0)
        self.stack.setCurrentIndex(idx)
        self.refresh_all_views()

    def _make_card(
        self, title: str, initial_value: str = "0", accent_color: str = "#4F46E5"
    ) -> tuple[QFrame, QLabel]:
        frame = QFrame()
        frame.setMinimumHeight(56)
        frame.setStyleSheet(
            f"QFrame {{ background-color: #FFFFFF; border: 1px solid #E8ECF4; "
            f"border-left: 4px solid {accent_color}; border-radius: 11px; padding: 4px 10px; }}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(2)
        lbl_title = QLabel(title)
        lbl_title.setWordWrap(True)
        lbl_title.setStyleSheet(
            "color: #64748B; font-size: 11px; font-weight: 700; border: none;"
        )
        lbl_val = QLabel(initial_value)
        lbl_val.setWordWrap(True)
        lbl_val.setStyleSheet(
            "color: #0F172A; font-size: 15px; font-weight: 800; border: none;"
        )
        layout.addWidget(lbl_title)
        layout.addWidget(lbl_val)
        return frame, lbl_val

    # ------------------------------------------------------------------
    # Menu 1: Beranda (Main Dashboard — PRD 4.1)
    # ------------------------------------------------------------------
    def _create_dashboard_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 10, 20, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        subtitle = QLabel(
            "Pantau ringkasan ekstraksi kreator, status perangkat Android, dan performa produk keranjang kuning."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #475569; font-size: 13px;")
        btn_quick_scrape = QPushButton("Mulai Scraping Kreator Baru")
        btn_quick_scrape.setProperty("class", "SuccessBtn")
        btn_quick_scrape.clicked.connect(
            lambda: self.navigate_to(self.PAGE_NEW_SCRAPING)
        )
        header.addWidget(subtitle, 1)
        header.addSpacing(12)
        header.addWidget(btn_quick_scrape)
        layout.addLayout(header)

        cards_grid = QGridLayout()
        cards_grid.setSpacing(12)
        c1, self.lbl_dash_device = self._make_card(
            "Status Perangkat Android", "Disconnected", "#0D9F6E"
        )
        c2, self.lbl_dash_jobs = self._make_card(
            "Jumlah Pekerjaan Scraping", "0", "#4F46E5"
        )
        c3, self.lbl_dash_creators = self._make_card(
            "Total Kreator Dianalisis", "0", "#7C3AED"
        )
        c4, self.lbl_dash_videos = self._make_card(
            "Total Video Terkumpul", "0", "#1D63FF"
        )
        c5, self.lbl_dash_products = self._make_card(
            "Total Produk Teridentifikasi", "0", "#D97706"
        )
        cards_grid.addWidget(c1, 0, 0)
        cards_grid.addWidget(c2, 0, 1)
        cards_grid.addWidget(c3, 0, 2)
        cards_grid.addWidget(c4, 0, 3)
        cards_grid.addWidget(c5, 0, 4)
        cards_grid.setColumnStretch(0, 2)
        for col in range(1, 5):
            cards_grid.setColumnStretch(col, 1)
        layout.addLayout(cards_grid)

        splitter = QSplitter(Qt.Vertical)

        recent_box = QGroupBox(
            "Riwayat Pekerjaan Terakhir (Klik dua kali untuk membuka Analitik)"
        )
        recent_layout = QVBoxLayout(recent_box)
        self.tbl_dash_recent = QTableWidget(0, 6)
        self.tbl_dash_recent.setHorizontalHeaderLabels(
            [
                "Job ID",
                "Creator Username",
                "Periode",
                "Total Video",
                "Total Produk",
                "Status",
            ]
        )
        self._configure_table(self.tbl_dash_recent, stretch_columns=(1, 5))
        self.tbl_dash_recent.cellDoubleClicked.connect(
            self._on_dash_recent_double_clicked
        )
        recent_layout.addWidget(self.tbl_dash_recent)
        splitter.addWidget(recent_box)

        perf_box = QGroupBox("Product Performance Overview (Pekerjaan Terakhir)")
        perf_layout = QVBoxLayout(perf_box)
        self.tbl_dash_top_products = QTableWidget(0, 4)
        self.tbl_dash_top_products.setHorizontalHeaderLabels(
            ["Product Name", "Videos", "Total Views", "Average Views"]
        )
        self._configure_table(self.tbl_dash_top_products, stretch_columns=(0,))
        perf_layout.addWidget(self.tbl_dash_top_products)
        splitter.addWidget(perf_box)

        layout.addWidget(splitter, 1)
        return page

    # ------------------------------------------------------------------
    # Menu 2: Perangkat Android (Clean typographic UI without icons/mock)
    # ------------------------------------------------------------------
    def _create_device_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 8, 26, 20)
        layout.setSpacing(14)

        # 1. Mint Green Dismissible Alert Banner
        self.dev_banner_frame = QFrame()
        self.dev_banner_frame.setStyleSheet(
            "QFrame { background-color: #E6F8EF; border: 1px solid #A7F3D0; border-radius: 14px; }"
        )
        banner_layout = QHBoxLayout(self.dev_banner_frame)
        banner_layout.setContentsMargins(16, 10, 14, 10)
        banner_layout.setSpacing(10)

        self.lbl_dev_banner_icon = QLabel("")
        self.lbl_dev_banner_icon.setVisible(False)

        self.lbl_dev_banner_badge = QLabel(
            "Perangkat aktif & siap scraping: Belum ada perangkat terhubung"
        )
        self.lbl_dev_banner_badge.setWordWrap(True)
        self.lbl_dev_banner_badge.setStyleSheet(
            "color: #065F46; font-size: 13px; font-weight: 700; border: none; background: transparent;"
        )

        btn_close_banner = QPushButton("x")
        btn_close_banner.setFixedSize(24, 24)
        btn_close_banner.setStyleSheet(
            "QPushButton { background: transparent; color: #065F46; font-size: 13px; font-weight: 700; border: none; }"
            "QPushButton:hover { color: #0F172A; }"
        )
        btn_close_banner.clicked.connect(lambda: self.dev_banner_frame.setVisible(False))

        banner_layout.addWidget(self.lbl_dev_banner_badge, 1)
        banner_layout.addWidget(btn_close_banner)
        layout.addWidget(self.dev_banner_frame)

        # 2. Row of Pill Action Buttons (No icons, no Mock button, no redundant Wireless Scan button)
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)
        btn_scan_usb = QPushButton("Scan USB (ADB)")
        btn_scan_usb.clicked.connect(self._on_scan_devices)

        btn_poc = QPushButton("Tes Koneksi TikTok")
        btn_poc.setProperty("class", "SuccessBtn")
        btn_poc.clicked.connect(self._on_run_poc_check)

        btn_disconnect = QPushButton("Disconnect")
        btn_disconnect.setProperty("class", "DangerBtn")
        btn_disconnect.clicked.connect(self._on_disconnect_device)

        top_bar.addWidget(btn_scan_usb)
        top_bar.addWidget(btn_poc)
        top_bar.addWidget(btn_disconnect)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # 3. White Card: Wireless Debugging Pairing via QR Code (Android 12+)
        wireless_card = QFrame()
        wireless_card.setProperty("class", "WhiteCard")
        w_card_layout = QVBoxLayout(wireless_card)
        w_card_layout.setContentsMargins(20, 14, 20, 14)
        w_card_layout.setSpacing(0)

        w_header_row = QHBoxLayout()
        w_title_col = QVBoxLayout()
        w_title_col.setSpacing(2)
        lbl_w_title = QLabel("Wireless Debugging Pairing (Android 12+)")
        lbl_w_title.setStyleSheet(
            "color: #0F172A; font-size: 15px; font-weight: 800; background: transparent;"
        )
        lbl_w_hint = QLabel("Hubungkan perangkat secara otomatis dengan scan QR Code dari menu Wireless Debugging HP")
        lbl_w_hint.setWordWrap(True)
        lbl_w_hint.setStyleSheet(
            "color: #64748B; font-size: 12px; font-weight: 500; background: transparent;"
        )
        w_title_col.addWidget(lbl_w_title)
        w_title_col.addWidget(lbl_w_hint)

        self.btn_qr_pair = QPushButton("Pair dengan Scan QR")
        self.btn_qr_pair.setProperty("class", "SuccessBtn")
        self.btn_qr_pair.clicked.connect(self._on_open_qr_pairing_dialog)

        w_header_row.addLayout(w_title_col, 1)
        w_header_row.addSpacing(12)
        w_header_row.addWidget(self.btn_qr_pair)
        w_card_layout.addLayout(w_header_row)

        # Keep backing QLineEdit attributes hidden for compatibility
        self.inp_pair_addr = QLineEdit()
        self.inp_pair_code = QLineEdit()
        self.inp_conn_addr = QLineEdit()
        layout.addWidget(wireless_card)

        # 4. White Card: Daftar Perangkat Terdeteksi + Search & Refresh Footer
        dev_card = QFrame()
        dev_card.setProperty("class", "WhiteCard")
        dev_layout = QVBoxLayout(dev_card)
        dev_layout.setContentsMargins(20, 16, 20, 14)
        dev_layout.setSpacing(12)

        dev_header_row = QHBoxLayout()
        lbl_d_title = QLabel("Daftar Perangkat Terdeteksi")
        lbl_d_title.setStyleSheet("color: #0F172A; font-size: 15px; font-weight: 800;")
        lbl_d_sub = QLabel("Klik baris untuk memilih perangkat aktif")
        lbl_d_sub.setStyleSheet("color: #64748B; font-size: 12px; margin-left: 6px;")

        self.inp_device_search = QLineEdit()
        self.inp_device_search.setPlaceholderText("Cari perangkat...")
        self.inp_device_search.setFixedWidth(220)
        self.inp_device_search.setStyleSheet(
            "QLineEdit { background-color: #F8FAFC; border: 1px solid #E2E8F0; "
            "border-radius: 15px; padding: 6px 14px; font-size: 12px; }"
        )
        self.inp_device_search.textChanged.connect(self._refresh_device_table)

        dev_header_row.addWidget(lbl_d_title)
        dev_header_row.addWidget(lbl_d_sub)
        dev_header_row.addStretch()
        dev_header_row.addWidget(self.inp_device_search)
        dev_layout.addLayout(dev_header_row)

        self.tbl_devices = QTableWidget(0, 6)
        self.tbl_devices.setHorizontalHeaderLabels(
            [
                "Device Name",
                "Model",
                "Android Version",
                "Connection Type",
                "Device ID (Serial)",
                "Status",
            ]
        )
        self._configure_table(self.tbl_devices, stretch_columns=(0, 4))
        self.tbl_devices.itemSelectionChanged.connect(self._on_select_device_row)
        self.tbl_devices.cellClicked.connect(
            lambda _r, _c: self._on_select_device_row()
        )
        dev_layout.addWidget(self.tbl_devices, 1)

        # Bottom Status & Refresh Bar inside Device Card
        footer_bar = QFrame()
        footer_bar.setStyleSheet(
            "QFrame { background-color: #F8FAFC; border: 1px solid #EDF2F7; border-radius: 12px; }"
        )
        fb_layout = QHBoxLayout(footer_bar)
        fb_layout.setContentsMargins(14, 8, 12, 8)

        self.lbl_device_poc_status = QLabel("Status Koneksi TikTok: Belum diuji.")
        self.lbl_device_poc_status.setWordWrap(True)
        self.lbl_device_poc_status.setStyleSheet(
            "color: #1E293B; font-size: 12px; font-weight: 700; border: none; background: transparent;"
        )

        self.lbl_device_last_updated = QLabel(
            f"Terakhir diperbarui: {_now_human_str()}"
        )
        self.lbl_device_last_updated.setStyleSheet(
            "color: #64748B; font-size: 12px; border: none; background: transparent;"
        )

        btn_footer_refresh = QPushButton("Refresh")
        btn_footer_refresh.setProperty("class", "SecondaryBtn")
        btn_footer_refresh.clicked.connect(self._on_refresh_devices)

        fb_layout.addWidget(self.lbl_device_poc_status, 1)
        fb_layout.addWidget(self.lbl_device_last_updated)
        fb_layout.addWidget(btn_footer_refresh)
        dev_layout.addWidget(footer_bar)

        layout.addWidget(dev_card, 1)
        return page

    # ------------------------------------------------------------------
    # Menu 3: Scraping Kreator (Unified Input Form + Live Monitor — PRD 4.3 & 4.4)
    # ------------------------------------------------------------------
    def _create_unified_scraping_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 8, 18, 16)
        layout.setSpacing(10)

        form_box = QGroupBox("1. Konfigurasi && Mulai Scraping Kreator")
        form_grid = QGridLayout(form_box)
        form_grid.setSpacing(8)

        self.inp_creator_username = QLineEdit("@kakmuti7")
        self.inp_creator_username.setPlaceholderText("Contoh: @kakmuti7")
        self.inp_creator_username.textChanged.connect(self._update_preflight_summary)

        self.cmb_period = QComboBox()
        self.cmb_period.addItem("7 Days (7 Hari Terakhir)", 7)
        self.cmb_period.addItem("30 Days (30 Hari Terakhir)", 30)
        self.cmb_period.currentIndexChanged.connect(self._update_preflight_summary)

        self.chk_compliance = QCheckBox(
            "Perangkat Android && sesi akun telah diotorisasi"
        )
        self.chk_compliance.setChecked(True)

        self.chk_open_yellow_basket = QCheckBox(
            "Buka Keranjang Kuning && Ambil Link Produk"
        )
        self.chk_open_yellow_basket.setChecked(False)
        self.chk_open_yellow_basket.setToolTip(
            "Tidak dicentang (Mode Cepat 50+ Video): Hanya membaca nama produk langsung dari label Keranjang Kuning di layar video (hemat RAM & tidak mental ke Beranda).\n"
            "Dicentang (Mode Ambil Link): Membuka Keranjang Kuning & menyalin link produk Tokopedia/TikTok Shop."
        )
        self.chk_open_yellow_basket.setStyleSheet(
            "QCheckBox { font-weight: 700; color: #1E293B; }"
        )

        self.lbl_pre_device = QLabel("-")
        self.lbl_pre_device.setStyleSheet("font-weight: 700; color: #0F172A;")
        self.lbl_pre_status = QLabel("-")
        self.lbl_pre_status.setStyleSheet("font-weight: 700; color: #0D9F6E;")
        self.lbl_pre_username = QLabel("@kakmuti7")
        self.lbl_pre_username.setStyleSheet("font-weight: 700; color: #4F46E5;")
        self.lbl_pre_period = QLabel("7 Hari Terakhir")
        self.lbl_pre_period.setStyleSheet("font-weight: 700;")

        form_grid.addWidget(QLabel("Username Kreator:"), 0, 0)
        form_grid.addWidget(self.inp_creator_username, 0, 1)
        form_grid.addWidget(QLabel("Periode Analisis:"), 0, 2)
        form_grid.addWidget(self.cmb_period, 0, 3)

        pre_row = QHBoxLayout()
        pre_row.addWidget(QLabel("Perangkat:"))
        pre_row.addWidget(self.lbl_pre_device)
        pre_row.addSpacing(12)
        pre_row.addWidget(QLabel("Status:"))
        pre_row.addWidget(self.lbl_pre_status)
        pre_row.addSpacing(12)
        pre_row.addWidget(QLabel("Target:"))
        pre_row.addWidget(self.lbl_pre_username)
        pre_row.addWidget(self.lbl_pre_period)
        pre_row.addStretch()
        form_grid.addLayout(pre_row, 1, 0, 1, 4)

        chk_row = QHBoxLayout()
        chk_row.addWidget(self.chk_compliance)
        chk_row.addStretch()
        chk_row.addWidget(self.chk_open_yellow_basket)
        form_grid.addLayout(chk_row, 2, 0, 1, 4)

        action_row = QHBoxLayout()
        self.btn_start_scraping = QPushButton("Mulai Scraping")
        self.btn_start_scraping.setProperty("class", "SuccessBtn")
        self.btn_start_scraping.clicked.connect(self._on_start_scraping_clicked)

        btn_reset = QPushButton("Reset")
        btn_reset.setProperty("class", "SecondaryBtn")
        btn_reset.clicked.connect(self._on_reset_scraping_form)

        self.btn_cancel_job = QPushButton("Stop && Selesaikan")
        self.btn_cancel_job.setProperty("class", "DangerBtn")
        self.btn_cancel_job.setEnabled(False)
        self.btn_cancel_job.clicked.connect(self._on_cancel_running_job)

        self.btn_mon_view_analytics = QPushButton("Lihat Hasil Analitik")
        self.btn_mon_view_analytics.setProperty("class", "IndigoBtn")
        self.btn_mon_view_analytics.setEnabled(False)
        self.btn_mon_view_analytics.clicked.connect(
            lambda: self.navigate_to(self.PAGE_ANALYTICS)
        )

        self.btn_mon_export_excel = QPushButton("Export Excel (.xlsx)")
        self.btn_mon_export_excel.setProperty("class", "SecondaryBtn")
        self.btn_mon_export_excel.setEnabled(False)
        self.btn_mon_export_excel.clicked.connect(self._on_export_current_analytics)

        action_row.addWidget(self.btn_start_scraping)
        action_row.addWidget(btn_reset)
        action_row.addWidget(self.btn_cancel_job)
        action_row.addStretch()
        action_row.addWidget(self.btn_mon_view_analytics)
        action_row.addWidget(self.btn_mon_export_excel)
        form_grid.addLayout(action_row, 3, 0, 1, 4)
        layout.addWidget(form_box)

        mon_box = QGroupBox("2. Live Monitor && Progress Pekerjaan")
        mon_layout = QVBoxLayout(mon_box)
        mon_layout.setSpacing(8)

        self.lbl_mon_phase_banner = QLabel(
            "Status Monitor: Siap memulai pekerjaan baru."
        )
        self.lbl_mon_phase_banner.setStyleSheet(
            "color: #3730A3; background-color: #EEF2FF; border: 1px solid #C7D2FE; "
            "border-radius: 10px; padding: 7px 14px; font-weight: 700;"
        )
        mon_layout.addWidget(self.lbl_mon_phase_banner)

        grid = QGridLayout()
        grid.setSpacing(10)
        c_user, self.lbl_mon_user = self._make_card("Username Kreator", "-", "#4F46E5")
        c_per, self.lbl_mon_period = self._make_card("Periode Analisis", "-", "#7C3AED")
        c_stat, self.lbl_mon_status = self._make_card("Status Pekerjaan", "Idle", "#0D9F6E")
        c_dur, self.lbl_mon_duration = self._make_card("Durasi Pekerjaan", "00:00", "#1D63FF")

        c_tot, self.lbl_mon_total_v = self._make_card("Total Video Ditemukan", "0", "#4F46E5")
        c_ok, self.lbl_mon_proc_v = self._make_card("Video Berhasil Diproses", "0", "#0D9F6E")
        c_fail, self.lbl_mon_fail_v = self._make_card("Video Gagal Diproses", "0", "#EF4444")
        c_prod, self.lbl_mon_prod = self._make_card("Produk Ditemukan", "0", "#D97706")

        grid.addWidget(c_user, 0, 0)
        grid.addWidget(c_per, 0, 1)
        grid.addWidget(c_stat, 0, 2)
        grid.addWidget(c_dur, 0, 3)
        grid.addWidget(c_tot, 1, 0)
        grid.addWidget(c_ok, 1, 1)
        grid.addWidget(c_fail, 1, 2)
        grid.addWidget(c_prod, 1, 3)
        mon_layout.addLayout(grid)

        self.prog_bar = QProgressBar()
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(0)
        mon_layout.addWidget(self.prog_bar)

        self.txt_monitor_logs = QPlainTextEdit()
        self.txt_monitor_logs.setReadOnly(True)
        self.txt_monitor_logs.setStyleSheet(
            "font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 12px; "
            "background-color: #0E132C; color: #E2E8F0; border-radius: 10px; padding: 10px;"
        )
        mon_layout.addWidget(self.txt_monitor_logs, 1)
        layout.addWidget(mon_box, 1)

        return page

    # ------------------------------------------------------------------
    # Menu 4: Analitik & Laporan (Tabbed Hub for PRD 4.5, 4.6, 4.7, 4.8 & 4.9)
    # ------------------------------------------------------------------
    def _create_analytics_hub_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 6, 18, 16)
        layout.setSpacing(8)

        # Keep backing combo box hidden; automatically show the latest analytics job
        self.cmb_analytics_job = QComboBox()
        self.cmb_analytics_job.currentIndexChanged.connect(self._on_analytics_job_selected)

        btn_export = QPushButton("Export Laporan ke Excel (.xlsx)")
        btn_export.setProperty("class", "SuccessBtn")
        btn_export.clicked.connect(self._on_export_current_analytics)

        self.analytics_tabs = QTabWidget()
        self.analytics_tabs.setCornerWidget(btn_export, Qt.TopRightCorner)
        self.products_sub_stack = QStackedWidget()
        self.products_sub_stack.addWidget(self._create_products_overview_tab()) # index 0
        self.products_sub_stack.addWidget(self._create_product_detail_page())   # index 1

        self.analytics_tabs.addTab(self.products_sub_stack, "Performa Produk")
        self.analytics_tabs.addTab(self._create_video_db_page(), "Daftar Video")
        self.analytics_tabs.addTab(self._create_history_page(), "Riwayat && Log")

        layout.addWidget(self.analytics_tabs, 1)
        return page

    def _create_products_overview_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        grid = QGridLayout()
        grid.setSpacing(8)
        c1, self.lbl_an_creator = self._make_card("Creator Username", "-", "#4F46E5")
        c2, self.lbl_an_period = self._make_card("Analysis Period", "-", "#7C3AED")
        c3, self.lbl_an_videos = self._make_card("Total Videos", "0", "#1D63FF")
        c4, self.lbl_an_views = self._make_card("Total Views", "0", "#0D9F6E")
        c5, self.lbl_an_products = self._make_card("Total Unique Products", "0", "#D97706")
        c6, self.lbl_an_avg = self._make_card("Average Views / Video", "0", "#6366F1")
        grid.addWidget(c1, 0, 0)
        grid.addWidget(c2, 0, 1)
        grid.addWidget(c3, 0, 2)
        grid.addWidget(c4, 1, 0)
        grid.addWidget(c5, 1, 1)
        grid.addWidget(c6, 1, 2)
        layout.addLayout(grid)

        self.lbl_calc_note = QLabel(
            "Catatan Perhitungan: Jika satu video menampilkan beberapa produk, views video tersebut "
            "dihitung pada masing-masing produk sehingga akumulasi views produk dapat melebihi total views kreator."
        )
        self.lbl_calc_note.setVisible(False)

        chart_box = QGroupBox("Top 5 Produk Teratas")
        chart_box.setToolTip(self.lbl_calc_note.text())
        chart_layout = QVBoxLayout(chart_box)
        chart_layout.setContentsMargins(10, 4, 10, 6)
        chart_layout.setSpacing(2)
        self.chart_widget = ProductBarChartWidget()
        chart_layout.addWidget(self.chart_widget)
        layout.addWidget(chart_box)

        filter_bar = QHBoxLayout()
        self.inp_product_search = QLineEdit()
        self.inp_product_search.setPlaceholderText("Cari nama produk keranjang kuning...")
        self.inp_product_search.textChanged.connect(self._refresh_analytics_table)

        self.cmb_product_sort = QComboBox()
        self.cmb_product_sort.addItem("Sort: Total Views (Tertinggi)", ("total_views", True))
        self.cmb_product_sort.addItem("Sort: Total Views (Terendah)", ("total_views", False))
        self.cmb_product_sort.addItem("Sort: Jumlah Video (Terbanyak)", ("video_count", True))
        self.cmb_product_sort.addItem("Sort: Jumlah Video (Tersedikit)", ("video_count", False))
        self.cmb_product_sort.currentIndexChanged.connect(self._refresh_analytics_table)

        btn_open_detail = QPushButton("Lihat Detail Produk Terpilih")
        btn_open_detail.setProperty("class", "IndigoBtn")
        btn_open_detail.clicked.connect(self._on_open_selected_product_detail)

        filter_bar.addWidget(QLabel("Search Produk:"))
        filter_bar.addWidget(self.inp_product_search, 1)
        filter_bar.addWidget(self.cmb_product_sort)
        filter_bar.addWidget(btn_open_detail)
        layout.addLayout(filter_bar)

        self.tbl_analytics_products = QTableWidget(0, 6)
        self.tbl_analytics_products.setMinimumHeight(210)
        self.tbl_analytics_products.setHorizontalHeaderLabels(
            [
                "Product Name",
                "Videos",
                "Total Views",
                "Average Views",
                "Kontribusi Views (%)",
                "Product URL",
            ]
        )
        self._configure_table(self.tbl_analytics_products, stretch_columns=(0, 5))
        self.tbl_analytics_products.cellDoubleClicked.connect(
            lambda _r, _c: self._on_open_selected_product_detail()
        )
        layout.addWidget(self.tbl_analytics_products, 2)
        return page

    def _create_product_detail_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        top = QHBoxLayout()
        btn_back = QPushButton("Kembali ke Daftar Produk")
        btn_back.setProperty("class", "SecondaryBtn")
        btn_back.clicked.connect(lambda: self.products_sub_stack.setCurrentIndex(0))

        self.btn_pd_copy_name = QPushButton("Salin Nama Produk")
        self.btn_pd_copy_name.setProperty("class", "SecondaryBtn")
        self.btn_pd_copy_name.clicked.connect(self._on_copy_product_name)

        self.btn_pd_open_url = QPushButton("Buka Link Produk di Browser")
        self.btn_pd_open_url.setProperty("class", "IndigoBtn")
        self.btn_pd_open_url.clicked.connect(self._on_open_product_url)

        top.addWidget(btn_back)
        top.addStretch()
        top.addWidget(self.btn_pd_copy_name)
        top.addWidget(self.btn_pd_open_url)
        layout.addLayout(top)

        info_box = QGroupBox("Informasi && Performa Produk")
        info_form = QFormLayout(info_box)
        info_form.setSpacing(10)
        self.lbl_pd_name = QLabel("-")
        self.lbl_pd_name.setStyleSheet("font-size: 15px; font-weight: 800; color: #0F172A;")
        self.lbl_pd_status_badge = QLabel("Teridentifikasi")
        self.lbl_pd_status_badge.setStyleSheet(
            "background-color: #E6F8EF; color: #065F46; border-radius: 8px; padding: 4px 10px; font-weight: 700;"
        )
        self.lbl_pd_url = QLabel("-")
        self.lbl_pd_videos = QLabel("0")
        self.lbl_pd_videos.setStyleSheet("font-weight: 700;")
        self.lbl_pd_views = QLabel("0")
        self.lbl_pd_views.setStyleSheet("font-weight: 700; color: #4F46E5;")
        self.lbl_pd_avg = QLabel("0")
        self.lbl_pd_contrib = QLabel("0%")
        self.lbl_pd_contrib.setStyleSheet("font-weight: 700; color: #0D9F6E;")

        info_form.addRow("Nama Produk:", self.lbl_pd_name)
        info_form.addRow("Status Identitas:", self.lbl_pd_status_badge)
        info_form.addRow("Link Produk:", self.lbl_pd_url)
        info_form.addRow("Jumlah Video:", self.lbl_pd_videos)
        info_form.addRow("Total Views:", self.lbl_pd_views)
        info_form.addRow("Rata-rata Views:", self.lbl_pd_avg)
        info_form.addRow("Persentase Kontribusi:", self.lbl_pd_contrib)
        layout.addWidget(info_box)

        vid_box = QGroupBox("Daftar Video yang Mempromosikan Produk Ini")
        v_layout = QVBoxLayout(vid_box)
        self.tbl_pd_videos = QTableWidget(0, 4)
        self.tbl_pd_videos.setHorizontalHeaderLabels(
            ["Video URL", "Upload Date", "Views", "Creator Username"]
        )
        self._configure_table(self.tbl_pd_videos, stretch_columns=(0,))
        v_layout.addWidget(self.tbl_pd_videos)
        layout.addWidget(vid_box, 1)
        return page

    def _create_video_db_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        filter_bar = QHBoxLayout()
        self.inp_vid_search = QLineEdit()
        self.inp_vid_search.setPlaceholderText("Cari Video ID / URL / Kreator...")
        self.inp_vid_search.textChanged.connect(self._refresh_video_db_table)

        self.cmb_vid_filter = QComboBox()
        self.cmb_vid_filter.addItem("Semua Video", "all")
        self.cmb_vid_filter.addItem("Hanya Video dengan Keranjang Kuning", "with_product")
        self.cmb_vid_filter.addItem("Filter Video Tanpa Produk", "without_product")
        self.cmb_vid_filter.currentIndexChanged.connect(self._refresh_video_db_table)

        self.cmb_vid_sort = QComboBox()
        self.cmb_vid_sort.addItem("Sort Views: Tertinggi", "views_desc")
        self.cmb_vid_sort.addItem("Sort Views: Terendah", "views_asc")
        self.cmb_vid_sort.currentIndexChanged.connect(self._refresh_video_db_table)

        btn_open_vid = QPushButton("Open Video URL")
        btn_open_vid.setProperty("class", "IndigoBtn")
        btn_open_vid.clicked.connect(self._on_open_selected_video_url)

        filter_bar.addWidget(self.inp_vid_search, 1)
        filter_bar.addWidget(self.cmb_vid_filter)
        filter_bar.addWidget(self.cmb_vid_sort)
        filter_bar.addWidget(btn_open_vid)
        layout.addLayout(filter_bar)

        self.tbl_video_db = QTableWidget(0, 7)
        self.tbl_video_db.setHorizontalHeaderLabels(
            [
                "Job ID",
                "Creator Username",
                "Video ID",
                "Video URL",
                "Upload Date",
                "Views",
                "Jumlah Produk",
            ]
        )
        self._configure_table(self.tbl_video_db, stretch_columns=(3,))
        layout.addWidget(self.tbl_video_db, 1)
        return page

    def _create_history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        action_bar = QHBoxLayout()
        btn_open_res = QPushButton("Buka di Tab Performa Produk")
        btn_open_res.setProperty("class", "IndigoBtn")
        btn_open_res.clicked.connect(self._on_history_open_result)

        btn_export_again = QPushButton("Export Again (.xlsx)")
        btn_export_again.setProperty("class", "SuccessBtn")
        btn_export_again.clicked.connect(self._on_history_export_excel)

        btn_resume = QPushButton("Resume Pekerjaan Terputus")
        btn_resume.setProperty("class", "SecondaryBtn")
        btn_resume.clicked.connect(self._on_history_resume_job)

        btn_delete = QPushButton("Delete History")
        btn_delete.setProperty("class", "DangerBtn")
        btn_delete.clicked.connect(self._on_history_delete_job)

        action_bar.addWidget(btn_open_res)
        action_bar.addWidget(btn_export_again)
        action_bar.addWidget(btn_resume)
        action_bar.addWidget(btn_delete)
        action_bar.addStretch()
        layout.addLayout(action_bar)

        splitter = QSplitter(Qt.Vertical)
        self.tbl_history = QTableWidget(0, 7)
        self.tbl_history.setHorizontalHeaderLabels(
            [
                "Job ID",
                "Creator Username",
                "Period",
                "Scraping Date",
                "Total Videos",
                "Total Products",
                "Job Status",
            ]
        )
        self._configure_table(self.tbl_history, stretch_columns=(1, 3, 6))
        self.tbl_history.itemSelectionChanged.connect(self._on_history_selection_changed)
        splitter.addWidget(self.tbl_history)

        log_group = QGroupBox("Log Pekerjaan Terpilih (View Logs)")
        lg_layout = QVBoxLayout(log_group)
        self.txt_history_logs = QPlainTextEdit()
        self.txt_history_logs.setReadOnly(True)
        self.txt_history_logs.setStyleSheet(
            "font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 12px; "
            "background-color: #0E132C; color: #E2E8F0; border-radius: 10px; padding: 10px;"
        )
        lg_layout.addWidget(self.txt_history_logs)
        splitter.addWidget(log_group)

        layout.addWidget(splitter, 1)
        return page

    # ------------------------------------------------------------------
    # View Refresh & State Synchronization
    # ------------------------------------------------------------------
    def refresh_all_views(self) -> None:
        self._refresh_device_table()
        self._update_device_badges()
        self._update_preflight_summary()
        self._refresh_dashboard_data()
        self._refresh_analytics_selector()
        self._refresh_video_db_table()
        self._refresh_history_table()
        self.lbl_device_last_updated.setText(
            f"Terakhir diperbarui: {_now_human_str()}"
        )

    def _update_device_badges(self) -> None:
        """Update both the top header device pill and Page 2 mint alert banner."""
        self.dev_banner_frame.setVisible(True)
        if not self.active_device:
            self.lbl_topbar_device_badge.setText("Belum Terhubung")
            self.lbl_topbar_device_badge.setStyleSheet(
                "background-color: #FEE2E2; color: #991B1B; border: 1px solid #FCA5A5; "
                "border-radius: 18px; padding: 7px 16px; font-size: 12px; font-weight: 700;"
            )
            self.dev_banner_frame.setStyleSheet(
                "QFrame { background-color: #FEF2F2; border: 1px solid #FECACA; border-radius: 14px; }"
            )
            self.lbl_dev_banner_badge.setText(
                "Perangkat Aktif: Belum ada perangkat Android yang dipilih. "
                "Gunakan tombol 'Pair dengan Scan QR' atau 'Scan USB (ADB)'."
            )
            self.lbl_dev_banner_badge.setStyleSheet(
                "color: #991B1B; font-size: 13px; font-weight: 700; border: none; background: transparent;"
            )
            return

        dev = self.active_device
        status = dev.connection_status
        summary = f"{dev.device_name} (Android {dev.android_version} • {dev.connection_type} • {status})"
        self.lbl_topbar_device_badge.setText(summary)

        if status == DeviceStatus.CONNECTED.value:
            self.lbl_topbar_device_badge.setStyleSheet(
                "background-color: #E6F8EF; color: #065F46; border: 1px solid #A7F3D0; "
                "border-radius: 18px; padding: 7px 16px; font-size: 12px; font-weight: 700;"
            )
            self.dev_banner_frame.setStyleSheet(
                "QFrame { background-color: #E6F8EF; border: 1px solid #A7F3D0; border-radius: 14px; }"
            )
            self.lbl_dev_banner_badge.setText(
                f"Perangkat aktif & siap scraping: {dev.device_name} | "
                f"Serial/IP: {dev.device_serial} | Android {dev.android_version} ({dev.connection_type})"
            )
            self.lbl_dev_banner_badge.setStyleSheet(
                "color: #065F46; font-size: 13px; font-weight: 700; border: none; background: transparent;"
            )
        elif status in (
            DeviceStatus.PAIRING_REQUIRED.value,
            DeviceStatus.UNAUTHORIZED.value,
        ):
            self.lbl_topbar_device_badge.setStyleSheet(
                "background-color: #FEF3C7; color: #92400E; border: 1px solid #FDE68A; "
                "border-radius: 18px; padding: 7px 16px; font-size: 12px; font-weight: 700;"
            )
            self.dev_banner_frame.setStyleSheet(
                "QFrame { background-color: #FFFBEB; border: 1px solid #FDE68A; border-radius: 14px; }"
            )
            self.lbl_dev_banner_badge.setText(
                f"Perangkat membutuhkan otorisasi/pairing: {dev.device_name} ({dev.device_serial}) — Status: {status}"
            )
            self.lbl_dev_banner_badge.setStyleSheet(
                "color: #92400E; font-size: 13px; font-weight: 700; border: none; background: transparent;"
            )
        else:
            self.lbl_topbar_device_badge.setStyleSheet(
                "background-color: #FEE2E2; color: #991B1B; border: 1px solid #FCA5A5; "
                "border-radius: 18px; padding: 7px 16px; font-size: 12px; font-weight: 700;"
            )
            self.dev_banner_frame.setStyleSheet(
                "QFrame { background-color: #FEF2F2; border: 1px solid #FECACA; border-radius: 14px; }"
            )
            self.lbl_dev_banner_badge.setText(
                f"Perangkat terputus: {dev.device_name} ({dev.device_serial}) — Status: {status}"
            )
            self.lbl_dev_banner_badge.setStyleSheet(
                "color: #991B1B; font-size: 13px; font-weight: 700; border: none; background: transparent;"
            )

    def _refresh_dashboard_data(self) -> None:
        status_str = (
            f"{self.active_device.device_name} ({self.active_device.connection_status})"
            if self.active_device
            else DeviceStatus.DISCONNECTED.value
        )
        self.lbl_dash_device.setText(status_str)

        jobs = self.db.list_jobs()
        videos = self.db.list_videos()
        creators = {j.creator_username.lower() for j in jobs}
        with self.db.connection() as conn:
            prod_count = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]

        self.lbl_dash_jobs.setText(str(len(jobs)))
        self.lbl_dash_creators.setText(str(len(creators)))
        self.lbl_dash_videos.setText(str(len(videos)))
        self.lbl_dash_products.setText(str(prod_count))

        self.tbl_dash_recent.setRowCount(0)
        for job in jobs[:8]:
            r = self.tbl_dash_recent.rowCount()
            self.tbl_dash_recent.insertRow(r)
            vals = [
                f"#{job.id}",
                job.creator_username,
                f"{job.period_days} Hari",
                str(job.processed_videos),
                str(job.total_products),
                job.job_status,
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, job.id)
                if c == 5:
                    _style_status_item(item, val)
                self.tbl_dash_recent.setItem(r, c, item)

        self.tbl_dash_top_products.setRowCount(0)
        if jobs:
            latest_valid = next((j for j in jobs if j.processed_videos > 0), jobs[0])
            latest_report = self.analytics.build_job_report(latest_valid.id)
            for prod in latest_report.products[:6]:
                r = self.tbl_dash_top_products.rowCount()
                self.tbl_dash_top_products.insertRow(r)
                vals = [
                    prod.product_name,
                    str(prod.video_count),
                    _fmt_int(prod.total_views),
                    _fmt_int(int(round(prod.average_views))),
                ]
                for c, val in enumerate(vals):
                    self.tbl_dash_top_products.setItem(r, c, QTableWidgetItem(val))

    def _on_dash_recent_double_clicked(self, row: int, _col: int) -> None:
        item = self.tbl_dash_recent.item(row, 0)
        if item and item.data(Qt.UserRole) is not None:
            self.current_analytics_job_id = int(item.data(Qt.UserRole))
            self.analytics_tabs.setCurrentIndex(0)
            self.products_sub_stack.setCurrentIndex(0)
            self.navigate_to(self.PAGE_ANALYTICS)

    def _refresh_device_table(self) -> None:
        all_devices = self.db.list_devices()
        devices = [
            d
            for d in all_devices
            if self.use_simulator_driver or not d.device_serial.startswith("SIMULATOR")
        ]
        if self.active_device is None and devices:
            real_connected = [
                d
                for d in devices
                if d.connection_status == DeviceStatus.CONNECTED.value
                and not d.device_serial.startswith("SIMULATOR")
            ]
            any_connected = [
                d for d in devices if d.connection_status == DeviceStatus.CONNECTED.value
            ]
            self.active_device = (
                real_connected[0]
                if real_connected
                else (any_connected[0] if any_connected else devices[0])
            )
            self.use_simulator_driver = self.active_device.device_serial.startswith(
                "SIMULATOR"
            )

        query = (
            self.inp_device_search.text().strip().lower()
            if hasattr(self, "inp_device_search")
            else ""
        )

        self.tbl_devices.blockSignals(True)
        self.tbl_devices.setRowCount(0)
        selected_row = -1
        for dev in devices:
            if query and not (
                query in dev.device_name.lower()
                or query in dev.model.lower()
                or query in dev.device_serial.lower()
                or query in dev.connection_type.lower()
            ):
                continue

            r = self.tbl_devices.rowCount()
            self.tbl_devices.insertRow(r)
            vals = [
                dev.device_name,
                dev.model,
                dev.android_version,
                dev.connection_type,
                dev.device_serial,
                dev.connection_status,
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, dev.device_serial)
                if c == 0:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                elif c == 5:
                    _style_status_item(item, dev.connection_status)
                self.tbl_devices.setItem(r, c, item)
            if self.active_device and dev.device_serial == self.active_device.device_serial:
                selected_row = r
        if selected_row >= 0:
            self.tbl_devices.selectRow(selected_row)
        self.tbl_devices.blockSignals(False)

    def _update_preflight_summary(self) -> None:
        if self.active_device:
            self.lbl_pre_device.setText(
                f"{self.active_device.device_name} (Android {self.active_device.android_version}, {self.active_device.connection_type})"
            )
            self.lbl_pre_status.setText(self.active_device.connection_status)
        else:
            self.lbl_pre_device.setText("Belum ada perangkat dipilih")
            self.lbl_pre_status.setText(DeviceStatus.DISCONNECTED.value)

        self.lbl_pre_username.setText(self.inp_creator_username.text().strip() or "-")
        period = self.cmb_period.currentData() or 7
        self.lbl_pre_period.setText(f"{period} Hari Terakhir")

    # ------------------------------------------------------------------
    # Device Actions (Menu 2)
    # ------------------------------------------------------------------
    def _on_scan_devices(self) -> None:
        self.use_simulator_driver = False
        self.lbl_device_poc_status.setText(
            "Memindai perangkat Android yang terhubung via USB (ADB)..."
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            found = self.device_manager.scan_devices()
        finally:
            QApplication.restoreOverrideCursor()

        if found:
            self.active_device = found[0]
            self.lbl_device_poc_status.setText(
                f"Ditemukan {len(found)} perangkat via ADB. Perangkat aktif: {self.active_device.device_name}."
            )
        else:
            ok_adb, adb_msg = self.device_manager.is_adb_available()
            if not ok_adb:
                self.lbl_device_poc_status.setText(
                    f"Tidak ada perangkat USB terdeteksi ({adb_msg})."
                )
            else:
                self.lbl_device_poc_status.setText(
                    "ADB siap, tetapi belum ada HP Android yang tercolok USB."
                )
        self.refresh_all_views()

    def _on_refresh_devices(self) -> None:
        self.use_simulator_driver = False
        self.lbl_device_poc_status.setText(
            "Memperbarui daftar perangkat Android (USB & Wireless Debugging)..."
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            usb_found = self.device_manager.scan_devices()
            wifi_found, _ = self.device_manager.scan_wireless_devices(auto_connect=True)
            if self.active_device and not self.active_device.device_serial.startswith("SIMULATOR"):
                ready = self.device_manager.ensure_device_ready(self.active_device)
                if ready is not None:
                    self.active_device = ready
            all_found = usb_found or wifi_found
            if all_found:
                connected = [
                    d
                    for d in all_found
                    if d.connection_status == DeviceStatus.CONNECTED.value
                ]
                if connected:
                    self.active_device = connected[0]
        finally:
            QApplication.restoreOverrideCursor()

        self.refresh_all_views()
        if self.active_device:
            self.lbl_device_poc_status.setText(
                f"Daftar perangkat diperbarui. Perangkat terpilih: {self.active_device.device_name} ({self.active_device.connection_status})."
            )
        else:
            self.lbl_device_poc_status.setText(
                "Daftar perangkat diperbarui. Belum ada perangkat Android yang terhubung."
            )

    def _on_scan_wireless_devices(self) -> None:
        self.use_simulator_driver = False
        self.lbl_device_poc_status.setText(
            "Memindai jaringan Wi-Fi (mDNS / Bonjour & LAN) untuk mencari HP dengan Wireless Debugging aktif..."
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            devices, services = self.device_manager.scan_wireless_devices(
                auto_connect=True
            )
        finally:
            QApplication.restoreOverrideCursor()

        if hasattr(self, "inp_pair_addr") and hasattr(self, "inp_conn_addr"):
            for svc in services:
                if svc.is_pairing_service:
                    self.inp_pair_addr.setText(svc.host_port)
                elif svc.is_connect_service:
                    self.inp_conn_addr.setText(svc.host_port)
                    if not self.inp_pair_addr.text().strip():
                        ip_only = svc.host_port.split(":")[0]
                        self.inp_pair_addr.setText(f"{ip_only}:")

        if devices:
            connected = [
                d
                for d in devices
                if d.connection_status == DeviceStatus.CONNECTED.value
            ]
            self.active_device = connected[0] if connected else devices[0]
            self.lbl_device_poc_status.setText(
                f"Scan Wireless selesai: ditemukan {len(devices)} perangkat / {len(services)} layanan mDNS. "
                f"Perangkat terpilih: {self.active_device.device_name} ({self.active_device.connection_status})."
            )
        else:
            self.lbl_device_poc_status.setText(
                "Tidak ditemukan perangkat dengan Wireless Debugging aktif di jaringan Wi-Fi yang sama. "
                "Pastikan komputer & HP berada di Wi-Fi yang sama dan menu Wireless Debugging sedang dibuka di HP."
            )
        self.refresh_all_views()

    def _on_activate_simulator(self) -> None:
        self.use_simulator_driver = True
        sim_dev = DeviceInfo(
            device_serial="SIMULATOR-ANDROID-13",
            device_name="Samsung Galaxy A52 (Simulator)",
            model="SM-A525F",
            android_version="13",
            connection_type=ConnectionType.USB.value,
            connection_status=DeviceStatus.CONNECTED.value,
            sdk_int=33,
            last_connected_at=utc_now_iso(),
        )
        self.db.upsert_device(sim_dev)
        self.active_device = sim_dev
        self.lbl_device_poc_status.setText(
            "Perangkat Simulasi Android 13 aktif (Connected). Siap untuk pengujian koneksi dan Scraping."
        )
        self.refresh_all_views()

    def _on_run_poc_check(self) -> None:
        if not self.active_device:
            self._refresh_device_table()
        if not self.active_device:
            QMessageBox.warning(
                self,
                "Perangkat Belum Dipilih",
                "Silakan hubungkan atau pilih perangkat terlebih dahulu.",
            )
            return
        self.lbl_device_poc_status.setText(
            f"Sedang mengetes koneksi TikTok pada {self.active_device.device_name} ({self.active_device.device_serial})..."
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            if not self.use_simulator_driver:
                ready_dev = self.device_manager.ensure_device_ready(self.active_device)
                if ready_dev is not None:
                    self.active_device = ready_dev
            driver = self._get_ui_driver_for_active_device()
            report = driver.verify_screen_access()
        finally:
            QApplication.restoreOverrideCursor()
        self.refresh_all_views()

        sample_str = ", ".join(report.sample_texts[:6]) if report.sample_texts else "-"
        self.lbl_device_poc_status.setText(
            f"[{report.driver_name}] {report.message} | Elemen terbaca: {report.ui_node_count} | Sampel: {sample_str}"
        )
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            detail_msg = (
                f"Status Koneksi  : {'BERHASIL' if report.success else 'GAGAL'}\n"
                f"Perangkat       : {self.active_device.device_name} ({report.device_serial})\n"
                f"Driver          : {report.driver_name}\n"
                f"Layar Terbuka   : {'Ya' if report.screen_unlocked else 'Tidak (Terkunci)'}\n"
                f"Aplikasi TikTok : {report.tiktok_package or 'Tidak ditemukan'}\n"
                f"Elemen UI       : {report.ui_node_count} node terbaca\n"
                f"Sampel Teks UI  : {sample_str}\n\n"
                f"Pesan: {report.message}"
            )
            if report.success:
                QMessageBox.information(
                    self, "Hasil Tes Koneksi TikTok", detail_msg
                )
            else:
                QMessageBox.warning(self, "Hasil Tes Koneksi TikTok", detail_msg)

    def _on_open_qr_pairing_dialog(self) -> None:
        self.use_simulator_driver = False
        dlg = QrPairingDialog(self.device_manager, self)
        if dlg.exec() == QDialog.Accepted:
            if dlg.paired_device:
                self.active_device = dlg.paired_device
                self.lbl_device_poc_status.setText(
                    f"Pairing & Auto-Connect via QR Code Berhasil: {self.active_device.device_name} ({self.active_device.device_serial})!"
                )
            self.refresh_all_views()

    def _on_pair_wireless(self) -> None:
        pair_addr = self.inp_pair_addr.text().strip()
        conn_addr = self.inp_conn_addr.text().strip()
        if pair_addr and conn_addr and pair_addr == conn_addr:
            QMessageBox.warning(
                self,
                "Port Pairing Tidak Boleh Sama dengan Port Connect",
                f"Anda memasukkan {pair_addr} di kedua kolom (Pairing dan Connect).\n\n"
                "Pada Android 12+, Port Pairing BERBEDA dengan Port Connect:\n"
                "1. Di HP Android (menu Wireless Debugging), ketuk 'Pair device with pairing code'.\n"
                "2. Di dalam kotak popup yang muncul, lihat angka 'IP address & Port' di bawah 6 digit kode.\n"
                "3. Masukkan IP:Port dari dalam popup tersebut ke kolom 'Pairing IP:Port', lalu klik 'Pair Device'.",
            )
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            ok, msg = self.device_manager.pair_wireless_device(
                pair_addr, self.inp_pair_code.text()
            )
            if ok:
                if conn_addr and ":" in conn_addr:
                    ok_c, _, dev_c = self.device_manager.connect_wireless_device(
                        conn_addr
                    )
                    if ok_c:
                        self.active_device = dev_c
                if not (
                    self.active_device
                    and self.active_device.connection_status
                    == DeviceStatus.CONNECTED.value
                ):
                    devices, _ = self.device_manager.scan_wireless_devices(
                        auto_connect=True
                    )
                    connected = [
                        d
                        for d in devices
                        if d.connection_status == DeviceStatus.CONNECTED.value
                    ]
                    if connected:
                        self.active_device = connected[0]
                if (
                    self.active_device
                    and self.active_device.connection_status
                    == DeviceStatus.CONNECTED.value
                ):
                    self.lbl_device_poc_status.setText(
                        f"Pairing & Auto-Connect Berhasil: {self.active_device.device_name} (Android {self.active_device.android_version}) — Connected!"
                    )
        finally:
            QApplication.restoreOverrideCursor()

        if ok:
            QMessageBox.information(self, "Pairing Berhasil", msg)
        else:
            QMessageBox.warning(self, "Pairing Gagal", msg)
        self.refresh_all_views()

    def _on_connect_wireless(self) -> None:
        self.use_simulator_driver = False
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            ok, msg, dev = self.device_manager.connect_wireless_device(
                self.inp_conn_addr.text()
            )
        finally:
            QApplication.restoreOverrideCursor()
        self.active_device = dev
        if ok:
            QMessageBox.information(self, "Wireless Connected", msg)
        else:
            QMessageBox.warning(self, "Koneksi Wireless Gagal", msg)
        self.refresh_all_views()

    def _on_disconnect_device(self) -> None:
        if not self.active_device:
            return
        self.device_manager.disconnect_device(self.active_device.device_serial)
        self.active_device.connection_status = DeviceStatus.DISCONNECTED.value
        self.lbl_device_poc_status.setText("Perangkat telah diputus (Disconnected).")
        self.refresh_all_views()

    def _on_select_device_row(self) -> None:
        rows = self.tbl_devices.selectionModel().selectedRows()
        if rows:
            row_idx = rows[0].row()
        else:
            row_idx = self.tbl_devices.currentRow()
        if row_idx < 0:
            return
        serial_item = self.tbl_devices.item(row_idx, 4)
        if not serial_item:
            return
        serial = serial_item.text()
        for dev in self.db.list_devices():
            if dev.device_serial == serial:
                self.active_device = dev
                self.use_simulator_driver = serial.startswith("SIMULATOR")
                if (
                    dev.connection_type == ConnectionType.WIRELESS.value
                    and ":" in serial
                    and hasattr(self, "inp_pair_addr")
                    and hasattr(self, "inp_conn_addr")
                ):
                    if "[Pairing Port]" in dev.device_name:
                        self.inp_pair_addr.setText(serial)
                    else:
                        self.inp_conn_addr.setText(serial)
                        if not self.inp_pair_addr.text().strip():
                            ip_only = serial.split(":")[0]
                            self.inp_pair_addr.setText(f"{ip_only}:")
                break
        self._update_device_badges()
        self._update_preflight_summary()

    def _get_ui_driver_for_active_device(self) -> UiAutomationDriver:
        if self.use_simulator_driver or (
            self.active_device
            and self.active_device.device_serial.startswith("SIMULATOR")
        ):
            return MockUiDriver(
                self.active_device.device_serial
                if self.active_device
                else "SIMULATOR-ANDROID-13"
            )
        open_links = (
            self.chk_open_yellow_basket.isChecked()
            if hasattr(self, "chk_open_yellow_basket")
            else False
        )
        return AdbUiDriver(
            device_serial=self.active_device.device_serial if self.active_device else "",
            adb_path=self.device_manager.adb_path,
            open_yellow_basket_links=open_links,
        )

    # ------------------------------------------------------------------
    # Scraping Execution & Background Worker (Menu 3)
    # ------------------------------------------------------------------
    def _on_reset_scraping_form(self) -> None:
        self.inp_creator_username.clear()
        self.cmb_period.setCurrentIndex(0)
        if hasattr(self, "chk_open_yellow_basket"):
            self.chk_open_yellow_basket.setChecked(False)
        if not self.controller.is_busy:
            self.prog_bar.setRange(0, 100)
            self.prog_bar.setValue(0)
            self.txt_monitor_logs.clear()
            self.lbl_mon_phase_banner.setText("Menunggu pekerjaan dimulai...")
        self._update_preflight_summary()

    def _on_start_scraping_clicked(self) -> None:
        self._launch_scraping_worker(resume_job_id=None)

    def _launch_scraping_worker(self, resume_job_id: Optional[int] = None) -> None:
        if self.controller.is_busy:
            QMessageBox.warning(
                self,
                "Pekerjaan Sedang Berjalan",
                "Sistem sedang menjalankan pekerjaan scraping lain. Tunggu hingga selesai.",
            )
            return

        if (
            self.active_device
            and not self.use_simulator_driver
            and not self.active_device.device_serial.startswith("SIMULATOR")
        ):
            ready_dev = self.device_manager.ensure_device_ready(self.active_device)
            if ready_dev is not None:
                self.active_device = ready_dev
            self.refresh_all_views()

        ok_dev, dev_msg = DeviceManager.validate_device_for_scraping(
            self.active_device
        )
        if not ok_dev:
            QMessageBox.warning(self, "Validasi Perangkat Gagal", dev_msg)
            return

        if not self.chk_compliance.isChecked():
            QMessageBox.warning(
                self,
                "Otorisasi Perangkat",
                "Harap centang persetujuan otorisasi perangkat terlebih dahulu.",
            )
            return

        username = self.inp_creator_username.text().strip()
        period_days = int(self.cmb_period.currentData() or 7)
        if resume_job_id is not None:
            old_job = self.db.get_job(resume_job_id)
            if old_job:
                username = old_job.creator_username
                period_days = old_job.period_days

        clean_username = username.strip().lstrip("@").strip()
        if not clean_username:
            QMessageBox.warning(
                self,
                "Username Kreator Kosong",
                "Harap masukkan username kreator TikTok (contoh: @kakmuti7) sebelum memulai scraping.",
            )
            return
        username = f"@{clean_username}"

        driver = self._get_ui_driver_for_active_device()

        self._discovered_during_scan = 0
        self.txt_monitor_logs.clear()
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(0)
        self.lbl_mon_user.setText(username)
        self.lbl_mon_period.setText(f"{period_days} Hari")
        self.lbl_mon_status.setText(JobStatus.INITIALIZING.value)
        self.lbl_mon_duration.setText("00:00")
        self.lbl_mon_total_v.setText("0")
        self.lbl_mon_proc_v.setText("0")
        self.lbl_mon_fail_v.setText("0")
        self.lbl_mon_prod.setText("0")
        self.lbl_mon_phase_banner.setText(
            f"Sedang memproses {username} (Periode {period_days} Hari)..."
        )
        self.btn_cancel_job.setEnabled(True)
        self.btn_mon_view_analytics.setEnabled(False)
        self.btn_mon_export_excel.setEnabled(False)
        self._job_start_epoch = time.time()
        self._duration_timer.start()

        self.navigate_to(self.PAGE_MONITOR)

        self._worker_thread = QThread(self)
        self._worker = ScrapingWorker(
            controller=self.controller,
            creator_username=username,
            period_days=period_days,
            device=self.active_device,
            driver=driver,
            compliance_accepted=True,
            resume_job_id=resume_job_id,
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.progress_changed.connect(self._on_worker_progress)
        self._worker.job_finished.connect(self._on_worker_finished)
        self._worker.job_error.connect(self._on_worker_error)
        self._worker_thread.start()

    def _on_worker_progress(self, snap: ScrapingProgressSnapshot) -> None:
        self.lbl_mon_user.setText(snap.creator_username)
        self.lbl_mon_period.setText(f"{snap.period_days} Hari")
        self.lbl_mon_status.setText(snap.job_status)

        m_vid = _VIDEO_NUM_RE.search(snap.latest_log_message or "")
        if m_vid:
            self._discovered_during_scan = max(
                self._discovered_during_scan, int(m_vid.group(1))
            )

        shown_total = max(snap.total_videos, self._discovered_during_scan, snap.processed_videos)
        self.lbl_mon_total_v.setText(str(shown_total))
        self.lbl_mon_proc_v.setText(str(snap.processed_videos))
        self.lbl_mon_fail_v.setText(str(snap.failed_videos))
        self.lbl_mon_prod.setText(str(snap.total_products))

        if snap.job_status == JobStatus.SCANNING_VIDEOS.value:
            self.prog_bar.setRange(0, 0)
            self.lbl_mon_phase_banner.setText(
                f"Fase Auto-Scroll Profil ({snap.processed_videos} video tersimpan): {snap.latest_log_message}"
            )
        else:
            self.prog_bar.setRange(0, 100)
            self.prog_bar.setValue(snap.progress_percent)
            self.lbl_mon_phase_banner.setText(
                f"[{snap.progress_percent}%] {snap.job_status}: {snap.latest_log_message}"
            )

        self.txt_monitor_logs.appendPlainText(
            f"[{snap.job_status}] {snap.latest_log_message}"
        )

    def _on_worker_finished(self, job: ScrapingJobRecord) -> None:
        self._cleanup_worker_thread()
        self.prog_bar.setRange(0, 100)
        has_data = job.processed_videos > 0
        if (
            job.job_status
            in (
                JobStatus.COMPLETED.value,
                JobStatus.COMPLETED_WITH_WARNINGS.value,
            )
            or has_data
        ):
            self.prog_bar.setValue(100)
            self.btn_mon_view_analytics.setEnabled(True)
            self.btn_mon_export_excel.setEnabled(True)
            self.lbl_mon_phase_banner.setText(
                f"Pekerjaan Selesai ({job.job_status}) — {job.processed_videos} video & {job.total_products} produk siap dianalisis / di-export."
            )
        else:
            self.lbl_mon_phase_banner.setText(
                f"Pekerjaan Berhenti ({job.job_status}): {job.error_message or '-'}"
            )
        self.lbl_mon_status.setText(job.job_status)
        self.current_analytics_job_id = job.id
        self.refresh_all_views()

    def _on_worker_error(self, err_msg: str) -> None:
        self._cleanup_worker_thread()
        self.prog_bar.setRange(0, 100)
        self.lbl_mon_status.setText(JobStatus.FAILED.value)
        self.lbl_mon_phase_banner.setText(f"Pekerjaan Gagal: {err_msg}")
        self.txt_monitor_logs.appendPlainText(f"[ERROR] {err_msg}")
        QMessageBox.critical(self, "Scraping Error", err_msg)
        self.refresh_all_views()

    def _cleanup_worker_thread(self) -> None:
        self._duration_timer.stop()
        self.btn_cancel_job.setEnabled(False)
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
            self._worker_thread = None
            self._worker = None

    def _on_cancel_running_job(self) -> None:
        self.btn_cancel_job.setEnabled(False)
        self.lbl_mon_phase_banner.setText(
            "Menghentikan scraping & menyelesaikan analisis untuk video yang sudah terkumpul..."
        )
        self.controller.request_cancel()

    def _update_monitor_duration(self) -> None:
        if self._job_start_epoch is None:
            return
        elapsed = int(time.time() - self._job_start_epoch)
        mins, secs = divmod(elapsed, 60)
        self.lbl_mon_duration.setText(f"{mins:02d}:{secs:02d}")

    # ------------------------------------------------------------------
    # Analytics, Product Detail, Video DB & History (Menu 4)
    # ------------------------------------------------------------------
    def _refresh_analytics_selector(self) -> None:
        jobs = self.db.list_jobs()
        self.cmb_analytics_job.blockSignals(True)
        self.cmb_analytics_job.clear()
        for job in jobs:
            self.cmb_analytics_job.addItem(
                f"Job #{job.id} — {job.creator_username} ({job.period_days}d) [{job.job_status}]",
                job.id,
            )
        latest_valid = next(
            (j for j in jobs if j.processed_videos > 0),
            jobs[0] if jobs else None,
        )
        if self.current_analytics_job_id is not None:
            idx = self.cmb_analytics_job.findData(self.current_analytics_job_id)
            if idx >= 0:
                self.cmb_analytics_job.setCurrentIndex(idx)
            elif latest_valid is not None:
                self.current_analytics_job_id = latest_valid.id
        elif latest_valid is not None:
            self.current_analytics_job_id = latest_valid.id
            idx = self.cmb_analytics_job.findData(latest_valid.id)
            if idx >= 0:
                self.cmb_analytics_job.setCurrentIndex(idx)
        self.cmb_analytics_job.blockSignals(False)
        self._refresh_analytics_table()

    def _on_analytics_job_selected(self) -> None:
        job_id = self.cmb_analytics_job.currentData()
        if job_id is not None:
            self.current_analytics_job_id = int(job_id)
            self._refresh_analytics_table()

    def _refresh_analytics_table(self) -> None:
        if self.current_analytics_job_id is None:
            self.tbl_analytics_products.setRowCount(0)
            self.chart_widget.set_products([])
            return

        sort_data = self.cmb_product_sort.currentData() or ("total_views", True)
        sort_by, descending = sort_data
        search_q = self.inp_product_search.text().strip()

        try:
            report = self.analytics.build_job_report(
                self.current_analytics_job_id,
                sort_by=sort_by,
                descending=descending,
                search_query=search_q,
            )
        except ValueError:
            return

        self.current_report = report
        self.lbl_an_creator.setText(report.creator_username)
        self.lbl_an_period.setText(f"{report.period_days} Days")
        self.lbl_an_videos.setText(str(report.total_videos))
        self.lbl_an_views.setText(_fmt_int(report.total_views))
        self.lbl_an_products.setText(str(report.total_unique_products))
        self.lbl_an_avg.setText(_fmt_int(int(round(report.average_views_per_video))))
        self.chart_widget.set_products(report.products)

        self.tbl_analytics_products.setRowCount(0)
        for prod in report.products:
            r = self.tbl_analytics_products.rowCount()
            self.tbl_analytics_products.insertRow(r)
            name_display = (
                f"{prod.product_name} [AMBIGU]"
                if prod.is_ambiguous
                else prod.product_name
            )
            vals = [
                name_display,
                str(prod.video_count),
                _fmt_int(prod.total_views),
                _fmt_int(int(round(prod.average_views))),
                f"{prod.view_contribution_pct:.2f}%",
                prod.product_url or "Tidak Tersedia",
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, prod.product_id)
                self.tbl_analytics_products.setItem(r, c, item)

    def _on_open_selected_product_detail(self) -> None:
        if not self.current_report:
            return
        rows = self.tbl_analytics_products.selectionModel().selectedRows()
        if not rows:
            cur_row = self.tbl_analytics_products.currentRow()
            if cur_row >= 0:
                pid = self.tbl_analytics_products.item(cur_row, 0).data(Qt.UserRole)
                prod = next(
                    (
                        p
                        for p in self.current_report.products
                        if p.product_id == pid
                    ),
                    None,
                )
            elif self.current_report.products:
                prod = self.current_report.products[0]
            else:
                return
        else:
            row_idx = rows[0].row()
            pid = self.tbl_analytics_products.item(row_idx, 0).data(Qt.UserRole)
            prod = next(
                (p for p in self.current_report.products if p.product_id == pid),
                None,
            )
        if prod is None:
            return

        self._populate_product_detail(prod)
        self.navigate_to(self.PAGE_PRODUCT_DETAIL)

    def _populate_product_detail(self, prod: ProductPerformance) -> None:
        self.lbl_pd_name.setText(prod.product_name)
        if prod.is_ambiguous:
            self.lbl_pd_status_badge.setText(
                "Ambigu (Nama Sama Tanpa Link Pembeda)"
            )
            self.lbl_pd_status_badge.setStyleSheet(
                "background-color: #FEF3C7; color: #92400E; border-radius: 8px; padding: 4px 10px; font-weight: 700;"
            )
        else:
            self.lbl_pd_status_badge.setText("Teridentifikasi Unik")
            self.lbl_pd_status_badge.setStyleSheet(
                "background-color: #E6F8EF; color: #065F46; border-radius: 8px; padding: 4px 10px; font-weight: 700;"
            )
        self.lbl_pd_url.setText(prod.product_url or "Tidak Tersedia")
        self.lbl_pd_videos.setText(str(prod.video_count))
        self.lbl_pd_views.setText(_fmt_int(prod.total_views))
        self.lbl_pd_avg.setText(_fmt_int(int(round(prod.average_views))))
        self.lbl_pd_contrib.setText(f"{prod.view_contribution_pct:.2f}%")
        self.btn_pd_open_url.setEnabled(bool(prod.product_url))

        self.tbl_pd_videos.setRowCount(0)
        for vid in prod.videos:
            r = self.tbl_pd_videos.rowCount()
            self.tbl_pd_videos.insertRow(r)
            vals = [
                vid.video_url,
                vid.upload_date or "Tidak Tersedia",
                _fmt_int(vid.views),
                vid.creator_username,
            ]
            for c, val in enumerate(vals):
                self.tbl_pd_videos.setItem(r, c, QTableWidgetItem(val))

    def _on_copy_product_name(self) -> None:
        name = self.lbl_pd_name.text().strip()
        if name and name != "-":
            cb = QApplication.clipboard()
            if cb:
                cb.setText(name)

    def _on_open_product_url(self) -> None:
        url = self.lbl_pd_url.text().strip()
        if url and url.startswith("http"):
            webbrowser.open(url)

    def _on_export_current_analytics(self) -> None:
        if self.current_analytics_job_id is None:
            QMessageBox.warning(
                self, "Belum Ada Job", "Pilih pekerjaan scraping terlebih dahulu."
            )
            return
        self._export_job_dialog(self.current_analytics_job_id)

    def _refresh_video_db_table(self) -> None:
        videos = self.db.list_videos()
        q = self.inp_vid_search.text().strip().lower()
        flt = self.cmb_vid_filter.currentData() or "all"
        srt = self.cmb_vid_sort.currentData() or "views_desc"

        filtered = []
        for v in videos:
            if flt == "with_product" and not v.has_product:
                continue
            if flt == "without_product" and v.has_product:
                continue
            if q and not (
                q in (v.video_id or "").lower()
                or q in v.video_url.lower()
                or q in v.creator_username.lower()
            ):
                continue
            filtered.append(v)

        filtered.sort(
            key=lambda x: (x.views or 0),
            reverse=(srt == "views_desc"),
        )

        self.tbl_video_db.setRowCount(0)
        for v in filtered:
            r = self.tbl_video_db.rowCount()
            self.tbl_video_db.insertRow(r)
            vals = [
                f"#{v.job_id}" if v.job_id else "-",
                v.creator_username,
                v.video_id or "Tidak Tersedia",
                v.video_url,
                v.upload_date or "Tidak Tersedia",
                _fmt_int(v.views),
                str(v.product_count),
            ]
            for c, val in enumerate(vals):
                self.tbl_video_db.setItem(r, c, QTableWidgetItem(val))

    def _on_open_selected_video_url(self) -> None:
        if self.tbl_video_db.rowCount() == 0:
            if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                QMessageBox.information(
                    self,
                    "Daftar Video Kosong",
                    "Belum ada data video untuk dibuka.",
                )
            return
        row_idx = self.tbl_video_db.currentRow()
        if row_idx < 0:
            row_idx = 0
            self.tbl_video_db.selectRow(0)
        url_item = self.tbl_video_db.item(row_idx, 3)
        creator_item = self.tbl_video_db.item(row_idx, 1)
        if url_item and url_item.text().startswith("http"):
            raw_url = url_item.text().strip()
            last_seg = raw_url.rstrip("/").split("/")[-1]
            if not last_seg.isdigit() and creator_item:
                creator_handle = creator_item.text().strip().lstrip("@")
                webbrowser.open(f"https://www.tiktok.com/@{creator_handle}")
            else:
                webbrowser.open(raw_url)

    def _refresh_history_table(self) -> None:
        jobs = self.db.list_jobs()
        self.tbl_history.setRowCount(0)
        for j in jobs:
            r = self.tbl_history.rowCount()
            self.tbl_history.insertRow(r)
            vals = [
                str(j.id),
                j.creator_username,
                f"{j.period_days} Days",
                j.started_at,
                str(j.processed_videos),
                str(j.total_products),
                j.job_status,
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, j.id)
                if c == 6:
                    _style_status_item(item, val)
                self.tbl_history.setItem(r, c, item)

    def _selected_history_job_id(self) -> Optional[int]:
        rows = self.tbl_history.selectionModel().selectedRows()
        if rows:
            row_idx = rows[0].row()
        else:
            row_idx = self.tbl_history.currentRow()
        if row_idx < 0:
            jobs = self.db.list_jobs()
            return jobs[0].id if jobs else None
        item = self.tbl_history.item(row_idx, 0)
        return int(item.data(Qt.UserRole)) if item else None

    def _on_history_selection_changed(self) -> None:
        job_id = self._selected_history_job_id()
        self.txt_history_logs.clear()
        if job_id is None:
            return
        for log in self.db.list_logs(job_id):
            vid_tag = f" [video:{log.video_id}]" if log.video_id else ""
            self.txt_history_logs.appendPlainText(
                f"{log.created_at} [{log.log_level}] ({log.action}){vid_tag}: {log.message}"
            )

    def _on_history_open_result(self) -> None:
        job_id = self._selected_history_job_id()
        if job_id is None:
            return
        self.current_analytics_job_id = job_id
        self.analytics_tabs.setCurrentIndex(0)
        self.products_sub_stack.setCurrentIndex(0)
        self.navigate_to(self.PAGE_ANALYTICS)

    def _on_history_export_excel(self) -> None:
        job_id = self._selected_history_job_id()
        if job_id is None:
            return
        self._export_job_dialog(job_id)

    def _export_job_dialog(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if not job:
            return
        DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        default_name = build_export_filename(
            job.creator_username, job.period_days, date.today()
        )
        default_path = str(DEFAULT_EXPORT_DIR / default_name)

        chosen_path, _ = QFileDialog.getSaveFileName(
            self,
            "Simpan Laporan Excel (.xlsx)",
            default_path,
            "Excel Workbook (*.xlsx)",
        )
        if not chosen_path:
            return
        saved = self.exporter.export_job_to_excel(job_id, chosen_path)
        QMessageBox.information(
            self,
            "Export Excel Berhasil",
            f"Laporan Excel (4 Worksheet) berhasil disimpan di:\n{saved}",
        )

    def _on_history_resume_job(self) -> None:
        job_id = self._selected_history_job_id()
        if job_id is None:
            return
        self._launch_scraping_worker(resume_job_id=job_id)

    def _on_history_delete_job(self) -> None:
        job_id = self._selected_history_job_id()
        if job_id is None:
            return
        ans = QMessageBox.question(
            self,
            "Konfirmasi Hapus Riwayat",
            f"Apakah Anda yakin ingin menghapus riwayat Job #{job_id} beserta datanya?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        with self.db.connection() as conn:
            conn.execute("DELETE FROM scraping_jobs WHERE id = ?", (job_id,))
        if self.current_analytics_job_id == job_id:
            self.current_analytics_job_id = None
        self.refresh_all_views()


def run_desktop_app() -> int:
    """Entry point to launch the PySide6 desktop GUI."""
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()
