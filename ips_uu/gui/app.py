"""Desktop GUI entry point for iPS-UU."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ips_uu import __version__
from ips_uu.services.contents_research_service import contents_requirements
from ips_uu.services.dependency_setup_service import dependency_setup
from ips_uu.services.device_service import detect_target
from ips_uu.services.external_tools_service import scan_external_tools
from ips_uu.services.ios_device_viewer_service import load_device_viewer_snapshot, perform_device_action
from ips_uu.services.ipsw_service import compatibility_summary, parse_ipsw
from ips_uu.services.logging_service import configure_logging, get_log_dir
from ips_uu.services.palera1n_service import (
    build_manual_plan as build_palera1n_plan,
    check_requirements as check_palera1n_requirements,
    find_toolchain as find_palera1n_toolchain,
    inspect_device as inspect_palera1n_device,
    run_dry_run as run_palera1n_dry_run,
    run_rootless_version_check,
)
from ips_uu.services.restore_service import backend_inventory, dry_run_plan, execute_restore
from ips_uu.services.settings_service import AppSettings, load_settings, save_settings
from ips_uu.services.shsh_blob_service import inspect_blob as inspect_shsh_blob
from ips_uu.services.turdus_merula_service import (
    build_tethered_plan,
    check_requirements,
    find_toolchain,
    inspect_artifacts as inspect_turdus_artifacts,
    inspect_device as inspect_turdus_device,
    inspect_ipsw as inspect_turdus_ipsw,
    repair_permissions as repair_turdus_permissions,
    run_dry_run as run_turdus_dry_run,
)
from ips_uu.restore_research import CONTENTS_RESTORE_METHODS

try:
    from PySide6.QtCore import QMargins, QRectF, QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
    from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - exercised by users without GUI deps.
    raise SystemExit("PySide6 is required for the GUI. Install with: python3 -m pip install '.[gui]'") from exc


LOGGER = logging.getLogger("ips_uu.gui")


def app_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)
    return Path(__file__).resolve().parents[2]


def app_icon() -> QIcon:
    root = app_root()
    candidates = [
        root / "assets/icons/png/ips-uu-icon-main-1024.png",
        root / "assets/icons/ips-uu.icns",
    ]
    icon = QIcon()
    for path in candidates:
        if path.exists():
            icon.addFile(str(path))
    return icon


class LogEmitter(QObject):
    message = Signal(str, str, str)


class QtLogHandler(logging.Handler):
    def __init__(self, emitter: LogEmitter) -> None:
        super().__init__()
        self.emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        self.emitter.message.emit(stamp, record.levelname, record.getMessage())


class WorkerSignals(QObject):
    started = Signal(str)
    result = Signal(str, object)
    error = Signal(str, str)
    finished = Signal(str)


class TaskWorker(QRunnable):
    def __init__(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.name = name
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        self.signals.started.emit(self.name)
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            self.signals.error.emit(self.name, str(exc))
        else:
            self.signals.result.emit(self.name, result)
        finally:
            self.signals.finished.emit(self.name)


def clear_layout(layout: QVBoxLayout | QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def scrollable_page(page: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidget(page)
    return scroll


def pill(text: str, tone: str = "neutral") -> QLabel:
    colors = {
        "neutral": ("#edf2f7", "#273244"),
        "ok": ("#dcfce7", "#14532d"),
        "warn": ("#fef3c7", "#713f12"),
        "bad": ("#fee2e2", "#7f1d1d"),
        "info": ("#dbeafe", "#1e3a8a"),
    }
    bg, fg = colors.get(tone, colors["neutral"])
    label = QLabel(text)
    label.setObjectName("Pill")
    label.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:10px; padding:4px 9px; font-weight:600;")
    return label


def format_bytes(value: object) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "Unavailable"
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}"


def terminal_text(result: dict[str, Any]) -> str:
    command = " ".join(str(part) for part in result.get("command") or [])
    lines = [
        "$ " + command,
    ]
    stdout = str(result.get("stdout") or "").rstrip()
    stderr = str(result.get("stderr") or "").rstrip()
    if stdout:
        lines.append(stdout)
    if stderr:
        lines.append(stderr)
    lines.append(f"[exit {result.get('returncode')}]")
    lines.append("$ ")
    return "\n".join(lines)


class Card(QFrame):
    def __init__(self, title: str, value: str = "", detail: str = "") -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("CardTitle")
        self.title.setWordWrap(True)
        self.title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.value = QLabel(value)
        self.value.setObjectName("CardValue")
        self.value.setWordWrap(True)
        self.value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.detail = QLabel(detail)
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        self.detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)

    def set(self, value: str, detail: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)


class DevicePreviewWidget(QWidget):
    """Static, clean-room visual representation of the selected iOS device."""

    def __init__(self) -> None:
        super().__init__()
        self.device: dict[str, Any] | None = None
        self.setMinimumSize(230, 330)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_device(self, device: dict[str, Any] | None) -> None:
        self.device = device
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = self.width()
        height = self.height()
        dark_text = QColor("#172033")
        muted = QColor("#667085")
        accent = QColor("#2563eb")
        ok = QColor("#16a34a")
        warn = QColor("#d97706")
        error = QColor("#dc2626")
        bg = QColor("#eef2f7")
        painter.fillRect(self.rect(), QColor("#f8fafc"))

        device = self.device or {}
        product = str(device.get("product_type") or "")
        is_tablet = product.startswith("iPad")
        frame_w = min(width * (0.62 if is_tablet else 0.42), 250 if is_tablet else 150)
        frame_h = min(height * 0.78, frame_w * (1.36 if is_tablet else 2.05))
        x = (width - frame_w) / 2
        y = max(14, (height - frame_h) / 2 - 8)
        radius = 24 if is_tablet else 30

        shadow = QRectF(x + 8, y + 10, frame_w, frame_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 28))
        painter.drawRoundedRect(shadow, radius, radius)

        frame = QRectF(x, y, frame_w, frame_h)
        painter.setBrush(QColor("#202938"))
        painter.drawRoundedRect(frame, radius, radius)
        painter.setBrush(QColor("#3b4658"))
        painter.drawRoundedRect(QRectF(x + 4, y + 4, frame_w - 8, frame_h - 8), radius - 4, radius - 4)

        screen_margin = 12 if is_tablet else 9
        screen = QRectF(x + screen_margin, y + screen_margin + (4 if is_tablet else 10), frame_w - screen_margin * 2, frame_h - screen_margin * 2 - (8 if is_tablet else 18))
        painter.setBrush(QColor("#f9fafb"))
        painter.drawRoundedRect(screen, 16, 16)

        if not device:
            status_color = muted
            title = "No Device"
            subtitle = "Connect by USB"
            model = "Waiting"
        else:
            badges = set(device.get("badges") or [])
            if "Error" in badges:
                status_color = error
            elif "Needs Trust" in badges or "Locked" in badges:
                status_color = warn
            else:
                status_color = ok
            title = str(device.get("device_name") or device.get("model_name") or "iOS Device")
            subtitle = str(device.get("product_version") or "iOS unknown")
            model = str(device.get("model_name") or device.get("product_type") or "Unknown model")

        top_color = QColor("#dbeafe") if status_color == ok else QColor("#fef3c7") if status_color == warn else QColor("#fee2e2") if status_color == error else QColor("#e5e7eb")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(top_color)
        painter.drawRoundedRect(screen.adjusted(6, 6, -6, -screen.height() * 0.52), 14, 14)
        painter.setBrush(QColor(255, 255, 255, 90))
        painter.drawEllipse(QRectF(screen.left() + screen.width() * 0.12, screen.top() + screen.height() * 0.10, screen.width() * 0.42, screen.width() * 0.42))
        painter.setBrush(QColor(255, 255, 255, 65))
        painter.drawEllipse(QRectF(screen.left() + screen.width() * 0.48, screen.top() + screen.height() * 0.04, screen.width() * 0.30, screen.width() * 0.30))

        painter.setBrush(status_color)
        painter.drawEllipse(QRectF(screen.left() + 14, screen.top() + 14, 12, 12))
        painter.setPen(QPen(dark_text))
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(QRectF(screen.left() + 34, screen.top() + 8, screen.width() - 46, 24), Qt.AlignmentFlag.AlignVCenter, title)

        painter.setPen(QPen(muted))
        small_font = QFont()
        small_font.setPointSize(9)
        painter.setFont(small_font)
        painter.drawText(QRectF(screen.left() + 16, screen.top() + 46, screen.width() - 32, 20), Qt.AlignmentFlag.AlignCenter, model)
        painter.drawText(QRectF(screen.left() + 16, screen.top() + 68, screen.width() - 32, 20), Qt.AlignmentFlag.AlignCenter, subtitle)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        card_top = screen.top() + screen.height() * 0.46
        for index, label in enumerate(("USB", "Trust", "Status")):
            row = QRectF(screen.left() + 16, card_top + index * 34, screen.width() - 32, 24)
            painter.setBrush(QColor("#edf2f7"))
            painter.drawRoundedRect(row, 8, 8)
            painter.setPen(QPen(muted))
            painter.drawText(row.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, label)
            painter.setPen(QPen(status_color if index == 2 else accent))
            value = "Connected" if device and index == 0 else "Required" if index == 1 and not device else ""
            if device and index == 1:
                value = str(device.get("pairing_status") or "Unknown")
            if device and index == 2:
                value = ", ".join(str(item) for item in (device.get("badges") or [])[:2])
            painter.drawText(row.adjusted(70, 0, -10, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, value)

        painter.setPen(QPen(QColor("#cbd5e1")))
        if is_tablet:
            painter.drawRoundedRect(QRectF(x + frame_w * 0.44, y + frame_h - 14, frame_w * 0.12, 4), 2, 2)
        else:
            painter.drawRoundedRect(QRectF(x + frame_w * 0.36, y + 10, frame_w * 0.28, 5), 3, 3)

        footer = QRectF(8, height - 44, width - 16, 34)
        painter.setPen(QPen(muted))
        painter.setFont(small_font)
        painter.drawText(
            footer,
            Qt.AlignmentFlag.AlignCenter,
            "Static device visual. Live preview requires a supported, user-authorized capture backend.",
        )


class MainWindow(QMainWindow):
    nav_items = [
        "Dashboard",
        "Device / Target",
        "iOS Device Viewer",
        "Firmware / IPSW",
        "Restore Research / Dry Run",
        "Restore Methods",
        "External Tools",
        "palera1n",
        "Turdus Merula",
        "Contents Requirements",
        "Logs",
        "Settings",
        "About",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.settings_data = load_settings()
        configure_logging(self.settings_data.verbose_logging)
        self.thread_pool = QThreadPool.globalInstance()
        self.active_workers: list[TaskWorker] = []
        self.log_emitter = LogEmitter()
        self.log_emitter.message.connect(self.append_log)
        logging.getLogger("ips_uu").addHandler(QtLogHandler(self.log_emitter))
        logging.getLogger("ips_uu").setLevel(logging.DEBUG if self.settings_data.verbose_logging else logging.INFO)

        self.device: dict[str, Any] | None = None
        self.device_viewer: dict[str, Any] | None = None
        self.ipsw: dict[str, Any] | None = None
        self.plan: dict[str, Any] | None = None
        self.inventory: dict[str, Any] | None = None
        self.contents_requirements: dict[str, Any] | None = None
        self.external_tools: dict[str, Any] | None = None
        self.shsh_inspection: dict[str, Any] | None = None
        self.palera1n_toolchain: dict[str, Any] | None = None
        self.palera1n_device: dict[str, Any] | None = None
        self.palera1n_preflight: dict[str, Any] | None = None
        self.palera1n_plan: dict[str, Any] | None = None
        self.palera1n_session_dir: str | None = None
        self.tm_toolchain: dict[str, Any] | None = None
        self.tm_device: dict[str, Any] | None = None
        self.tm_ipsw: dict[str, Any] | None = None
        self.tm_preflight: dict[str, Any] | None = None
        self.tm_plan: dict[str, Any] | None = None
        self.tm_session_dir: str | None = None
        self.tm_artifact_paths: dict[str, QLineEdit] = {}
        self.tm_artifacts: dict[str, Any] | None = None

        self.setWindowTitle("iPS-UU Restore Research")
        icon = app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(1180, 760)
        self.setMinimumSize(980, 660)
        self.apply_theme(self.settings_data.theme)
        self.build_ui()
        self.device_viewer_timer = QTimer(self)
        self.device_viewer_timer.setInterval(6000)
        self.device_viewer_timer.timeout.connect(self.auto_refresh_ios_device_viewer)
        self.load_initial_state()

    def build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(265)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("iPS-UU")
        title.setObjectName("AppTitle")
        subtitle = QLabel("Signed restore research console")
        subtitle.setObjectName("SidebarSubtitle")
        side_layout.addWidget(title)
        side_layout.addWidget(subtitle)
        side_layout.addSpacing(18)
        self.nav = QListWidget()
        self.nav.setObjectName("Navigation")
        self.nav.setWordWrap(True)
        self.nav.setUniformItemSizes(False)
        for item in self.nav_items:
            nav_item = QListWidgetItem(item)
            nav_item.setSizeHint(nav_item.sizeHint().expandedTo(nav_item.sizeHint().grownBy(QMargins(0, 6, 0, 6))))
            self.nav.addItem(nav_item)
        self.nav.currentRowChanged.connect(self.switch_page)
        side_layout.addWidget(self.nav, 1)
        self.status_pill = pill("Dry-run only", "ok")
        side_layout.addWidget(self.status_pill)
        layout.addWidget(sidebar)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(26, 22, 26, 22)
        main_layout.setSpacing(14)
        header = QHBoxLayout()
        head_text = QVBoxLayout()
        self.page_title = QLabel("Dashboard")
        self.page_title.setObjectName("PageTitle")
        self.page_subtitle = QLabel("Monitor target state, firmware metadata, and safe restore preflight.")
        self.page_subtitle.setObjectName("Muted")
        head_text.addWidget(self.page_title)
        head_text.addWidget(self.page_subtitle)
        header.addLayout(head_text, 1)
        self.global_progress = QProgressBar()
        self.global_progress.setFixedWidth(220)
        self.global_progress.setRange(0, 100)
        self.global_progress.setValue(0)
        header.addWidget(self.global_progress)
        main_layout.addLayout(header)

        self.pages = QStackedWidget()
        main_layout.addWidget(self.pages, 1)
        layout.addWidget(main, 1)

        self.pages.addWidget(scrollable_page(self.dashboard_page()))
        self.pages.addWidget(scrollable_page(self.device_page()))
        self.pages.addWidget(scrollable_page(self.ios_device_viewer_page()))
        self.pages.addWidget(scrollable_page(self.firmware_page()))
        self.pages.addWidget(scrollable_page(self.restore_page()))
        self.pages.addWidget(scrollable_page(self.methods_page()))
        self.pages.addWidget(scrollable_page(self.external_tools_page()))
        self.pages.addWidget(scrollable_page(self.palera1n_page()))
        self.pages.addWidget(scrollable_page(self.turdus_merula_page()))
        self.pages.addWidget(scrollable_page(self.contents_requirements_page()))
        self.pages.addWidget(self.logs_page())
        self.pages.addWidget(scrollable_page(self.settings_page()))
        self.pages.addWidget(scrollable_page(self.about_page()))
        self.nav.setCurrentRow(0)

    def dashboard_page(self) -> QWidget:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setSpacing(14)
        self.card_status = Card("Tool Status", "Ready", "Dry-run mode is enabled by default.")
        self.card_device = Card("Detected Device", "No device detected", "Use Device / Target to refresh.")
        self.card_firmware = Card("Selected Firmware", "No IPSW selected", "Use Firmware / IPSW to choose a bundle.")
        self.card_signing = Card("Signing / Compatibility", "Not checked", "Run a dry check after selecting firmware.")
        self.card_dryrun = Card("Latest Dry-Run Result", "No dry-run yet", "The restore plan will appear after preflight.")
        layout.addWidget(self.card_status, 0, 0)
        layout.addWidget(self.card_device, 0, 1)
        layout.addWidget(self.card_firmware, 1, 0)
        layout.addWidget(self.card_signing, 1, 1)
        layout.addWidget(self.card_dryrun, 2, 0, 1, 2)
        return page

    def device_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        actions = QHBoxLayout()
        self.refresh_device_btn = QPushButton("Refresh Device")
        self.refresh_device_btn.clicked.connect(self.refresh_device)
        actions.addWidget(self.refresh_device_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.device_empty = QLabel("No device detected")
        self.device_empty.setObjectName("EmptyState")
        layout.addWidget(self.device_empty)
        self.device_grid = QGridLayout()
        layout.addLayout(self.device_grid)
        layout.addStretch(1)
        return page

    def ios_device_viewer_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Clean-room USB device viewer using libimobiledevice-style tools only. "
            "It shows metadata, pairing status, and trust guidance without modifying the device."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        actions = QHBoxLayout()
        refresh = QPushButton("Refresh / Retry")
        refresh.clicked.connect(self.refresh_ios_device_viewer)
        copy = QPushButton("Copy Diagnostics")
        copy.clicked.connect(self.copy_ios_device_viewer_diagnostics)
        actions.addWidget(refresh)
        actions.addWidget(copy)
        actions.addStretch(1)
        layout.addLayout(actions)

        device_actions = QHBoxLayout()
        restart = QPushButton("Restart")
        restart.clicked.connect(lambda: self.run_ios_device_action("restart"))
        shutdown = QPushButton("Shutdown")
        shutdown.clicked.connect(lambda: self.run_ios_device_action("shutdown"))
        enter_recovery = QPushButton("Enter Recovery")
        enter_recovery.clicked.connect(lambda: self.run_ios_device_action("enter_recovery"))
        exit_recovery = QPushButton("Exit Recovery")
        exit_recovery.clicked.connect(lambda: self.run_ios_device_action("exit_recovery"))
        for button in (restart, shutdown, enter_recovery, exit_recovery):
            device_actions.addWidget(button)
        device_actions.addStretch(1)
        layout.addLayout(device_actions)

        body = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("Connected Devices"))
        self.ios_device_list = QListWidget()
        self.ios_device_list.currentRowChanged.connect(self.render_ios_device_viewer_detail)
        left.addWidget(self.ios_device_list, 1)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        self.ios_status_badges = QHBoxLayout()
        right.addLayout(self.ios_status_badges)
        self.ios_device_detail_grid = QGridLayout()
        right.addLayout(self.ios_device_detail_grid)
        troubleshooting = QFrame()
        troubleshooting.setObjectName("Panel")
        troubleshooting_layout = QVBoxLayout(troubleshooting)
        troubleshooting_layout.addWidget(QLabel("Troubleshooting"))
        self.ios_guidance = QLabel("Connect an iPhone or iPad over USB.")
        self.ios_guidance.setObjectName("Muted")
        self.ios_guidance.setWordWrap(True)
        troubleshooting_layout.addWidget(self.ios_guidance)
        right.addWidget(troubleshooting)
        screen = QFrame()
        screen.setObjectName("Panel")
        screen_layout = QVBoxLayout(screen)
        screen_layout.addWidget(QLabel("Device Visual"))
        self.ios_device_visual = DevicePreviewWidget()
        screen_layout.addWidget(self.ios_device_visual, 1)
        self.ios_screen_placeholder = QLabel("Live screen preview requires a supported, user-authorized capture backend.")
        self.ios_screen_placeholder.setObjectName("Muted")
        self.ios_screen_placeholder.setWordWrap(True)
        screen_layout.addWidget(self.ios_screen_placeholder)
        right.addWidget(screen)
        body.addLayout(right, 2)
        layout.addLayout(body, 1)

        self.ios_device_viewer_output = QTextEdit()
        self.ios_device_viewer_output.setReadOnly(True)
        self.ios_device_viewer_output.setPlaceholderText("Device viewer diagnostics JSON appears here.")
        layout.addWidget(self.ios_device_viewer_output, 1)
        return page

    def firmware_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        picker = QHBoxLayout()
        self.ipsw_path = QLineEdit(self.settings_data.last_ipsw)
        self.ipsw_path.setPlaceholderText("Choose an IPSW restore bundle")
        browse = QPushButton("Browse")
        browse.clicked.connect(self.pick_ipsw)
        parse = QPushButton("Parse IPSW")
        parse.clicked.connect(self.parse_selected_ipsw)
        picker.addWidget(self.ipsw_path, 1)
        picker.addWidget(browse)
        picker.addWidget(parse)
        layout.addLayout(picker)
        self.firmware_warning = QLabel("Select an IPSW to inspect BuildManifest.plist and Restore.plist.")
        self.firmware_warning.setObjectName("Muted")
        layout.addWidget(self.firmware_warning)
        self.firmware_grid = QGridLayout()
        layout.addLayout(self.firmware_grid)
        layout.addStretch(1)
        return page

    def restore_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        self.run_dry_btn = QPushButton("Run Dry Check")
        self.run_dry_btn.setObjectName("PrimaryButton")
        self.run_dry_btn.clicked.connect(self.run_dry_check)
        self.restore_action_combo = QComboBox()
        self.restore_action_combo.addItems(["restore", "update"])
        self.restore_action_combo.setToolTip("restore performs an erase install; update preserves data only when the backend and device state support it.")
        self.execute_btn = QPushButton("Execute Signed Restore")
        self.execute_btn.clicked.connect(self.execute_signed_restore)
        self.execute_btn.setEnabled(not self.settings_data.dry_run_only)
        self.execute_btn.setToolTip("Requires Dry-run only mode to be disabled in Settings and multiple confirmations.")
        top.addWidget(self.run_dry_btn)
        top.addWidget(self.restore_action_combo)
        top.addWidget(self.execute_btn)
        top.addStretch(1)
        layout.addLayout(top)
        self.step_layout = QVBoxLayout()
        layout.addLayout(self.step_layout)
        self.plan_view = QTextEdit()
        self.plan_view.setReadOnly(True)
        self.plan_view.setPlaceholderText("Dry-run JSON and safety refusals appear here.")
        layout.addWidget(self.plan_view, 1)
        return page

    def methods_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Observed restore and flash methods from the Contents bundle. "
            "Safe adapters are usable for signed restore planning/execution and firmware metadata; private or unsafe paths remain blocked."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        actions = QHBoxLayout()
        refresh = QPushButton("Refresh Method Catalog")
        refresh.clicked.connect(self.render_methods)
        actions.addWidget(refresh)
        actions.addStretch(1)
        layout.addLayout(actions)
        inspector = QFrame()
        inspector.setObjectName("Panel")
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.addWidget(QLabel("SHSH/APTicket Blob Inspector"))
        blob_row = QHBoxLayout()
        self.shsh_blob_path = QLineEdit()
        self.shsh_blob_path.setPlaceholderText("Choose local .shsh/.shsh2/.bshsh2 file")
        browse_blob = QPushButton("Browse Blob")
        browse_blob.clicked.connect(self.pick_shsh_blob)
        inspect_blob_btn = QPushButton("Inspect Blob")
        inspect_blob_btn.clicked.connect(self.run_shsh_blob_inspection)
        blob_row.addWidget(self.shsh_blob_path, 1)
        blob_row.addWidget(browse_blob)
        blob_row.addWidget(inspect_blob_btn)
        inspector_layout.addLayout(blob_row)
        compare_row = QHBoxLayout()
        self.shsh_expected_product = QLineEdit()
        self.shsh_expected_product.setPlaceholderText("Expected ProductType")
        self.shsh_expected_ecid = QLineEdit()
        self.shsh_expected_ecid.setPlaceholderText("Expected ECID")
        self.shsh_expected_apnonce = QLineEdit()
        self.shsh_expected_apnonce.setPlaceholderText("Expected APNonce")
        compare_row.addWidget(self.shsh_expected_product)
        compare_row.addWidget(self.shsh_expected_ecid)
        compare_row.addWidget(self.shsh_expected_apnonce)
        inspector_layout.addLayout(compare_row)
        note = QLabel("Local structural inspection only. iPS-UU does not fetch, replay, submit, patch, or select blobs for restore.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        inspector_layout.addWidget(note)
        self.shsh_inspection_output = QTextEdit()
        self.shsh_inspection_output.setReadOnly(True)
        self.shsh_inspection_output.setPlaceholderText("SHSH/APTicket inspection JSON appears here.")
        inspector_layout.addWidget(self.shsh_inspection_output, 1)
        layout.addWidget(inspector, 1)
        self.methods_scroll = QScrollArea()
        self.methods_scroll.setWidgetResizable(True)
        self.methods_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.methods_scroll_content = QWidget()
        self.methods_grid = QVBoxLayout(self.methods_scroll_content)
        self.methods_grid.setSpacing(12)
        self.methods_grid.setContentsMargins(0, 0, 8, 0)
        self.methods_scroll.setWidget(self.methods_scroll_content)
        layout.addWidget(self.methods_scroll, 1)
        return page

    def external_tools_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Inventory optional external tools for research and troubleshooting. "
            "This page does not launch jailbreak, exploit, privilege-escalation, or device-modifying workflows."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        actions = QHBoxLayout()
        refresh = QPushButton("Refresh External Tools")
        refresh.clicked.connect(self.refresh_external_tools)
        export = QPushButton("Export Inventory")
        export.clicked.connect(self.export_external_tools)
        actions.addWidget(refresh)
        actions.addWidget(export)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.external_tools_grid = QGridLayout()
        layout.addLayout(self.external_tools_grid)
        documentation = QFrame()
        documentation.setObjectName("Panel")
        documentation_layout = QVBoxLayout(documentation)
        documentation_layout.addWidget(QLabel("External Prerequisites Documentation"))
        for text in (
            "palera1n may be placed at tools/palera1n for inventory only.",
            "iPS-UU records presence, version, permissions, signature information, hash values, and binary metadata.",
            "Any palera1n or jailbreak-related workflow must be read, understood, and performed outside iPS-UU.",
            "iPS-UU never provides one-click jailbreak actions or buttons that invoke palera1n commands.",
        ):
            item = QLabel(text)
            item.setObjectName("Muted")
            item.setWordWrap(True)
            documentation_layout.addWidget(item)
        layout.addWidget(documentation)
        self.external_tools_view = QTextEdit()
        self.external_tools_view.setReadOnly(True)
        self.external_tools_view.setPlaceholderText("External tool inventory JSON appears here.")
        layout.addWidget(self.external_tools_view, 1)
        return page

    def palera1n_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "palera1n is handled as an external, user-managed prerequisite. "
            "iPS-UU inventories the tool, shows static compatibility guidance, and records a documentation-only plan."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        actions = QHBoxLayout()
        refresh_tools = QPushButton("Refresh Tool")
        refresh_tools.clicked.connect(self.refresh_palera1n_toolchain)
        refresh_device = QPushButton("Refresh Device")
        refresh_device.clicked.connect(self.refresh_palera1n_device)
        preflight = QPushButton("Run Preflight")
        preflight.clicked.connect(self.run_palera1n_preflight)
        rootless = QPushButton("rootless")
        rootless.clicked.connect(self.run_palera1n_rootless_version)
        rootless.setToolTip("Runs only palera1n --version as a passive metadata check.")
        dry_run = QPushButton("Save Guidance Plan")
        dry_run.setObjectName("PrimaryButton")
        dry_run.clicked.connect(self.run_palera1n_dry_run)
        actions.addWidget(refresh_tools)
        actions.addWidget(refresh_device)
        actions.addWidget(preflight)
        actions.addWidget(rootless)
        actions.addWidget(dry_run)
        actions.addStretch(1)
        layout.addLayout(actions)

        guide = QFrame()
        guide.setObjectName("Panel")
        guide_layout = QVBoxLayout(guide)
        guide_layout.addWidget(QLabel("External palera1n Guidance"))
        for text in (
            "Read the iOS Guide palera1n instructions before doing anything outside iPS-UU.",
            "Compatible static guidance: A11 and earlier devices on iOS 15.0 and later, with A11 passcode/SEP caveats.",
            "USB-C to Lightning cables may cause DFU entry issues; USB-A to Lightning may be needed.",
            "On Apple Silicon Macs using USB-C, the guide notes the device may need to be unplugged and replugged after Checkmate appears.",
            "A9(X) and earlier devices may get stuck midway in pongoOS and may require rerunning the external command.",
            "iPS-UU does not run palera1n commands, enter DFU, jailbreak, or modify the device.",
        ):
            label = QLabel(text)
            label.setObjectName("Muted")
            label.setWordWrap(True)
            guide_layout.addWidget(label)
        layout.addWidget(guide)

        ack = QHBoxLayout()
        self.palera1n_caveat_ack = QCheckBox("I reviewed A11/passcode/SEP and platform-specific caveats.")
        self.palera1n_external_ack = QCheckBox("I understand any palera1n command must be run outside iPS-UU.")
        ack.addWidget(self.palera1n_caveat_ack, 1)
        ack.addWidget(self.palera1n_external_ack, 1)
        layout.addLayout(ack)

        self.palera1n_cards = QGridLayout()
        layout.addLayout(self.palera1n_cards)
        self.palera1n_preflight_layout = QVBoxLayout()
        layout.addLayout(self.palera1n_preflight_layout)
        self.palera1n_output = QTextEdit()
        self.palera1n_output.setObjectName("TerminalOutput")
        self.palera1n_output.setReadOnly(True)
        self.palera1n_output.setPlaceholderText("palera1n output, inventory, compatibility notes, and documentation-only plan appear here.")
        layout.addWidget(self.palera1n_output, 1)
        command_row = QHBoxLayout()
        self.palera1n_command_input = QLineEdit()
        self.palera1n_command_input.setPlaceholderText("Type help, version, -l, status, guide, clear")
        self.palera1n_command_input.returnPressed.connect(self.run_palera1n_terminal_command)
        run_command_btn = QPushButton("Run")
        run_command_btn.clicked.connect(self.run_palera1n_terminal_command)
        command_row.addWidget(QLabel("$"))
        command_row.addWidget(self.palera1n_command_input, 1)
        command_row.addWidget(run_command_btn)
        layout.addLayout(command_row)
        return page

    def turdus_merula_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Guided Turdus Merula workflow wrapper for tethered A9(X)/A10(X) restore research. "
            "iPS-UU treats pwnDFU/exploit work as a manual external prerequisite, then performs passive discovery, "
            "compatibility checks, file validation, and session logging."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        actions = QHBoxLayout()
        refresh_tools = QPushButton("Refresh Tools")
        refresh_tools.clicked.connect(self.refresh_turdus_tools)
        repair = QPushButton("Repair Permissions")
        repair.clicked.connect(self.repair_turdus_permissions)
        refresh_device = QPushButton("Refresh Device Mode")
        refresh_device.clicked.connect(self.refresh_turdus_device)
        open_tools = QPushButton("Open Tools Folder")
        open_tools.clicked.connect(self.open_turdus_tools_folder)
        actions.addWidget(refresh_tools)
        actions.addWidget(repair)
        actions.addWidget(refresh_device)
        actions.addWidget(open_tools)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.tm_status = QLabel("Ready")
        self.tm_status.setObjectName("Muted")
        self.tm_status.setWordWrap(True)
        layout.addWidget(self.tm_status)

        self.tm_cards = QGridLayout()
        layout.addLayout(self.tm_cards)

        prerequisite = QFrame()
        prerequisite.setObjectName("Panel")
        prerequisite_layout = QVBoxLayout(prerequisite)
        prerequisite_layout.addWidget(QLabel("Manual Prerequisite Checklist"))
        for text in (
            "1. Connect device",
            "2. Manually complete required external prerequisite outside this app",
            "3. Return to app",
            "4. App verifies device state",
            "5. Continue restore/preparation workflow",
        ):
            item = QLabel(text)
            item.setObjectName("Muted")
            item.setWordWrap(True)
            prerequisite_layout.addWidget(item)
        layout.addWidget(prerequisite)

        firmware = QHBoxLayout()
        self.tm_ipsw_path = QLineEdit()
        self.tm_ipsw_path.setPlaceholderText("Choose IPSW for Turdus Merula preflight")
        browse = QPushButton("Browse IPSW")
        browse.clicked.connect(self.pick_turdus_ipsw)
        parse = QPushButton("Parse IPSW")
        parse.clicked.connect(self.parse_turdus_ipsw)
        firmware.addWidget(self.tm_ipsw_path, 1)
        firmware.addWidget(browse)
        firmware.addWidget(parse)
        layout.addLayout(firmware)

        mode_row = QHBoxLayout()
        self.tm_mode_combo = QComboBox()
        self.tm_mode_combo.addItems(["Tethered restore preparation", "Untethered restore with blobs"])
        self.tm_mode_combo.setCurrentIndex(0)
        self.tm_mode_combo.currentIndexChanged.connect(self.update_turdus_controls)
        self.tm_tethered_ack = QCheckBox("I understand this is tethered and the device may require this computer to boot.")
        self.tm_data_loss_ack = QCheckBox("I understand this may erase my device.")
        mode_row.addWidget(QLabel("Mode"))
        mode_row.addWidget(self.tm_mode_combo)
        mode_row.addWidget(self.tm_tethered_ack, 1)
        mode_row.addWidget(self.tm_data_loss_ack, 1)
        layout.addLayout(mode_row)

        artifacts = QFrame()
        artifacts.setObjectName("Panel")
        artifacts_layout = QVBoxLayout(artifacts)
        artifacts_layout.addWidget(QLabel("Optional User-Supplied Artifacts"))
        for key, label, placeholder in (
            ("shsh_blob", "SHSH/APTicket blob", "Optional .shsh/.shsh2/.bshsh2 path"),
            ("baseband_blob", "Baseband blob", "Optional baseband-related blob path"),
            ("restore_manifest", "Restore manifest", "Optional manifest/plist path"),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            field = QLineEdit()
            field.setPlaceholderText(placeholder)
            browse_artifact = QPushButton("Browse")
            browse_artifact.clicked.connect(lambda _checked=False, name=key: self.pick_turdus_artifact(name))
            self.tm_artifact_paths[key] = field
            row.addWidget(field, 1)
            row.addWidget(browse_artifact)
            artifacts_layout.addLayout(row)
        note = QLabel("These paths are checked for existence only. iPS-UU does not parse, submit, patch, replay, or generate blobs.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        artifacts_layout.addWidget(note)
        layout.addWidget(artifacts)

        dfu = QLabel(
            "Manual prerequisite: complete any required DFU/recovery or other external state transition outside iPS-UU. "
            "Then return here and refresh device mode. The workflow will only continue when DFU or recovery is already detected."
        )
        dfu.setObjectName("Muted")
        dfu.setWordWrap(True)
        layout.addWidget(dfu)

        run_row = QHBoxLayout()
        preflight = QPushButton("Run Preflight")
        preflight.clicked.connect(self.run_turdus_preflight)
        dry_run = QPushButton("Run Preflight / Dry Run")
        dry_run.setObjectName("PrimaryButton")
        dry_run.clicked.connect(self.run_turdus_dry_run)
        self.tm_execute_btn = QPushButton("Execution Disabled")
        self.tm_execute_btn.setEnabled(False)
        copy = QPushButton("Copy Diagnostics")
        copy.clicked.connect(self.copy_turdus_diagnostics)
        export = QPushButton("Export Session Log")
        export.clicked.connect(self.export_turdus_session_log)
        run_row.addWidget(preflight)
        run_row.addWidget(dry_run)
        run_row.addWidget(self.tm_execute_btn)
        run_row.addWidget(copy)
        run_row.addWidget(export)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.tm_preflight_layout = QVBoxLayout()
        layout.addLayout(self.tm_preflight_layout)
        self.tm_output = QTextEdit()
        self.tm_output.setReadOnly(True)
        self.tm_output.setPlaceholderText("Manual prerequisite status, passive checks, and dry-run plan appear here.")
        layout.addWidget(self.tm_output, 1)
        return page

    def contents_requirements_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Requirements and safe Python implementation status derived from the local Contents bundle. "
            "Blocked restore capabilities are documented but not executable."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        actions = QHBoxLayout()
        refresh = QPushButton("Refresh Requirements")
        refresh.clicked.connect(self.refresh_contents_requirements)
        actions.addWidget(refresh)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.requirements_grid = QGridLayout()
        layout.addLayout(self.requirements_grid)
        self.requirements_view = QTextEdit()
        self.requirements_view.setReadOnly(True)
        self.requirements_view.setPlaceholderText("Contents requirements JSON appears here.")
        layout.addWidget(self.requirements_view, 1)
        return page

    def logs_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        actions = QHBoxLayout()
        clear = QPushButton("Clear Logs")
        clear.clicked.connect(self.clear_logs)
        export = QPushButton("Export Logs")
        export.clicked.connect(self.export_logs)
        open_folder = QPushButton("Open Log Folder")
        open_folder.clicked.connect(self.open_log_folder)
        actions.addWidget(clear)
        actions.addWidget(export)
        actions.addWidget(open_folder)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)
        return page

    def settings_page(self) -> QWidget:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setColumnStretch(1, 1)
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["auto", "cfgutil", "idevicerestore"])
        self.backend_combo.setCurrentText(self.settings_data.backend)
        self.cfgutil_path = QLineEdit(self.settings_data.cfgutil_path)
        self.idevicerestore_path = QLineEdit(self.settings_data.idevicerestore_path)
        self.verbose_toggle = QCheckBox("Verbose logging")
        self.verbose_toggle.setChecked(self.settings_data.verbose_logging)
        self.dry_only_toggle = QCheckBox("Dry-run only mode")
        self.dry_only_toggle.setChecked(self.settings_data.dry_run_only)
        self.dry_only_toggle.stateChanged.connect(self.update_execution_controls)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["system", "light", "dark"])
        self.theme_combo.setCurrentText(self.settings_data.theme)
        save = QPushButton("Save Settings")
        save.clicked.connect(self.save_settings)
        auto_detect = QPushButton("Auto Detect Tools")
        auto_detect.clicked.connect(self.auto_detect_tools)
        self.dependency_status = QLabel("Use auto detect to find supported restore tools without copying external binaries.")
        self.dependency_status.setObjectName("Muted")
        self.dependency_status.setWordWrap(True)
        rows = [
            ("Backend", self.backend_combo),
            ("cfgutil path", self.cfgutil_path),
            ("idevicerestore path", self.idevicerestore_path),
            ("Logging", self.verbose_toggle),
            ("Safety", self.dry_only_toggle),
            ("Theme", self.theme_combo),
        ]
        for row, (label, widget) in enumerate(rows):
            layout.addWidget(QLabel(label), row, 0)
            layout.addWidget(widget, row, 1)
        actions = QHBoxLayout()
        actions.addWidget(auto_detect)
        actions.addWidget(save)
        actions.addStretch(1)
        layout.addLayout(actions, len(rows), 1)
        layout.addWidget(self.dependency_status, len(rows) + 1, 0, 1, 2)
        layout.setRowStretch(len(rows) + 2, 1)
        return page

    def about_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        about = QTextEdit()
        about.setReadOnly(True)
        about.setHtml(
            f"""
            <h2>iPS-UU Restore Research</h2>
            <p>Version {__version__}</p>
            <p>iPS-UU helps inspect IPSW firmware and plan lawful Apple-signed restore dry-runs.</p>
            <p><b>Safety statement:</b> this tool does not support unsigned downgrades, signing bypasses,
            SEP/baseband bypasses, APNonce manipulation, exploit chains, pwned DFU, firmware patching,
            ticket patching, or private entitlement abuse.</p>
            <p>Credits: local research and Python implementation in this repository.</p>
            """
        )
        layout.addWidget(about)
        return page

    def load_initial_state(self) -> None:
        LOGGER.info("GUI started")
        self.render_methods()
        self.start_worker("inventory", backend_inventory)
        self.refresh_ios_device_viewer()
        self.device_viewer_timer.start()
        self.refresh_external_tools()
        self.refresh_palera1n_toolchain()
        self.refresh_contents_requirements()
        self.refresh_turdus_tools()
        if self.settings_data.last_ipsw:
            QTimer.singleShot(200, self.parse_selected_ipsw)

    def switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.page_title.setText(self.nav_items[index])
        subtitles = {
            0: "Monitor target state, firmware metadata, and safe restore preflight.",
            1: "Detect connected devices and restorable modes.",
            2: "View connected iOS/iPadOS device identity, trust, and pairing status.",
            3: "Inspect IPSW metadata before any restore planning.",
            4: "Run non-destructive restore research preflight.",
            5: "Review observed restore methods and their safety status.",
            6: "Inventory optional external tools without launching them.",
            7: "Document palera1n external prerequisites without launching them.",
            8: "Guided Turdus Merula preflight and dry-run workflow.",
            9: "Review extracted Contents requirements and implementation status.",
            10: "Review structured activity logs.",
            11: "Configure paths, logging, and dry-run policy.",
            12: "Purpose, safety, version, and credits.",
        }
        self.page_subtitle.setText(subtitles.get(index, ""))

    def start_worker(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        worker = TaskWorker(name, fn, *args, **kwargs)
        worker.setAutoDelete(False)
        worker.signals.started.connect(self.task_started)
        worker.signals.result.connect(self.task_result)
        worker.signals.error.connect(self.task_error)
        worker.signals.finished.connect(self.task_finished)
        self.active_workers.append(worker)
        self.thread_pool.start(worker)

    def task_started(self, name: str) -> None:
        self.global_progress.setRange(0, 0)
        LOGGER.info("%s started", name)

    def task_finished(self, name: str) -> None:
        self.global_progress.setRange(0, 100)
        self.global_progress.setValue(100)
        LOGGER.info("%s finished", name)
        self.active_workers = [worker for worker in self.active_workers if worker.name != name]
        self.update_execution_controls()

    def task_error(self, name: str, error: str) -> None:
        self.global_progress.setRange(0, 100)
        self.global_progress.setValue(0)
        LOGGER.error("%s failed: %s", name, error)
        QMessageBox.warning(self, "iPS-UU", f"{name} could not complete.\n\n{error}")

    def task_result(self, name: str, result: object) -> None:
        if name == "device":
            self.device = result if isinstance(result, dict) else None
            self.render_device()
        elif name == "ios_device_viewer":
            self.device_viewer = result if isinstance(result, dict) else None
            self.render_ios_device_viewer()
        elif name == "ios_device_action":
            if isinstance(result, dict):
                self.ios_device_viewer_output.setPlainText(json.dumps(result, indent=2, sort_keys=True))
                LOGGER.info("iOS device action %s completed success=%s", result.get("action"), result.get("succeeded"))
                self.refresh_ios_device_viewer()
        elif name == "ipsw":
            self.ipsw = result if isinstance(result, dict) else None
            self.render_firmware()
        elif name == "dry_run":
            self.plan = result if isinstance(result, dict) else None
            self.render_plan()
        elif name == "execute_restore":
            if isinstance(result, dict):
                self.render_execution_result(result)
        elif name == "inventory":
            self.inventory = result if isinstance(result, dict) else None
        elif name == "contents_requirements":
            self.contents_requirements = result if isinstance(result, dict) else None
            self.render_contents_requirements()
        elif name == "external_tools":
            self.external_tools = result if isinstance(result, dict) else None
            self.render_external_tools()
        elif name == "shsh_inspection":
            self.shsh_inspection = result if isinstance(result, dict) else None
            self.shsh_inspection_output.setPlainText(json.dumps(self.shsh_inspection, indent=2, sort_keys=True))
        elif name == "palera1n_toolchain":
            self.palera1n_toolchain = result if isinstance(result, dict) else None
            self.render_palera1n()
        elif name == "palera1n_device":
            self.palera1n_device = result if isinstance(result, dict) else None
            self.render_palera1n()
        elif name == "palera1n_rootless_version":
            if isinstance(result, dict):
                self.append_palera1n_terminal_text(terminal_text(result))
                LOGGER.info("palera1n rootless version check completed success=%s", result.get("succeeded"))
        elif name == "palera1n_dry_run":
            if isinstance(result, dict):
                self.palera1n_plan = result.get("plan") if isinstance(result.get("plan"), dict) else None
                self.palera1n_preflight = result.get("preflight") if isinstance(result.get("preflight"), dict) else self.palera1n_preflight
                self.palera1n_session_dir = result.get("session_dir")
            self.render_palera1n()
        elif name == "dependency_setup":
            if isinstance(result, dict):
                self.apply_dependency_setup(result)
        elif name == "tm_toolchain":
            self.tm_toolchain = result if isinstance(result, dict) else None
            self.render_turdus()
        elif name == "tm_repair_permissions":
            if isinstance(result, dict):
                self.tm_toolchain = result.get("toolchain") if isinstance(result.get("toolchain"), dict) else self.tm_toolchain
            self.render_turdus()
        elif name == "tm_device":
            self.tm_device = result if isinstance(result, dict) else None
            self.render_turdus()
        elif name == "tm_ipsw":
            self.tm_ipsw = result if isinstance(result, dict) else None
            self.render_turdus()
        elif name == "tm_preflight":
            self.tm_preflight = result if isinstance(result, dict) else None
            self.render_turdus()
        elif name == "tm_dry_run":
            if isinstance(result, dict):
                self.tm_plan = result.get("plan") if isinstance(result.get("plan"), dict) else None
                self.tm_preflight = result.get("preflight") if isinstance(result.get("preflight"), dict) else self.tm_preflight
                self.tm_session_dir = result.get("session_dir")
            self.render_turdus()
        self.update_dashboard()

    def refresh_device(self) -> None:
        self.start_worker("device", detect_target, "auto")

    def refresh_ios_device_viewer(self) -> None:
        if any(worker.name == "ios_device_viewer" for worker in self.active_workers):
            return
        self.start_worker("ios_device_viewer", load_device_viewer_snapshot)

    def auto_refresh_ios_device_viewer(self) -> None:
        if self.nav.currentRow() == self.nav_items.index("iOS Device Viewer"):
            self.refresh_ios_device_viewer()

    def copy_ios_device_viewer_diagnostics(self) -> None:
        payload = (self.device_viewer or {}).get("diagnostics") or self.device_viewer or {}
        QApplication.clipboard().setText(json.dumps(payload, indent=2, sort_keys=True))
        LOGGER.info("iOS device viewer diagnostics copied")

    def selected_ios_device_udid(self) -> str | None:
        devices = (self.device_viewer or {}).get("devices") or []
        index = self.ios_device_list.currentRow()
        if 0 <= index < len(devices):
            return devices[index].get("udid")
        return None

    def run_ios_device_action(self, action: str) -> None:
        labels = {
            "restart": "restart the selected device",
            "shutdown": "shut down the selected device",
            "enter_recovery": "put the selected device into recovery mode",
            "exit_recovery": "ask the recovery-mode device to exit recovery",
        }
        warning = QMessageBox.warning(
            self,
            "Confirm Device Action",
            f"This will {labels.get(action, action)} using public libimobiledevice/irecovery tooling. It does not restore, jailbreak, or bypass device security.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Cancel,
        )
        if warning != QMessageBox.StandardButton.Ok:
            return
        self.start_worker("ios_device_action", perform_device_action, action, self.selected_ios_device_udid())

    def refresh_contents_requirements(self) -> None:
        self.start_worker("contents_requirements", contents_requirements, Path("Contents"), CONTENTS_RESTORE_METHODS)

    def refresh_external_tools(self) -> None:
        self.start_worker("external_tools", scan_external_tools)

    def pick_shsh_blob(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose SHSH/APTicket Blob", str(Path.home()), "SHSH blobs (*.shsh *.shsh2 *.bshsh2);;All files (*)")
        if path:
            self.shsh_blob_path.setText(path)

    def run_shsh_blob_inspection(self) -> None:
        path = self.shsh_blob_path.text().strip()
        if not path:
            QMessageBox.information(self, "iPS-UU", "Choose a local SHSH/APTicket blob before inspecting.")
            return
        self.start_worker(
            "shsh_inspection",
            inspect_shsh_blob,
            path,
            self.shsh_expected_product.text().strip() or None,
            self.shsh_expected_ecid.text().strip() or None,
            self.shsh_expected_apnonce.text().strip() or None,
        )

    def refresh_palera1n_toolchain(self) -> None:
        self.start_worker("palera1n_toolchain", find_palera1n_toolchain)

    def refresh_palera1n_device(self) -> None:
        self.start_worker("palera1n_device", inspect_palera1n_device)

    def set_palera1n_terminal_text(self, text: str) -> None:
        self.palera1n_output.setPlainText(text)
        self.palera1n_output.moveCursor(QTextCursor.MoveOperation.End)

    def append_palera1n_terminal_text(self, text: str) -> None:
        current = self.palera1n_output.toPlainText().rstrip()
        merged = f"{current}\n{text}" if current else text
        self.set_palera1n_terminal_text(merged)

    def run_palera1n_terminal_command(self) -> None:
        command = self.palera1n_command_input.text().strip()
        self.palera1n_command_input.clear()
        if not command:
            return
        normalized = command.lower()
        if normalized in {"clear", "cls"}:
            self.palera1n_output.clear()
            return
        if normalized in {"help", "?"}:
            self.append_palera1n_terminal_text(
                "$ help\n"
                "Allowed commands:\n"
                "  help      Show this help.\n"
                "  clear     Clear this terminal pane.\n"
                "  version   Run tools/palera1n --version only.\n"
                "  rootless  Alias for version.\n"
                "  status    Show detected palera1n/device/preflight status.\n"
                "  guide     Show the external iOS Guide URL.\n"
                "Arbitrary shell commands and jailbreak actions are not supported."
            )
            return
        if normalized == "-l":
            self.append_palera1n_terminal_text(f"$ {command}")
            self.run_palera1n_rootless_version()
            return
        if normalized == "status":
            status = {
                "tool_found": bool((self.palera1n_toolchain or {}).get("found")),
                "device": self.palera1n_device or self.device,
                "preflight_passed": bool((self.palera1n_preflight or {}).get("passed")),
                "execution": "disabled",
            }
            self.append_palera1n_terminal_text("$ status\n" + json.dumps(status, indent=2, sort_keys=True))
            return
        if normalized == "guide":
            self.append_palera1n_terminal_text("$ guide\nhttps://ios.cfw.guide/installing-palera1n/#running-palera1n-1")
            return
        self.append_palera1n_terminal_text(
            f"$ {command}\n"
            "refused: this pane only supports passive iPS-UU commands. "
            "It does not run shell commands, palera1n jailbreak actions, or device-modifying workflows."
        )

    def run_palera1n_rootless_version(self) -> None:
        self.start_worker("palera1n_rootless_version", run_rootless_version_check)

    def run_palera1n_preflight(self) -> None:
        self.palera1n_preflight = check_palera1n_requirements(
            self.palera1n_device or self.device,
            caveat_ack=self.palera1n_caveat_ack.isChecked(),
            external_ack=self.palera1n_external_ack.isChecked(),
        )
        LOGGER.info("palera1n preflight completed passed=%s", self.palera1n_preflight.get("passed"))
        self.render_palera1n()

    def run_palera1n_dry_run(self) -> None:
        self.run_palera1n_preflight()
        if not self.palera1n_preflight or not self.palera1n_preflight.get("passed"):
            QMessageBox.warning(self, "iPS-UU", "Preflight must pass before saving a palera1n guidance plan.")
            return
        plan = build_palera1n_plan(self.palera1n_device or self.device or {}, self.palera1n_preflight)
        self.start_worker("palera1n_dry_run", run_palera1n_dry_run, plan, self.palera1n_preflight)

    def auto_detect_tools(self) -> None:
        self.start_worker("dependency_setup", dependency_setup, True)

    def export_external_tools(self) -> None:
        payload = self.external_tools or {}
        if not payload:
            QMessageBox.information(self, "iPS-UU", "Refresh External Tools before exporting inventory.")
            return
        target, _ = QFileDialog.getSaveFileName(self, "Export External Tool Inventory", str(Path.home() / "ips-uu-external-tools.json"), "JSON files (*.json);;All files (*)")
        if not target:
            return
        Path(target).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        LOGGER.info("external tool inventory exported to %s", target)

    def refresh_turdus_tools(self) -> None:
        self.start_worker("tm_toolchain", find_toolchain)

    def repair_turdus_permissions(self) -> None:
        self.start_worker("tm_repair_permissions", repair_turdus_permissions)

    def refresh_turdus_device(self) -> None:
        self.start_worker("tm_device", inspect_turdus_device)

    def pick_turdus_ipsw(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose IPSW", str(Path.home()), "IPSW firmware (*.ipsw);;All files (*)")
        if path:
            self.tm_ipsw_path.setText(path)
            self.parse_turdus_ipsw()

    def parse_turdus_ipsw(self) -> None:
        path = self.tm_ipsw_path.text().strip()
        if not path:
            QMessageBox.information(self, "iPS-UU", "Choose an IPSW before parsing.")
            return
        product = (self.tm_device or self.device or {}).get("product_type")
        self.start_worker("tm_ipsw", inspect_turdus_ipsw, path, product)

    def turdus_artifacts(self) -> dict[str, Any]:
        paths = {name: field.text().strip() or None for name, field in self.tm_artifact_paths.items()}
        self.tm_artifacts = inspect_turdus_artifacts(paths)
        return self.tm_artifacts

    def pick_turdus_artifact(self, name: str) -> None:
        target = self.tm_artifact_paths.get(name)
        if target is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose Artifact", str(Path.home()), "Blob or plist files (*.shsh *.shsh2 *.bshsh2 *.plist);;All files (*)")
        if path:
            target.setText(path)
            self.turdus_artifacts()
            self.render_turdus()

    def run_turdus_preflight(self) -> None:
        artifacts = self.turdus_artifacts()
        self.tm_preflight = check_requirements(
            self.tm_device,
            self.tm_ipsw,
            self.tm_tethered_ack.isChecked(),
            self.tm_data_loss_ack.isChecked(),
            artifacts=artifacts,
        )
        LOGGER.info("waiting for device in required mode before Turdus Merula post-prerequisite workflow")
        self.render_turdus()

    def run_turdus_dry_run(self) -> None:
        self.run_turdus_preflight()
        if self.tm_mode_combo.currentIndex() != 0:
            QMessageBox.warning(self, "iPS-UU", "Untethered restore with blobs is not implemented in this wrapper.")
            return
        if not self.tm_preflight or not self.tm_preflight.get("passed"):
            QMessageBox.warning(self, "iPS-UU", "Preflight must pass before creating a manual-prerequisite dry-run plan.")
            return
        try:
            plan = build_tethered_plan(self.tm_device or {}, self.tm_ipsw or {}, artifacts=self.tm_artifacts)
        except Exception as exc:
            QMessageBox.warning(self, "iPS-UU", str(exc))
            return
        self.tm_status.setText("Manual-prerequisite dry-run plan saved to session logs.")
        self.start_worker("tm_dry_run", run_turdus_dry_run, plan, self.tm_preflight)

    def open_turdus_tools_folder(self) -> None:
        path = Path((self.tm_toolchain or {}).get("root") or "tools").resolve()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def copy_turdus_diagnostics(self) -> None:
        payload = {
            "toolchain": self.tm_toolchain,
            "device": self.tm_device,
            "ipsw": self.tm_ipsw,
            "artifacts": self.tm_artifacts,
            "preflight": self.tm_preflight,
            "plan": self.tm_plan,
            "session_dir": self.tm_session_dir,
        }
        QApplication.clipboard().setText(json.dumps(payload, indent=2, sort_keys=True))
        LOGGER.info("turdus merula diagnostics copied")

    def export_turdus_session_log(self) -> None:
        if self.tm_session_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.tm_session_dir))
            return
        target, _ = QFileDialog.getSaveFileName(self, "Export Turdus Merula Diagnostics", str(Path.home() / "turdus-merula-diagnostics.json"), "JSON files (*.json);;All files (*)")
        if not target:
            return
        payload = {
            "toolchain": self.tm_toolchain,
            "device": self.tm_device,
            "ipsw": self.tm_ipsw,
            "artifacts": self.tm_artifacts,
            "preflight": self.tm_preflight,
            "plan": self.tm_plan,
        }
        Path(target).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        LOGGER.info("turdus merula diagnostics exported to %s", target)

    def update_turdus_controls(self) -> None:
        if self.tm_mode_combo.currentIndex() != 0:
            self.tm_status.setText("Untethered restore with blobs is not implemented by this wrapper.")
        else:
            self.tm_status.setText("Waiting for device in required mode after manual external prerequisite.")

    def pick_ipsw(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose IPSW", str(Path.home()), "IPSW firmware (*.ipsw);;All files (*)")
        if path:
            self.ipsw_path.setText(path)
            self.parse_selected_ipsw()

    def parse_selected_ipsw(self) -> None:
        path = self.ipsw_path.text().strip()
        if not path:
            QMessageBox.information(self, "iPS-UU", "Choose an IPSW before parsing firmware metadata.")
            return
        product = (self.device or {}).get("product_type")
        self.start_worker("ipsw", parse_ipsw, path, product)

    def run_dry_check(self) -> None:
        path = self.ipsw_path.text().strip()
        if not path:
            QMessageBox.information(self, "iPS-UU", "Choose an IPSW before running a dry check.")
            return
        if not Path(path).exists():
            QMessageBox.warning(self, "iPS-UU", "The selected IPSW does not exist.")
            return
        backend = self.backend_combo.currentText()
        product = (self.device or {}).get("product_type")
        action = self.restore_action_combo.currentText()
        self.set_steps(
            [
                ("Device detected", "pending"),
                ("IPSW parsed", "pending"),
                ("Compatibility checked", "pending"),
                ("Signing checked if available", "pending"),
                ("Restore backend candidate found", "pending"),
                ("Risk and safety result", "pending"),
            ]
        )
        self.start_worker("dry_run", dry_run_plan, path, "auto", product, None, None, None, action, backend)

    def execute_signed_restore(self) -> None:
        if self.dry_only_toggle.isChecked():
            QMessageBox.information(self, "iPS-UU", "Disable Dry-run only mode in Settings before executing a restore.")
            return
        path = self.ipsw_path.text().strip()
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "iPS-UU", "Choose a valid IPSW before executing a restore.")
            return
        action = self.restore_action_combo.currentText()
        backend = self.backend_combo.currentText()
        product = (self.device or {}).get("product_type")
        first = QMessageBox.warning(
            self,
            "Confirm Restore",
            "This will run a real signed firmware restore/update through the selected backend. "
            "The device may be erased and backend validation failures are terminal.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Cancel,
        )
        if first != QMessageBox.StandardButton.Ok:
            return
        second = QMessageBox.warning(
            self,
            "Confirm Device Wipe Risk",
            "Confirm you understand this may wipe data and iPS-UU will not bypass Apple signing, APTicket, nonce, SEP, or baseband validation.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Cancel,
        )
        if second != QMessageBox.StandardButton.Ok:
            return
        self.execute_btn.setEnabled(False)
        self.set_steps(
            [
                ("Device detected", "pending"),
                ("IPSW parsed", "pending"),
                ("Compatibility checked", "pending"),
                ("Apple signing handled by backend", "pending"),
                ("Restore command running", "pending"),
                ("Backend result", "pending"),
            ]
        )
        LOGGER.warning("starting signed restore backend=%s action=%s ipsw=%s", backend, action, path)
        self.start_worker("execute_restore", execute_restore, path, "auto", product, None, None, None, action, backend)

    def render_ios_device_viewer(self) -> None:
        payload = self.device_viewer or {}
        devices = payload.get("devices") or []
        current_masked = None
        current = self.ios_device_list.currentItem()
        if current:
            current_masked = current.data(Qt.ItemDataRole.UserRole)
        self.ios_device_list.clear()
        for device in devices:
            label = f"{device.get('device_name') or 'iOS Device'} - {device.get('masked_udid') or 'unknown'}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, device.get("masked_udid"))
            self.ios_device_list.addItem(item)
            if current_masked and current_masked == device.get("masked_udid"):
                self.ios_device_list.setCurrentItem(item)
        if devices and self.ios_device_list.currentRow() < 0:
            self.ios_device_list.setCurrentRow(0)
        self.ios_guidance.setText("\n".join(payload.get("guidance") or ["Connect an iPhone or iPad over USB."]))
        screen = payload.get("screen") or {}
        self.ios_screen_placeholder.setText(str(screen.get("message") or "Live screen preview requires a supported, user-authorized capture backend."))
        self.ios_device_viewer_output.setPlainText(json.dumps(payload, indent=2, sort_keys=True))
        self.render_ios_device_viewer_detail()

    def render_ios_device_viewer_detail(self, _index: int | None = None) -> None:
        clear_layout(self.ios_status_badges)
        clear_layout(self.ios_device_detail_grid)
        devices = (self.device_viewer or {}).get("devices") or []
        index = self.ios_device_list.currentRow()
        device = devices[index] if 0 <= index < len(devices) else None
        self.ios_device_visual.set_device(device)
        if not device:
            for label in ("No Device", "Disconnected"):
                self.ios_status_badges.addWidget(pill(label, "neutral"))
            self.ios_device_detail_grid.addWidget(Card("Connection Status", "No device connected", "Connect an iPhone or iPad over USB."), 0, 0)
            return
        for badge in device.get("badges") or ["Connected"]:
            tone = {
                "Connected": "ok",
                "Paired": "ok",
                "Locked": "warn",
                "Needs Trust": "warn",
                "Unsupported": "bad",
                "Error": "bad",
            }.get(str(badge), "neutral")
            self.ios_status_badges.addWidget(pill(str(badge), tone))
        fields = [
            ("Device Name", device.get("device_name") or "Unknown", "User-visible name from ideviceinfo."),
            ("Model Name", device.get("model_name") or "Unknown", "Mapped from ProductType or exposed marketing name."),
            ("Serial Number", device.get("serial_number") or "Unavailable", "Requires trusted metadata access."),
            ("Logic Number", device.get("logic_number") or "Unavailable", "MLB/logic-board serial if exposed by the device."),
            ("Logic Board", device.get("logic_board") or "Unavailable", "Hardware model or board identifier."),
            ("ECID", device.get("ecid") or "Unavailable", "UniqueChipID from ideviceinfo when available."),
            ("UDID", device.get("masked_udid") or "Unknown", "Masked except last 6 characters in diagnostics."),
            ("Model ID", device.get("model_id") or "Unavailable", "ModelNumber/RegionInfo when available."),
            ("Product Type", device.get("product_type") or "Unknown", str(device.get("architecture") or "")),
            ("Firmware Version", device.get("firmware_version") or device.get("product_version") or "Unknown", str(device.get("build_version") or "")),
            ("Device Storage", format_bytes(device.get("disk_capacity_bytes")), f"Free: {format_bytes(device.get('disk_free_bytes'))}"),
            ("IMEI", device.get("imei") or "Unavailable", "Cellular devices only; requires trusted metadata access."),
            ("Wi-Fi Address", device.get("wifi_address") or "Unavailable", "Requires trusted metadata access."),
            ("Bluetooth Address", device.get("bluetooth_address") or "Unavailable", "Requires trusted metadata access."),
            ("Connection Status", device.get("connection_status") or "Unknown", "USB/libimobiledevice detection."),
            ("Pairing / Trust", device.get("pairing_status") or "Unknown", device.get("lock_status") or ""),
        ]
        errors = device.get("errors") or []
        if errors:
            fields.append(("Last Error", "Action Required", errors[-1]))
            self.ios_guidance.setText("Unlock the device and tap Trust This Computer.\n" + "\n".join(errors))
        for idx, (title, value, detail) in enumerate(fields):
            self.ios_device_detail_grid.addWidget(Card(title, str(value), str(detail)), idx // 2, idx % 2)

    def render_device(self) -> None:
        clear_layout(self.device_grid)
        if not self.device or self.device.get("error"):
            self.device_empty.setText("No device detected")
            self.device_empty.show()
            detail = self.device.get("error") if self.device else "No device metadata available."
            self.device_grid.addWidget(Card("Detection", "Unavailable", str(detail)), 0, 0)
            return
        self.device_empty.hide()
        fields = [
            ("Mode", self.device.get("current_mode")),
            ("ProductType", self.device.get("product_type")),
            ("ECID", self.device.get("ecid")),
            ("UDID", self.device.get("udid")),
            ("iOS Version", self.device.get("product_version")),
            ("Build", self.device.get("build_version")),
        ]
        for index, (label, value) in enumerate(fields):
            self.device_grid.addWidget(Card(label, str(value or "Unknown")), index // 2, index % 2)

    def render_firmware(self) -> None:
        clear_layout(self.firmware_grid)
        if not self.ipsw:
            self.firmware_warning.setText("No firmware metadata available.")
            return
        comp = compatibility_summary(self.device, self.ipsw)
        self.firmware_warning.setText(comp["message"])
        supported = ", ".join(self.ipsw.get("supported_product_types") or []) or "Unknown"
        fields = [
            ("ProductVersion", self.ipsw.get("product_version")),
            ("BuildVersion", self.ipsw.get("product_build_version")),
            ("BuildIdentities", self.ipsw.get("build_identity_count")),
            ("Restore.plist", "Present" if self.ipsw.get("restore_plist_present") else "Not present"),
            ("Supported Devices", supported),
            ("Selected Identity", (self.ipsw.get("selected_identity") or {}).get("variant") or self.ipsw.get("match_error") or "Unknown"),
        ]
        for index, (label, value) in enumerate(fields):
            self.firmware_grid.addWidget(Card(label, str(value or "Unknown")), index // 2, index % 2)

    def render_plan(self) -> None:
        if not self.plan:
            return
        self.plan_view.setPlainText(json.dumps(self.plan, indent=2, sort_keys=True))
        warnings = self.plan.get("warnings") or []
        backend = (self.plan.get("candidate_restore_backend") or {}).get("selected")
        signing = (self.plan.get("signing_status") or {}).get("status")
        unsafe = bool(warnings) or backend in {None, "none"}
        self.set_steps(
            [
                ("Device detected", "warn" if (self.plan.get("device") or {}).get("error") else "ok"),
                ("IPSW parsed", "ok"),
                ("Compatibility checked", "ok" if (self.plan.get("compatibility") or {}).get("product_type_match") is not False else "bad"),
                ("Signing checked if available", "warn" if signing == "not_verified_in_local_dry_run" else "ok"),
                ("Restore backend candidate found", "ok" if backend not in {None, "none"} else "bad"),
                ("Risk and safety result", "warn" if unsafe else "ok"),
            ]
        )
        LOGGER.info("dry-run backend=%s warnings=%s", backend, len(warnings))

    def render_execution_result(self, result: dict[str, Any]) -> None:
        self.plan_view.setPlainText(json.dumps(result, indent=2, sort_keys=True))
        succeeded = bool(result.get("succeeded"))
        plan = result.get("plan") or {}
        self.plan = plan if isinstance(plan, dict) else self.plan
        self.set_steps(
            [
                ("Device detected", "ok" if not ((plan.get("device") or {}).get("error")) else "warn"),
                ("IPSW parsed", "ok"),
                ("Compatibility checked", "ok" if ((plan.get("compatibility") or {}).get("product_type_match") is not False) else "bad"),
                ("Apple signing handled by backend", "ok" if succeeded else "warn"),
                ("Restore command completed", "ok" if succeeded else "bad"),
                ("Backend result", "ok" if succeeded else "bad"),
            ]
        )
        if succeeded:
            LOGGER.info("signed restore completed successfully")
            QMessageBox.information(self, "iPS-UU", "Restore command completed successfully.")
        else:
            LOGGER.error("signed restore failed returncode=%s", result.get("returncode"))
            QMessageBox.warning(self, "iPS-UU", f"Restore command failed with return code {result.get('returncode')}. See logs/result output.")

    def render_methods(self) -> None:
        clear_layout(self.methods_grid)
        for method in CONTENTS_RESTORE_METHODS:
            state = str(method.get("status") or "inventory")
            online = str(method.get("online_or_offline") or "unknown")
            safe = bool(method.get("safe_to_execute_in_ips_uu"))
            tone = "ok" if safe else "warn"
            if "blocked" in state or "out_of_scope" in state:
                tone = "bad"
            action = method.get("ips_uu_action")
            detail = f"{state.replace('_', ' ')}. {method.get('refusal_or_guardrail')}"
            if action:
                detail = f"{detail} CLI: restore-research {action}"
            card = Card(
                str(method.get("name") or method.get("id")),
                online.replace("_", " ").title(),
                detail,
            )
            box = QVBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(6)
            box.addWidget(card)
            box.addWidget(pill("USABLE" if safe else "BLOCKED", tone))
            holder = QWidget()
            holder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            holder.setLayout(box)
            self.methods_grid.addWidget(holder)
        self.methods_grid.addStretch(1)

    def render_turdus(self) -> None:
        clear_layout(self.tm_cards)
        clear_layout(self.tm_preflight_layout)
        toolchain = self.tm_toolchain or {}
        device = self.tm_device or {}
        ipsw = self.tm_ipsw or {}
        tool_state = "Found" if toolchain.get("found") else "Missing"
        permission_state = "OK" if toolchain.get("executable_permissions_ok") else "Needs repair"
        chip = device.get("chip_class") or "unknown"
        mode = device.get("current_mode") or "unknown"
        firmware = f"{ipsw.get('product_version') or 'No IPSW'} {ipsw.get('product_build_version') or ''}".strip()
        compatible = ipsw.get("compatible_with_device")
        compatibility = "Unknown" if compatible is None else ("Compatible" if compatible else "Mismatch")
        artifacts = self.tm_artifacts or self.turdus_artifacts()
        invalid_artifacts = [item for item in artifacts.get("artifacts", []) if item.get("selected") and not item.get("exists")]
        artifact_state = "OK" if artifacts.get("valid") else "Invalid path"
        artifact_detail = "Selected artifact paths exist." if not invalid_artifacts else ", ".join(str(item.get("name")) for item in invalid_artifacts)
        cards = [
            Card("Toolchain", tool_state, permission_state),
            Card("Device", str(device.get("product_type") or "No device"), f"{chip}, mode {mode}"),
            Card("Firmware", firmware, compatibility),
            Card("Workflow", self.tm_mode_combo.currentText(), "External prerequisite must already be complete before continuing."),
            Card("Artifacts", artifact_state, artifact_detail),
            Card("Activation Risk", "Warning" if ipsw.get("activation_baseband_warning") else "None detected", ipsw.get("activation_baseband_warning_text") or "No iOS 10 cellular A10X/iPhone 7 warning detected."),
            Card("Execution", "Disabled", "iPS-UU does not launch pwnDFU, exploit, or Turdus Merula commands."),
        ]
        for index, card in enumerate(cards):
            self.tm_cards.addWidget(card, index // 2, index % 2)

        preflight = self.tm_preflight or check_requirements(
            self.tm_device,
            self.tm_ipsw,
            self.tm_tethered_ack.isChecked(),
            self.tm_data_loss_ack.isChecked(),
            artifacts=artifacts,
        )
        for item in preflight.get("checks", []):
            state = "ok" if item.get("passed") else "bad"
            row = QHBoxLayout()
            row.addWidget(pill("PASS" if item.get("passed") else "FAIL", state))
            label = QLabel(f"{item.get('label')}: {item.get('detail') or ''}")
            label.setWordWrap(True)
            row.addWidget(label, 1)
            holder = QWidget()
            holder.setLayout(row)
            self.tm_preflight_layout.addWidget(holder)

        output = {
            "toolchain": self.tm_toolchain,
            "device": self.tm_device,
            "ipsw": self.tm_ipsw,
            "artifacts": self.tm_artifacts,
            "preflight": self.tm_preflight,
            "plan": self.tm_plan,
            "session_dir": self.tm_session_dir,
        }
        self.tm_output.setPlainText(json.dumps(output, indent=2, sort_keys=True))
        self.tm_execute_btn.setEnabled(False)

    def render_external_tools(self) -> None:
        clear_layout(self.external_tools_grid)
        payload = self.external_tools or {}
        if not payload:
            self.external_tools_view.clear()
            return
        palera1n = ((payload.get("tools") or {}).get("palera1n") or {})
        metadata = palera1n.get("metadata") or {}
        permissions = metadata.get("permissions") or {}
        signature = metadata.get("signature") or {}
        version = palera1n.get("version") or {}
        device = payload.get("device") or {}
        compatibility = device.get("compatibility_information") or {}
        core_tools = ((payload.get("tools") or {}).get("core") or [])
        core_status = ", ".join(f"{item.get('name')}:{item.get('status')}" for item in core_tools)
        cards = [
            Card("palera1n", str(palera1n.get("status") or "Missing"), str(metadata.get("path") or "tools/palera1n")),
            Card("Version Detected", "Yes" if version.get("detected") else "No", str(version.get("value") or version.get("method") or "Unavailable")),
            Card("Permissions", str(permissions.get("mode") or "Unknown"), "Executable" if permissions.get("executable") else "Not executable"),
            Card("Signature", str(signature.get("summary") or "Unavailable"), f"codesign return: {signature.get('returncode', 'n/a')}"),
            Card("SHA-256", str(metadata.get("sha256") or "Unavailable")[:32], "Full hash is included in the JSON diagnostics."),
            Card("Compatibility Information", str(compatibility.get("palera1n_static_compatibility") or "Unknown"), str(compatibility.get("note") or "")),
            Card("Detected Device", str(device.get("product_type") or "No device"), f"{device.get('architecture') or 'unknown'}, iOS {device.get('product_version') or 'unknown'}"),
            Card("Other Detected Tools", core_status or "None", "Shown for inventory context only."),
            Card("Documentation", "Inventory Only", "No palera1n jailbreak, exploit, or device-modifying actions are exposed."),
        ]
        for index, card in enumerate(cards):
            self.external_tools_grid.addWidget(card, index // 3, index % 3)
        self.external_tools_view.setPlainText(json.dumps(payload, indent=2, sort_keys=True))

    def render_palera1n(self) -> None:
        clear_layout(self.palera1n_cards)
        clear_layout(self.palera1n_preflight_layout)
        toolchain = self.palera1n_toolchain or find_palera1n_toolchain()
        device = self.palera1n_device or self.device or {}
        preflight = self.palera1n_preflight or check_palera1n_requirements(
            device,
            caveat_ack=self.palera1n_caveat_ack.isChecked(),
            external_ack=self.palera1n_external_ack.isChecked(),
        )
        tool = (toolchain.get("tool") or {})
        version = tool.get("version") or {}
        compatibility = preflight.get("compatibility") or {}
        cards = [
            Card("palera1n", "Installed" if toolchain.get("found") else "Missing", str(((tool.get("metadata") or {}).get("path")) or "tools/palera1n")),
            Card("Version", str(version.get("value") or "Not detected"), str(version.get("method") or "")),
            Card("Device", str(device.get("product_type") or "No device"), f"iOS {device.get('product_version') or 'unknown'}"),
            Card("Compatibility", str(compatibility.get("status") or "unknown"), "Static A11-and-earlier iOS 15+ guidance only."),
            Card("Guide", "External", "https://ios.cfw.guide/installing-palera1n/#running-palera1n-1"),
            Card("Execution", "Disabled", "iPS-UU never launches palera1n or jailbreak workflows."),
        ]
        for index, card in enumerate(cards):
            self.palera1n_cards.addWidget(card, index // 3, index % 3)
        for item in preflight.get("checks", []):
            row = QHBoxLayout()
            row.addWidget(pill("PASS" if item.get("passed") else "FAIL", "ok" if item.get("passed") else "bad"))
            label = QLabel(f"{item.get('label')}: {item.get('detail') or ''}")
            label.setWordWrap(True)
            row.addWidget(label, 1)
            holder = QWidget()
            holder.setLayout(row)
            self.palera1n_preflight_layout.addWidget(holder)
        output = {
            "toolchain": self.palera1n_toolchain,
            "device": self.palera1n_device or self.device,
            "preflight": self.palera1n_preflight,
            "plan": self.palera1n_plan,
            "session_dir": self.palera1n_session_dir,
        }
        self.palera1n_output.setPlainText(json.dumps(output, indent=2, sort_keys=True))

    def render_contents_requirements(self) -> None:
        clear_layout(self.requirements_grid)
        if not self.contents_requirements:
            self.requirements_view.clear()
            return
        bundle = self.contents_requirements.get("bundle_info") or {}
        components = self.contents_requirements.get("bundled_components") or []
        implemented = self.contents_requirements.get("implemented_safe_features") or []
        blocked = self.contents_requirements.get("blocked_research_areas") or []
        requirements = self.contents_requirements.get("release_requirements") or []
        external = self.contents_requirements.get("external_tools") or []
        present_components = sum(1 for item in components if item.get("present"))
        required_names = ", ".join(
            str(item.get("name"))
            for item in requirements
            if item.get("required_for_release")
        )
        tool_state = ", ".join(
            f"{item.get('name')}:{'yes' if item.get('present') else 'no'}"
            for item in external
        )
        cards = [
            Card("Bundle", str(bundle.get("display_name") or "Unknown"), f"{bundle.get('bundle_identifier') or 'unknown'} {bundle.get('version') or ''}".strip()),
            Card("Bundled Components", f"{present_components}/{len(components)} present", "Local reverse-engineering inputs only."),
            Card("Safe Python Features", str(len(implemented)), "Implemented or inventory-only safe capabilities."),
            Card("Blocked Areas", str(len(blocked)), "Unsigned/offline/private restore behavior remains non-executable."),
            Card("Release Requirements", required_names or "None", "Core requirements kept in this release folder."),
            Card("External Tools", tool_state or "No tools found", "Optional backends and metadata helpers."),
        ]
        for index, card in enumerate(cards):
            self.requirements_grid.addWidget(card, index // 2, index % 2)
        self.requirements_view.setPlainText(json.dumps(self.contents_requirements, indent=2, sort_keys=True))

    def set_steps(self, steps: list[tuple[str, str]]) -> None:
        clear_layout(self.step_layout)
        for text, state in steps:
            row = QHBoxLayout()
            row.addWidget(pill(state.upper(), {"ok": "ok", "warn": "warn", "bad": "bad"}.get(state, "neutral")))
            label = QLabel(text)
            row.addWidget(label, 1)
            holder = QWidget()
            holder.setLayout(row)
            self.step_layout.addWidget(holder)

    def update_dashboard(self) -> None:
        mode = (self.device or {}).get("current_mode") or "No device detected"
        if self.device and self.device.get("error"):
            mode = "No device detected"
        self.card_device.set(str(mode), (self.device or {}).get("product_type") or "")
        if self.ipsw:
            self.card_firmware.set(
                str(self.ipsw.get("product_version") or "Unknown"),
                f"Build {self.ipsw.get('product_build_version') or 'unknown'}",
            )
        comp = compatibility_summary(self.device, self.ipsw)
        self.card_signing.set(comp["status"].replace("_", " ").title(), comp["message"])
        if self.plan:
            backend = (self.plan.get("candidate_restore_backend") or {}).get("selected") or "unknown"
            warnings = len(self.plan.get("warnings") or [])
            mode = "Execution enabled" if not self.dry_only_toggle.isChecked() else "Dry-run only"
            self.card_dryrun.set(f"Backend: {backend}", f"{warnings} warning(s). {mode}.")

    def append_log(self, stamp: str, level: str, message: str) -> None:
        self.log_view.append(f"[{stamp}] [{level}] {message}")

    def clear_logs(self) -> None:
        self.log_view.clear()
        LOGGER.info("visible logs cleared")

    def export_logs(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "Export Logs", str(Path.home() / "ips-uu-log.txt"), "Text files (*.txt);;All files (*)")
        if not target:
            return
        Path(target).write_text(self.log_view.toPlainText(), encoding="utf-8")
        LOGGER.info("logs exported to %s", target)

    def open_log_folder(self) -> None:
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))

    def save_settings(self) -> None:
        self.settings_data = AppSettings(
            backend=self.backend_combo.currentText(),
            cfgutil_path=self.cfgutil_path.text().strip(),
            idevicerestore_path=self.idevicerestore_path.text().strip(),
            verbose_logging=self.verbose_toggle.isChecked(),
            dry_run_only=self.dry_only_toggle.isChecked(),
            theme=self.theme_combo.currentText(),
            last_ipsw=self.ipsw_path.text().strip(),
        )
        save_settings(self.settings_data)
        self.apply_theme(self.settings_data.theme)
        self.update_execution_controls()
        LOGGER.info("settings saved")
        QMessageBox.information(self, "iPS-UU", "Settings saved.")

    def update_execution_controls(self) -> None:
        if hasattr(self, "execute_btn"):
            restore_running = any(worker.name == "execute_restore" for worker in self.active_workers)
            self.execute_btn.setEnabled((not self.dry_only_toggle.isChecked()) and not restore_running)
            self.execute_btn.setToolTip(
                "Run a real signed restore/update through the selected backend."
                if not self.dry_only_toggle.isChecked()
                else "Disable Dry-run only mode in Settings before executing a restore."
            )

    def apply_dependency_setup(self, payload: dict[str, Any]) -> None:
        backend = str(payload.get("selected_backend") or "auto")
        self.backend_combo.setCurrentText(backend)
        for item in payload.get("cfgutil_candidates") or []:
            if item.get("usable"):
                self.cfgutil_path.setText(str(item.get("path") or ""))
                break
        for item in payload.get("path_tools") or []:
            if item.get("name") == "idevicerestore" and item.get("usable"):
                self.idevicerestore_path.setText(str(item.get("path") or ""))
        available = "available" if payload.get("restore_execution_available") else "not available"
        self.dependency_status.setText(
            f"Supported restore backend: {backend} ({available}). "
            "External Apple/third-party binaries were not copied; detected tools are used in place."
        )
        LOGGER.info("dependency setup selected_backend=%s available=%s", backend, available)

    def apply_theme(self, theme: str) -> None:
        dark = theme == "dark"
        if theme == "system":
            dark = False
        if dark:
            bg, panel, text, muted, border, accent = "#111827", "#1f2937", "#f9fafb", "#9ca3af", "#374151", "#60a5fa"
            nav = "#0f172a"
        else:
            bg, panel, text, muted, border, accent = "#f6f8fb", "#ffffff", "#172033", "#667085", "#d8dee9", "#2563eb"
            nav = "#111827"
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{ background: {bg}; color: {text}; font-size: 13px; }}
            #Sidebar {{ background: {nav}; }}
            #AppTitle {{ color: #ffffff; font-size: 28px; font-weight: 800; }}
            #SidebarSubtitle {{ color: #cbd5e1; }}
            #PageTitle {{ font-size: 25px; font-weight: 750; color: {text}; }}
            #Muted {{ color: {muted}; }}
            #Card {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; }}
            #CardTitle {{ color: {muted}; font-size: 12px; font-weight: 700; text-transform: uppercase; }}
            #CardValue {{ color: {text}; font-size: 16px; font-weight: 700; }}
            #Navigation {{ background: transparent; border: none; color: #cbd5e1; outline: 0; }}
            #Navigation::item {{ padding: 10px 10px; border-radius: 6px; }}
            #Navigation::item:selected {{ background: #334155; color: #ffffff; }}
            QPushButton {{ background: {panel}; border: 1px solid {border}; border-radius: 6px; padding: 8px 12px; }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton:disabled {{ color: {muted}; background: {bg}; }}
            #PrimaryButton {{ background: {accent}; color: #ffffff; border-color: {accent}; font-weight: 700; }}
            QLineEdit, QTextEdit, QComboBox {{ background: {panel}; border: 1px solid {border}; border-radius: 6px; padding: 7px; }}
            #TerminalOutput {{ background: #000000; color: #f8f8f8; border: 1px solid #111111; border-radius: 6px; padding: 10px; font-family: Menlo, Monaco, Consolas, monospace; font-size: 12px; }}
            QProgressBar {{ border: 1px solid {border}; border-radius: 5px; text-align: center; background: {panel}; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
            #EmptyState {{ background: {panel}; border: 1px dashed {border}; border-radius: 8px; padding: 18px; color: {muted}; }}
            """
        )


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv or sys.argv)
    app.setApplicationName("iPS-UU")
    app.setOrganizationName("iPS-UU")
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
