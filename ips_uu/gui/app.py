"""Desktop GUI entry point for iPS-UU."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ips_uu import __version__
from ips_uu.services.contents_research_service import DEFAULT_CONTENTS_ROOT, contents_requirements
from ips_uu.services.dependency_setup_service import dependency_setup
from ips_uu.services.device_service import detect_target
from ips_uu.services.external_tools_service import scan_external_tools
from ips_uu.services.ideviceinstaller_service import build_install_plan
from ips_uu.services.idevicerestore_service import build_restore_plan
from ips_uu.services.ios_device_viewer_service import load_device_viewer_snapshot, perform_device_action
from ips_uu.services.ipsw_service import compatibility_summary, parse_ipsw
from ips_uu.services.logging_service import configure_logging, get_log_dir
from ips_uu.services.mock_tss_service import (
    RESULT_STATUSES,
    SIMULATION_BANNER,
    generate_mock_response,
    safe_mock_ticket_name,
    save_mock_ticket,
    simulated_restore_flash_plan,
)
from ips_uu.services.purple_restore_service import (
    PURPLE_SIMULATION_BANNER,
    PURPLE_STATES,
    build_purple_restore_session,
    request_mock_tatsu_ticket,
    run_purple_restore_simulation,
)
from ips_uu.services.restore_service import backend_inventory, dry_run_plan, execute_restore
from ips_uu.services.restore_options_service import analyze_restore_options
from ips_uu.services.settings_service import AppSettings, load_settings, save_settings
from ips_uu.services.shsh_blob_service import inspect_blob as inspect_shsh_blob
from ips_uu.services.tool_discovery import analyze_open_source_tool, discover_tools, run_diagnostics
from ips_uu.restore_research import CONTENTS_RESTORE_METHODS

try:
    from PySide6.QtCore import QMargins, QRectF, QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
    from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap, QTextCursor
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
    stream = Signal(str, str, str)
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


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "length": len(value),
            "hex_preview": value[:64].hex(),
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def json_text(value: Any) -> str:
    return json.dumps(json_safe(value), indent=2, sort_keys=True)


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


class DeviceHeaderPanel(QFrame):
    """Compact connected-device summary inspired by professional restore tools."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("DeviceHeader")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        top = QHBoxLayout()
        names = QVBoxLayout()
        self.title = QLabel("No device connected")
        self.title.setObjectName("DeviceHeaderTitle")
        self.subtitle = QLabel("Connect a device over USB and refresh.")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
        names.addWidget(self.title)
        names.addWidget(self.subtitle)
        top.addLayout(names, 1)
        self.badges = QHBoxLayout()
        top.addLayout(self.badges)
        layout.addLayout(top)

        self.metrics = QGridLayout()
        self.metrics.setHorizontalSpacing(12)
        self.metrics.setVerticalSpacing(8)
        layout.addLayout(self.metrics)

    def set_device(self, device: dict[str, Any] | None, fallback: dict[str, Any] | None = None) -> None:
        clear_layout(self.badges)
        clear_layout(self.metrics)
        data = device or fallback or {}
        if not data:
            self.title.setText("No device connected")
            self.subtitle.setText("Connect a device over USB and refresh.")
            self.badges.addWidget(pill("Disconnected", "neutral"))
            return

        model = str(data.get("model_name") or data.get("marketing_name") or data.get("product_type") or "iOS Device")
        device_name = str(data.get("device_name") or model)
        product = str(data.get("product_type") or "Unknown ProductType")
        version = str(data.get("firmware_version") or data.get("product_version") or "iOS unknown")
        build = str(data.get("build_version") or "")
        mode = str(data.get("current_mode") or data.get("connection_status") or "Unknown")
        self.title.setText(device_name)
        self.subtitle.setText(f"{model} | {product} | {version}{' (' + build + ')' if build else ''}")

        badges = list(data.get("badges") or [])
        if not badges:
            if data.get("error"):
                badges = ["Error"]
            elif data.get("product_type") or data.get("udid"):
                badges = ["Connected"]
            else:
                badges = [mode]
        for badge in badges[:5]:
            tone = {
                "Connected": "ok",
                "Paired": "ok",
                "Unlocked/Unknown": "ok",
                "normal": "ok",
                "Locked": "warn",
                "Needs Trust": "warn",
                "recovery": "warn",
                "dfu": "warn",
                "Unsupported": "bad",
                "Error": "bad",
            }.get(str(badge), "neutral")
            self.badges.addWidget(pill(str(badge), tone))

        rows = [
            ("Mode", mode),
            ("ECID", data.get("ecid") or "Unavailable"),
            ("UDID", data.get("masked_udid") or data.get("udid") or "Unavailable"),
            ("Serial", data.get("serial_number") or "Unavailable"),
            ("USB", data.get("usb_location") or data.get("location") or "Unavailable"),
            ("Board", data.get("hardware_model") or data.get("board_config") or data.get("model_identifier") or "Unavailable"),
            ("Chip", data.get("chip_id") or data.get("chip_family") or data.get("architecture") or "Unavailable"),
            ("Trust", data.get("pairing_status") or data.get("lock_status") or "Unknown"),
        ]
        for index, (label, value) in enumerate(rows):
            block = QFrame()
            block.setObjectName("MetricBlock")
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(10, 8, 10, 8)
            block_layout.setSpacing(2)
            key = QLabel(label)
            key.setObjectName("MetricLabel")
            val = QLabel(str(value))
            val.setObjectName("MetricValue")
            val.setWordWrap(True)
            block_layout.addWidget(key)
            block_layout.addWidget(val)
            self.metrics.addWidget(block, index // 3, index % 3)


class MainWindow(QMainWindow):
    nav_items = [
        "Dashboard",
        "Connected Device",
        "Firmware / IPSW",
        "Restore Options",
        "Restore",
        "Signing Simulator",
        "Purple Restore",
        "Downgrade",
        "Apps / Install",
        "Logs",
        "Tools",
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
        self.tools_inventory: dict[str, Any] | None = None
        self.tool_analysis: dict[str, Any] | None = None
        self.tool_diagnostics: dict[str, Any] | None = None
        self.downgrade_plan: dict[str, Any] | None = None
        self.install_plan: dict[str, Any] | None = None
        self.restore_options: dict[str, Any] | None = None
        self.mock_signing_response: dict[str, Any] | None = None
        self.purple_session: dict[str, Any] | None = None

        self.setWindowTitle("iPS-UU Device Servicing Workspace")
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
        subtitle = QLabel("Professional IPSW matters.")
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
        self.status_pill = pill("Wrapper mode", "ok")
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
        self.page_subtitle = QLabel("Professional firmware control for researchers, technicians, and device owners.")
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
        self.pages.addWidget(scrollable_page(self.firmware_page()))
        self.pages.addWidget(scrollable_page(self.restore_options_page()))
        self.pages.addWidget(scrollable_page(self.restore_page()))
        self.pages.addWidget(scrollable_page(self.signing_simulator_page()))
        self.pages.addWidget(scrollable_page(self.purple_restore_page()))
        self.pages.addWidget(scrollable_page(self.downgrade_page()))
        self.pages.addWidget(scrollable_page(self.apps_install_page()))
        self.pages.addWidget(self.logs_page())
        self.pages.addWidget(scrollable_page(self.tools_page()))
        self.pages.addWidget(scrollable_page(self.settings_page()))
        self.pages.addWidget(scrollable_page(self.about_page()))
        self.nav.setCurrentRow(0)

    def dashboard_page(self) -> QWidget:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setSpacing(14)
        self.card_status = Card("Tool Status", "Ready", "Bundled tools are external backends.")
        self.card_device = Card("Detected Device", "No device detected", "Use Connected Device to refresh.")
        self.card_firmware = Card("Selected Firmware", "No IPSW selected", "Use Firmware / IPSW to choose a bundle.")
        self.card_signing = Card("Compatibility", "Not checked", "Run compatibility or dry-run checks before execution.")
        self.card_dryrun = Card("Latest Command Plan", "No plan yet", "Backend command previews and logs appear in workflow tabs.")
        layout.addWidget(self.card_status, 0, 0)
        layout.addWidget(self.card_device, 0, 1)
        layout.addWidget(self.card_firmware, 1, 0)
        layout.addWidget(self.card_signing, 1, 1)
        layout.addWidget(self.card_dryrun, 2, 0, 1, 2)
        return page

    def device_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Connected-device metadata is read through public libimobiledevice-style utilities. "
            "Use this view to verify mode, ProductType, firmware, ECID, pairing, recovery state, and detailed hardware statistics before restore, recovery, or app-install workflows."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        actions = QHBoxLayout()
        self.refresh_device_btn = QPushButton("Refresh Device")
        self.refresh_device_btn.clicked.connect(self.refresh_connected_device)
        viewer = QPushButton("Refresh Detailed Viewer")
        viewer.clicked.connect(self.refresh_ios_device_viewer)
        actions.addWidget(self.refresh_device_btn)
        actions.addWidget(viewer)
        actions.addStretch(1)
        layout.addLayout(actions)
        device_actions = QHBoxLayout()
        restart = QPushButton("Restart")
        restart.clicked.connect(lambda: self.run_ios_device_action("restart"))
        shutdown = QPushButton("Shutdown")
        shutdown.clicked.connect(lambda: self.run_ios_device_action("shutdown"))
        enter_recovery = QPushButton("Recovery Mode")
        enter_recovery.clicked.connect(lambda: self.run_ios_device_action("enter_recovery"))
        exit_recovery = QPushButton("Exit Recovery")
        exit_recovery.clicked.connect(lambda: self.run_ios_device_action("exit_recovery"))
        dfu = QPushButton("DFU Instructions")
        dfu.clicked.connect(self.show_dfu_instructions)
        for button in (restart, shutdown, enter_recovery, exit_recovery, dfu):
            device_actions.addWidget(button)
        device_actions.addStretch(1)
        layout.addLayout(device_actions)
        self.device_empty = QLabel("No device detected")
        self.device_empty.setObjectName("EmptyState")
        layout.addWidget(self.device_empty)
        self.connected_device_header = DeviceHeaderPanel()
        layout.addWidget(self.connected_device_header)
        body = QHBoxLayout()
        details = QVBoxLayout()
        self.device_grid = QGridLayout()
        details.addLayout(self.device_grid)
        self.connected_verbose_grid = QGridLayout()
        details.addLayout(self.connected_verbose_grid)
        details.addWidget(QLabel("Device Diagnostics"))
        self.device_diagnostics_output = QTextEdit()
        self.device_diagnostics_output.setReadOnly(True)
        self.device_diagnostics_output.setPlaceholderText("Detection commands, stdout/stderr, USB entries, and recommended fixes appear here.")
        details.addWidget(self.device_diagnostics_output, 1)
        body.addLayout(details, 3)
        preview_panel = QFrame()
        preview_panel.setObjectName("Panel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.addWidget(QLabel("Screen Preview"))
        self.connected_screen_preview = QLabel("Refresh Detailed Viewer to capture the screen with idevicescreenshot.")
        self.connected_screen_preview.setObjectName("EmptyState")
        self.connected_screen_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.connected_screen_preview.setMinimumSize(240, 420)
        self.connected_screen_preview.setWordWrap(True)
        preview_layout.addWidget(self.connected_screen_preview, 1)
        self.connected_screen_status = QLabel("Preview requires a trusted normal-mode device and bundled idevicescreenshot.")
        self.connected_screen_status.setObjectName("Muted")
        self.connected_screen_status.setWordWrap(True)
        preview_layout.addWidget(self.connected_screen_status)
        preview_layout.addWidget(QLabel("Device Imagery"))
        self.connected_device_visual = DevicePreviewWidget()
        preview_layout.addWidget(self.connected_device_visual, 1)
        body.addWidget(preview_panel, 1)
        layout.addLayout(body)
        dfu_panel = QFrame()
        dfu_panel.setObjectName("Panel")
        dfu_layout = QVBoxLayout(dfu_panel)
        dfu_layout.addWidget(QLabel("DFU Mode Reference"))
        self.connected_dfu_text = QLabel(self.dfu_instruction_text())
        self.connected_dfu_text.setObjectName("Muted")
        self.connected_dfu_text.setWordWrap(True)
        dfu_layout.addWidget(self.connected_dfu_text)
        layout.addWidget(dfu_panel)
        self.connected_viewer_output = QTextEdit()
        self.connected_viewer_output.setReadOnly(True)
        self.connected_viewer_output.setPlaceholderText("Detailed connected-device diagnostics appear here.")
        layout.addWidget(self.connected_viewer_output, 1)
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
        self.ios_device_header = DeviceHeaderPanel()
        right.addWidget(self.ios_device_header)
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

    def restore_options_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "NovaCerts Restore Options shows standard signed restore paths for the connected device, "
            "checks selected IPSWs, and lists bundled external research backends without bypassing Apple signing."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        picker = QHBoxLayout()
        self.restore_options_ipsw_path = QLineEdit(self.settings_data.last_ipsw)
        self.restore_options_ipsw_path.setPlaceholderText("Optional IPSW for compatibility/signing check")
        browse = QPushButton("Browse IPSW")
        browse.clicked.connect(self.pick_restore_options_ipsw)
        refresh = QPushButton("Refresh Restore Options")
        refresh.setObjectName("PrimaryButton")
        refresh.clicked.connect(self.refresh_restore_options)
        picker.addWidget(self.restore_options_ipsw_path, 1)
        picker.addWidget(browse)
        picker.addWidget(refresh)
        layout.addLayout(picker)

        doc_picker = QHBoxLayout()
        self.restore_options_doc_path = QLineEdit("")
        self.restore_options_doc_path.setPlaceholderText("Optional Purple Restore .pr / PRKit / Classic PR2 / restore-options plist")
        browse_doc = QPushButton("Browse Restore Doc")
        browse_doc.clicked.connect(self.pick_restore_options_doc)
        doc_picker.addWidget(self.restore_options_doc_path, 1)
        doc_picker.addWidget(browse_doc)
        layout.addLayout(doc_picker)

        self.restore_options_status = QLabel("Select an IPSW or refresh to inspect current signed restore paths.")
        self.restore_options_status.setObjectName("Muted")
        self.restore_options_status.setWordWrap(True)
        layout.addWidget(self.restore_options_status)

        self.restore_options_device_grid = QGridLayout()
        layout.addLayout(self.restore_options_device_grid)
        self.restore_options_paths_grid = QGridLayout()
        layout.addLayout(self.restore_options_paths_grid)

        guidance = QFrame()
        guidance.setObjectName("Panel")
        guidance_layout = QVBoxLayout(guidance)
        guidance_layout.addWidget(QLabel("Restore Without Updating"))
        for text in (
            "If the installed iOS is still signed, reinstalling that same version may be possible.",
            "If the version is no longer signed, a standard restore will normally update to a currently signed version.",
            "User data reset may be possible through device settings, but this is not the same as reinstalling firmware.",
            "Recovery restore normally requires signed firmware.",
        ):
            label = QLabel(text)
            label.setObjectName("Muted")
            label.setWordWrap(True)
            guidance_layout.addWidget(label)
        layout.addWidget(guidance)

        self.restore_options_backend_grid = QGridLayout()
        layout.addLayout(self.restore_options_backend_grid)
        self.restore_options_output = QTextEdit()
        self.restore_options_output.setReadOnly(True)
        self.restore_options_output.setPlaceholderText("Restore option analysis, firmware status, command previews, dry-run plans, and session log paths appear here.")
        layout.addWidget(self.restore_options_output, 1)
        return page

    def restore_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Restore workflows are planned as explicit backend commands. iPS-UU does not pretend idevicerestore, cfgutil, or future tools are proprietary app logic."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        top = QHBoxLayout()
        self.run_dry_btn = QPushButton("Run Dry Check")
        self.run_dry_btn.setObjectName("PrimaryButton")
        self.run_dry_btn.clicked.connect(self.run_dry_check)
        self.restore_action_combo = QComboBox()
        self.restore_action_combo.addItems(["restore", "update"])
        self.restore_action_combo.setToolTip("restore performs an erase install; update preserves data only when the backend and device state support it.")
        self.execute_btn = QPushButton("Force Signed Flash")
        self.execute_btn.clicked.connect(self.execute_signed_restore)
        self.execute_btn.setEnabled(True)
        self.execute_btn.setToolTip("Run a real signed firmware restore/update through the first usable supported backend after confirmations.")
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

    def signing_simulator_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        banner = QLabel(SIMULATION_BANNER)
        banner.setObjectName("SimulationBanner")
        banner.setWordWrap(True)
        layout.addWidget(banner)

        self.signing_simulation_toggle = QCheckBox("Enable local signing simulation mode")
        self.signing_simulation_toggle.setToolTip("Required for mock signing responses. This never contacts Apple and never authorizes a real restore.")
        layout.addWidget(self.signing_simulation_toggle)

        form = QGridLayout()
        self.mock_device_model = QLineEdit((self.device or {}).get("product_type") or "iPhone10,5")
        self.mock_device_model.setPlaceholderText("Device model, e.g. iPhone10,5")
        self.mock_ecid = QLineEdit((self.device or {}).get("ecid") or "mock-ecid")
        self.mock_ecid.setPlaceholderText("ECID placeholder")
        self.mock_build = QLineEdit((self.ipsw or {}).get("product_build_version") or "20H240")
        self.mock_build.setPlaceholderText("Target build")
        self.mock_result_selector = QComboBox()
        self.mock_result_selector.addItems(["Approved", "Rejected", "Tethered Only", "Expired", "Network Error"])
        form.addWidget(QLabel("Device Model"), 0, 0)
        form.addWidget(self.mock_device_model, 0, 1)
        form.addWidget(QLabel("ECID"), 1, 0)
        form.addWidget(self.mock_ecid, 1, 1)
        form.addWidget(QLabel("Target Build"), 2, 0)
        form.addWidget(self.mock_build, 2, 1)
        form.addWidget(QLabel("Result"), 3, 0)
        form.addWidget(self.mock_result_selector, 3, 1)
        layout.addLayout(form)

        actions = QHBoxLayout()
        generate = QPushButton("Generate Mock Response")
        generate.setObjectName("PrimaryButton")
        generate.clicked.connect(self.generate_mock_signing_response)
        copy = QPushButton("Copy JSON")
        copy.clicked.connect(self.copy_mock_signing_json)
        save = QPushButton("Save Mock Ticket")
        save.clicked.connect(self.save_mock_signing_ticket)
        simulate_restore = QPushButton("Simulate Restore/Flash")
        simulate_restore.clicked.connect(self.simulate_mock_restore_flash)
        actions.addWidget(generate)
        actions.addWidget(copy)
        actions.addWidget(save)
        actions.addWidget(simulate_restore)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.mock_signing_output = QTextEdit()
        self.mock_signing_output.setReadOnly(True)
        self.mock_signing_output.setPlaceholderText("Mock signing JSON appears here. Files can only be saved as .mock.json.")
        layout.addWidget(self.mock_signing_output, 1)
        return page

    def purple_restore_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        banner = QLabel(PURPLE_SIMULATION_BANNER)
        banner.setObjectName("SimulationBanner")
        banner.setWordWrap(True)
        layout.addWidget(banner)

        intro = QLabel(
            "This emulator visually mirrors a Purple Restore / Tatsu-style workflow for internal UI testing only. "
            "It never contacts Apple services, never changes trust state, never creates valid restore artifacts, and never runs restore binaries."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QGridLayout()
        self.purple_product_type = QLineEdit((self.device or {}).get("product_type") or "iPhone10,5")
        self.purple_product_type.setPlaceholderText("ProductType, e.g. iPhone10,5")
        self.purple_ecid = QLineEdit((self.device or {}).get("ecid") or "mock-ecid")
        self.purple_ecid.setPlaceholderText("ECID placeholder")
        self.purple_mode = QComboBox()
        self.purple_mode.addItems(["normal", "recovery", "dfu"])
        self.purple_ipsw_path = QLineEdit(self.settings_data.last_ipsw)
        self.purple_ipsw_path.setPlaceholderText("Select IPSW for compatibility check")
        browse = QPushButton("Browse")
        browse.clicked.connect(self.pick_purple_ipsw)
        form.addWidget(QLabel("ProductType"), 0, 0)
        form.addWidget(self.purple_product_type, 0, 1)
        form.addWidget(QLabel("ECID"), 1, 0)
        form.addWidget(self.purple_ecid, 1, 1)
        form.addWidget(QLabel("Initial Mode"), 2, 0)
        form.addWidget(self.purple_mode, 2, 1)
        form.addWidget(QLabel("IPSW"), 3, 0)
        form.addWidget(self.purple_ipsw_path, 3, 1)
        form.addWidget(browse, 3, 2)
        layout.addLayout(form)

        actions = QHBoxLayout()
        prepare = QPushButton("Prepare Purple Restore")
        prepare.setObjectName("PrimaryButton")
        prepare.clicked.connect(self.prepare_purple_restore)
        ticket = QPushButton("Request Mock Tatsu Ticket")
        ticket.clicked.connect(self.request_purple_mock_ticket)
        proceed = QPushButton("Simulate Restore")
        proceed.clicked.connect(self.run_purple_restore_emulation)
        copy = QPushButton("Copy JSON")
        copy.clicked.connect(self.copy_purple_restore_json)
        actions.addWidget(prepare)
        actions.addWidget(ticket)
        actions.addWidget(proceed)
        actions.addWidget(copy)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.purple_state_grid = QGridLayout()
        for index, state in enumerate(PURPLE_STATES):
            state_label = QLabel(state)
            state_label.setObjectName("Muted")
            state_label.setWordWrap(True)
            self.purple_state_grid.addWidget(state_label, index // 3, index % 3)
        layout.addLayout(self.purple_state_grid)

        self.purple_output = QTextEdit()
        self.purple_output.setReadOnly(True)
        self.purple_output.setPlaceholderText("Purple Restore emulator session JSON appears here.")
        layout.addWidget(self.purple_output, 1)
        return page

    def downgrade_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Signed firmware downgrade planning for restore/recovery backends. "
            "Select a device and IPSW, check compatibility and signing expectations, then dry-run before any confirmed restore action."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        controls = QGridLayout()
        self.downgrade_device = QComboBox()
        self.downgrade_device.addItems(["Connected device / auto"])
        self.downgrade_ipsw_path = QLineEdit(self.settings_data.last_ipsw)
        self.downgrade_ipsw_path.setPlaceholderText("Choose IPSW for downgrade workflow")
        browse = QPushButton("Browse")
        browse.clicked.connect(self.pick_downgrade_ipsw)
        self.downgrade_backend = QComboBox()
        self.downgrade_backend.addItems(["idevicerestore", "cfgutil", "future signed-restore backend"])
        self.downgrade_status = QComboBox()
        self.downgrade_status.addItems(["standard signed restore", "signature unavailable", "unsupported target"])
        controls.addWidget(QLabel("Device"), 0, 0)
        controls.addWidget(self.downgrade_device, 0, 1)
        controls.addWidget(QLabel("IPSW"), 1, 0)
        controls.addWidget(self.downgrade_ipsw_path, 1, 1)
        controls.addWidget(browse, 1, 2)
        controls.addWidget(QLabel("Backend"), 2, 0)
        controls.addWidget(self.downgrade_backend, 2, 1)
        controls.addWidget(QLabel("Restore status"), 3, 0)
        controls.addWidget(self.downgrade_status, 3, 1)
        layout.addLayout(controls)

        risk_box = QFrame()
        risk_box.setObjectName("Panel")
        risk_layout = QVBoxLayout(risk_box)
        risk_layout.addWidget(QLabel("Practical Risks"))
        for text in (
            "This may erase data.",
            "This may update the device if the selected firmware is not signed.",
            "This may affect activation.",
            "This may void warranty.",
            "This may fail and require recovery.",
            "Unsigned firmware is normally refused by Apple restore services.",
        ):
            label = QLabel(text)
            label.setObjectName("Muted")
            label.setWordWrap(True)
            risk_layout.addWidget(label)
        layout.addWidget(risk_box)

        actions = QHBoxLayout()
        check = QPushButton("Check Compatibility")
        check.clicked.connect(self.check_downgrade_compatibility)
        dry = QPushButton("Dry Run")
        dry.setObjectName("PrimaryButton")
        dry.clicked.connect(self.run_downgrade_dry_run)
        execute = QPushButton("Execute After Confirmation")
        execute.clicked.connect(self.confirm_downgrade_execution)
        execute.setEnabled(False)
        actions.addWidget(check)
        actions.addWidget(dry)
        actions.addWidget(execute)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.downgrade_steps = QVBoxLayout()
        layout.addLayout(self.downgrade_steps)
        self.downgrade_output = QTextEdit()
        self.downgrade_output.setReadOnly(True)
        self.downgrade_output.setPlaceholderText("Compatibility, command plan, tethered status, and dry-run output appear here.")
        layout.addWidget(self.downgrade_output, 1)
        return page

    def jailbreak_boot_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Wrapper UX for supported bundled jailbreak and boot tools only. iPS-UU does not implement new exploits, rewrite exploit logic, or hide backend commands."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        controls = QGridLayout()
        self.jb_backend = QComboBox()
        self.jb_backend.addItems(["palera1n", "turdus merula", "future open-source backend"])
        self.jb_target = QLineEdit()
        self.jb_target.setPlaceholderText("Connected device / ProductType / UDID")
        self.jb_required_mode = QLineEdit("DFU or recovery, depending on backend")
        self.jb_command_preview = QLineEdit()
        self.jb_command_preview.setReadOnly(True)
        self.jb_command_preview.setText("tools/palera1n -l")
        controls.addWidget(QLabel("Target device"), 0, 0)
        controls.addWidget(self.jb_target, 0, 1)
        controls.addWidget(QLabel("Required mode"), 1, 0)
        controls.addWidget(self.jb_required_mode, 1, 1)
        controls.addWidget(QLabel("Backend selected"), 2, 0)
        controls.addWidget(self.jb_backend, 2, 1)
        controls.addWidget(QLabel("Command preview"), 3, 0)
        controls.addWidget(self.jb_command_preview, 3, 1)
        layout.addLayout(controls)
        actions = QHBoxLayout()
        refresh = QPushButton("Refresh Target")
        refresh.clicked.connect(self.prepare_jailbreak_boot_plan)
        dry = QPushButton("Build Command Plan")
        dry.setObjectName("PrimaryButton")
        dry.clicked.connect(self.prepare_jailbreak_boot_plan)
        run_palera1n = QPushButton("Run palera1n -l")
        run_palera1n.clicked.connect(self.run_palera1n_l_terminal)
        actions.addWidget(refresh)
        actions.addWidget(dry)
        actions.addWidget(run_palera1n)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.jb_output = QTextEdit()
        self.jb_output.setReadOnly(True)
        self.jb_output.setObjectName("TerminalOutput")
        self.jb_output.setPlaceholderText("Live output and success/failure summaries appear here when a supported backend command is run after confirmation.")
        layout.addWidget(self.jb_output, 1)
        return page

    def forsake_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Guided Forsake workflow. The app detects the device first, reads Forsake help/docs/source for reported support, builds dry-run command plans, and logs every session."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        step1 = QFrame()
        step1.setObjectName("Panel")
        step1_layout = QVBoxLayout(step1)
        step1_layout.addWidget(QLabel("Step 1: Detect Device"))
        actions = QHBoxLayout()
        refresh = QPushButton("Refresh Device")
        refresh.clicked.connect(self.refresh_forsake_device)
        diagnostics = QPushButton("Run Diagnostics")
        diagnostics.clicked.connect(self.refresh_forsake_device)
        copy = QPushButton("Copy Diagnostics")
        copy.clicked.connect(self.copy_forsake_diagnostics)
        actions.addWidget(refresh)
        actions.addWidget(diagnostics)
        actions.addWidget(copy)
        actions.addStretch(1)
        step1_layout.addLayout(actions)
        self.forsake_device_grid = QGridLayout()
        step1_layout.addLayout(self.forsake_device_grid)
        layout.addWidget(step1)

        step2 = QFrame()
        step2.setObjectName("Panel")
        step2_layout = QVBoxLayout(step2)
        step2_layout.addWidget(QLabel("Step 2: Select Firmware / Restore Files"))
        ipsw_row = QHBoxLayout()
        self.forsake_ipsw_path = QLineEdit(self.settings_data.last_ipsw)
        self.forsake_ipsw_path.setPlaceholderText("Optional IPSW")
        browse_ipsw = QPushButton("Browse IPSW")
        browse_ipsw.clicked.connect(self.pick_forsake_ipsw)
        parse_ipsw_btn = QPushButton("Parse IPSW")
        parse_ipsw_btn.clicked.connect(self.parse_forsake_ipsw)
        ipsw_row.addWidget(self.forsake_ipsw_path, 1)
        ipsw_row.addWidget(browse_ipsw)
        ipsw_row.addWidget(parse_ipsw_btn)
        step2_layout.addLayout(ipsw_row)
        files_row = QHBoxLayout()
        self.forsake_blob_path = QLineEdit()
        self.forsake_blob_path.setPlaceholderText("SHSH/blob path if Forsake requires it")
        self.forsake_boot_files_path = QLineEdit()
        self.forsake_boot_files_path.setPlaceholderText("Boot files folder/file if Forsake requires it")
        browse_blob = QPushButton("Browse Blob")
        browse_blob.clicked.connect(lambda: self.pick_forsake_file(self.forsake_blob_path, "Choose SHSH/Blob"))
        browse_boot = QPushButton("Browse Boot Files")
        browse_boot.clicked.connect(lambda: self.pick_forsake_file(self.forsake_boot_files_path, "Choose Boot File"))
        files_row.addWidget(self.forsake_blob_path, 1)
        files_row.addWidget(browse_blob)
        files_row.addWidget(self.forsake_boot_files_path, 1)
        files_row.addWidget(browse_boot)
        step2_layout.addLayout(files_row)
        self.forsake_ipsw_grid = QGridLayout()
        step2_layout.addLayout(self.forsake_ipsw_grid)
        layout.addWidget(step2)

        step3 = QFrame()
        step3.setObjectName("Panel")
        step3_layout = QVBoxLayout(step3)
        step3_layout.addWidget(QLabel("Step 3: Compatibility Check"))
        check = QPushButton("Check Compatibility")
        check.setObjectName("PrimaryButton")
        check.clicked.connect(self.run_forsake_compatibility)
        step3_layout.addWidget(check)
        self.forsake_compat_grid = QGridLayout()
        step3_layout.addLayout(self.forsake_compat_grid)
        layout.addWidget(step3)

        step4 = QFrame()
        step4.setObjectName("Panel")
        step4_layout = QVBoxLayout(step4)
        step4_layout.addWidget(QLabel("Step 4: Dry Run"))
        dry = QPushButton("Build Dry-Run Plan")
        dry.clicked.connect(self.run_forsake_dry_run)
        step4_layout.addWidget(dry)
        self.forsake_plan_output = QTextEdit()
        self.forsake_plan_output.setReadOnly(True)
        self.forsake_plan_output.setPlaceholderText("Forsake command plan, working directory, environment, and support metadata appear here.")
        step4_layout.addWidget(self.forsake_plan_output, 1)
        layout.addWidget(step4)

        step5 = QFrame()
        step5.setObjectName("Panel")
        step5_layout = QVBoxLayout(step5)
        step5_layout.addWidget(QLabel("Step 5: Execute"))
        confirm_row = QHBoxLayout()
        self.forsake_confirm_text = QLineEdit()
        self.forsake_confirm_text.setPlaceholderText("Type: I UNDERSTAND THIS MAY ERASE MY DEVICE")
        self.forsake_execute_btn = QPushButton("Execute Forsake")
        self.forsake_execute_btn.setEnabled(False)
        self.forsake_execute_btn.clicked.connect(self.execute_forsake_plan)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancel_forsake)
        confirm_row.addWidget(self.forsake_confirm_text, 1)
        confirm_row.addWidget(self.forsake_execute_btn)
        confirm_row.addWidget(cancel)
        step5_layout.addLayout(confirm_row)
        self.forsake_output = QTextEdit()
        self.forsake_output.setReadOnly(True)
        self.forsake_output.setObjectName("TerminalOutput")
        self.forsake_output.setPlaceholderText("Forsake stdout/stderr stream and session summary appear here.")
        step5_layout.addWidget(self.forsake_output, 1)
        layout.addWidget(step5)
        return page

    def apps_install_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel("App install workflows use ideviceinstaller as an external backend on trusted, user-controlled devices.")
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        row = QHBoxLayout()
        self.ipa_path = QLineEdit()
        self.ipa_path.setPlaceholderText("Choose IPA")
        browse = QPushButton("Browse")
        browse.clicked.connect(self.pick_ipa)
        plan = QPushButton("Build Install Plan")
        plan.setObjectName("PrimaryButton")
        plan.clicked.connect(self.build_app_install_plan)
        row.addWidget(self.ipa_path, 1)
        row.addWidget(browse)
        row.addWidget(plan)
        layout.addLayout(row)
        self.apps_install_output = QTextEdit()
        self.apps_install_output.setReadOnly(True)
        self.apps_install_output.setPlaceholderText("ideviceinstaller command plan appears here.")
        layout.addWidget(self.apps_install_output, 1)
        return page

    def tools_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary = QLabel(
            "Bundled tools are external open-source backends. iPS-UU discovers tools in tools/, shows versions/status, builds command plans, streams output, and exports logs."
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        actions = QHBoxLayout()
        refresh = QPushButton("Refresh Tools")
        refresh.clicked.connect(self.refresh_tool_inventory)
        diagnostics = QPushButton("Run Diagnostics")
        diagnostics.clicked.connect(self.run_selected_tool_diagnostics)
        inspect = QPushButton("Backend Inspector")
        inspect.clicked.connect(self.run_backend_inspector)
        open_folder = QPushButton("Open Tool Folder")
        open_folder.clicked.connect(self.open_selected_tool_folder)
        actions.addWidget(refresh)
        actions.addWidget(diagnostics)
        actions.addWidget(inspect)
        actions.addWidget(open_folder)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.tools_list = QListWidget()
        self.tools_list.currentRowChanged.connect(self.render_selected_tool)
        layout.addWidget(self.tools_list)
        self.tools_grid = QGridLayout()
        layout.addLayout(self.tools_grid)
        self.tools_output = QTextEdit()
        self.tools_output.setReadOnly(True)
        self.tools_output.setPlaceholderText("Tool inventory, diagnostics, and open-source Backend Inspector output appear here.")
        layout.addWidget(self.tools_output, 1)
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
            "iPS-UU discovers the bundled external toolchain, builds the iOS Guide command sequence, previews every "
            "command, opens selected steps in Terminal, and records diagnostics without hiding backend behavior."
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
            "2. Trust the Mac from the device when normal mode is available",
            "3. Enter Recovery mode",
            "4. Build the guide workflow for your detected A9(X) or A10(X) device",
            "5. Run each selected Terminal step in order and follow Turdus Merula prompts",
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
            ("shcblock", "A9 pre-restore shcblock", "A9 only: shcblock from pre-restore step"),
            ("post_shcblock", "A9 post-restore shcblock", "A9 only: shcblock from post-restore step"),
            ("pteblock", "A9 pteblock", "A9 only: pteblock used for tethered boot"),
            ("iboot_img4", "A10 iBoot.img4", "A10(X): image4/iBoot.img4"),
            ("signed_sep_img4", "signed-SEP.img4", "A10/A9: image4/signed-SEP.img4"),
            ("target_sep_im4p", "target-SEP.im4p", "A10(X): image4/target-SEP.im4p"),
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
            "Turdus Merula and turdusra1n run as external backend commands. Recovery and DFU transitions still happen in the "
            "Terminal prompts; refresh device mode here whenever the guide asks you to confirm the device state."
        )
        dfu.setObjectName("Muted")
        dfu.setWordWrap(True)
        layout.addWidget(dfu)

        run_row = QHBoxLayout()
        preflight = QPushButton("Run Preflight")
        preflight.clicked.connect(self.run_turdus_preflight)
        guide_plan = QPushButton("Build Guide Workflow")
        guide_plan.setObjectName("PrimaryButton")
        guide_plan.clicked.connect(self.build_turdus_guide_workflow)
        open_step = QPushButton("Open Selected Step in Terminal")
        open_step.clicked.connect(self.open_turdus_step_terminal)
        dry_run = QPushButton("Run Preflight / Dry Run")
        dry_run.clicked.connect(self.run_turdus_dry_run)
        self.tm_execute_btn = QPushButton("Use Selected Terminal Step")
        self.tm_execute_btn.clicked.connect(self.open_turdus_step_terminal)
        self.tm_execute_btn.setEnabled(False)
        copy = QPushButton("Copy Diagnostics")
        copy.clicked.connect(self.copy_turdus_diagnostics)
        export = QPushButton("Export Session Log")
        export.clicked.connect(self.export_turdus_session_log)
        run_row.addWidget(preflight)
        run_row.addWidget(guide_plan)
        run_row.addWidget(open_step)
        run_row.addWidget(dry_run)
        run_row.addWidget(self.tm_execute_btn)
        run_row.addWidget(copy)
        run_row.addWidget(export)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.tm_preflight_layout = QVBoxLayout()
        layout.addLayout(self.tm_preflight_layout)
        layout.addWidget(QLabel("Guide Workflow Steps"))
        self.tm_step_list = QListWidget()
        layout.addWidget(self.tm_step_list)
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
            <h2>iPS-UU Device Servicing Workspace</h2>
            <p>Version {__version__}</p>
            <p><b>One professional workspace for iOS servicing and research.</b></p>
            <p>This application is a professional device servicing and research interface for iOS restore, recovery, signed downgrade analysis, app install, firmware inspection, and detailed device statistics. You are responsible for your device, data, warranty status, carrier obligations, and compliance with local law.</p>
            <p>Bundled open-source tools are treated as external backends. iPS-UU shows tool paths, versions, command plans, output, logs, architecture readiness, and practical risks instead of presenting backend behavior as proprietary app logic.</p>
            """
        )
        layout.addWidget(about)
        return page

    def load_initial_state(self) -> None:
        LOGGER.info("GUI started")
        self.start_worker("inventory", backend_inventory)
        self.refresh_ios_device_viewer()
        self.device_viewer_timer.start()
        self.refresh_tool_inventory()
        if self.settings_data.last_ipsw:
            QTimer.singleShot(200, self.parse_selected_ipsw)

    def switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.page_title.setText(self.nav_items[index])
        subtitles = {
            0: "Professional firmware control for researchers, technicians, and device owners.",
            1: "Detect connected devices, trust state, identifiers, and recovery/DFU mode.",
            2: "Inspect IPSW metadata before restore, downgrade, or boot planning.",
            3: "Show standard signed restore paths, selected-IPSW status, backend support, and session logs.",
            4: "Plan and execute restore/update workflows through external backends.",
            5: "Local-only mock signing states for UI testing; never valid for real restore.",
            6: "Internal-only Purple Restore/Tatsu UI emulator with no real restore authority.",
            7: "Signed firmware downgrade planning with compatibility, signing expectations, risks, dry-run, and confirmation.",
            8: "Install IPA files through ideviceinstaller on trusted user-controlled devices.",
            9: "Review structured activity logs and export session records.",
            10: "Discover bundled flashing, recovery, app-install, and diagnostic tools.",
            11: "Configure paths, logging, and execution policy.",
            12: "Purpose, safety, version, and credits.",
        }
        self.page_subtitle.setText(subtitles.get(index, ""))

    def start_worker(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        worker = TaskWorker(name, fn, *args, **kwargs)
        if worker.kwargs.pop("_stream_to_ui", False):
            worker.kwargs["callback"] = lambda stream, line, worker=worker: worker.signals.stream.emit(worker.name, stream, line)
        worker.setAutoDelete(False)
        worker.signals.started.connect(self.task_started)
        worker.signals.stream.connect(self.task_stream)
        worker.signals.result.connect(self.task_result)
        worker.signals.error.connect(self.task_error)
        worker.signals.finished.connect(self.task_finished)
        self.active_workers.append(worker)
        self.thread_pool.start(worker)

    def task_stream(self, name: str, stream: str, line: str) -> None:
        if name == "forsake_execute" and hasattr(self, "forsake_output"):
            self.forsake_output.append(f"[{stream}] {line}")

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
            if hasattr(self, "connected_viewer_output"):
                self.connected_viewer_output.setPlainText(json.dumps(self.device_viewer or {}, indent=2, sort_keys=True))
                self.render_connected_device_verbose()
            if hasattr(self, "ios_device_list"):
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
        elif name == "tool_inventory":
            self.tools_inventory = result if isinstance(result, dict) else None
            self.render_tools_inventory()
        elif name == "tool_diagnostics":
            self.tool_diagnostics = result if isinstance(result, dict) else None
            self.tools_output.setPlainText(json.dumps(self.tool_diagnostics, indent=2, sort_keys=True))
        elif name == "tool_analysis":
            self.tool_analysis = result if isinstance(result, dict) else None
            self.tools_output.setPlainText(json.dumps(self.tool_analysis, indent=2, sort_keys=True))
        elif name == "restore_options":
            self.restore_options = result if isinstance(result, dict) else None
            self.render_restore_options()
        elif name == "forsake_toolchain":
            self.forsake_toolchain = result if isinstance(result, dict) else None
            self.render_forsake()
        elif name == "forsake_ipsw":
            self.forsake_ipsw = result if isinstance(result, dict) else None
            self.render_forsake()
        elif name == "forsake_execute":
            if isinstance(result, dict):
                self.forsake_output.append(json.dumps(result, indent=2, sort_keys=True))
                self.forsake_session_dir = result.get("session_dir") or self.forsake_session_dir
            self.render_forsake()
        self.update_dashboard()

    def refresh_device(self) -> None:
        self.start_worker("device", detect_target, "auto")

    def refresh_connected_device(self) -> None:
        self.refresh_device()
        self.refresh_ios_device_viewer()

    def refresh_ios_device_viewer(self) -> None:
        if any(worker.name == "ios_device_viewer" for worker in self.active_workers):
            return
        self.start_worker("ios_device_viewer", load_device_viewer_snapshot)

    def auto_refresh_ios_device_viewer(self) -> None:
        if self.nav.currentRow() == self.nav_items.index("Connected Device"):
            self.refresh_ios_device_viewer()

    def copy_ios_device_viewer_diagnostics(self) -> None:
        payload = (self.device_viewer or {}).get("diagnostics") or self.device_viewer or {}
        QApplication.clipboard().setText(json.dumps(payload, indent=2, sort_keys=True))
        LOGGER.info("iOS device viewer diagnostics copied")

    def selected_ios_device_udid(self) -> str | None:
        devices = (self.device_viewer or {}).get("devices") or []
        index = self.ios_device_list.currentRow() if hasattr(self, "ios_device_list") else -1
        if 0 <= index < len(devices):
            return devices[index].get("udid")
        if devices:
            return devices[0].get("udid")
        return None

    def dfu_instruction_text(self) -> str:
        return (
            "DFU is a hardware button sequence; the app cannot put a device into DFU for you. "
            "For Face ID / iPhone 8 or newer: connect USB, quick press Volume Up, quick press Volume Down, hold Side until the screen goes black, then hold Side + Volume Down for 5 seconds, release Side and keep holding Volume Down for about 10 seconds. "
            "For iPhone 7 / 7 Plus: hold Side + Volume Down for 8 seconds, release Side, keep holding Volume Down for about 10 seconds. "
            "For Home-button devices: hold Power + Home for 8 seconds, release Power, keep holding Home for about 10 seconds. "
            "A DFU screen is normally black; use Refresh Device to verify DFU/recovery detection."
        )

    def show_dfu_instructions(self) -> None:
        QMessageBox.information(self, "DFU Instructions", self.dfu_instruction_text())

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
        self.start_worker("contents_requirements", contents_requirements, DEFAULT_CONTENTS_ROOT, CONTENTS_RESTORE_METHODS)

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
            self.tm_guide_workflow = None
            self.parse_turdus_ipsw()

    def parse_turdus_ipsw(self) -> None:
        path = self.tm_ipsw_path.text().strip()
        if not path:
            QMessageBox.information(self, "iPS-UU", "Choose an IPSW before parsing.")
            return
        self.tm_guide_workflow = None
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
            self.tm_guide_workflow = None
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

    def build_turdus_guide_workflow(self) -> None:
        artifacts = {name: field.text().strip() or None for name, field in self.tm_artifact_paths.items()}
        self.tm_guide_workflow = build_turdus_guide_workflow(self.tm_device or self.device or {}, self.tm_ipsw or {}, artifacts)
        self.tm_output.setPlainText(json.dumps(self.tm_guide_workflow, indent=2, sort_keys=True))
        self.render_turdus()

    def open_turdus_step_terminal(self) -> None:
        workflow = self.tm_guide_workflow
        if not workflow:
            self.build_turdus_guide_workflow()
            workflow = self.tm_guide_workflow
        steps = (workflow or {}).get("steps") or []
        index = self.tm_step_list.currentRow()
        if not (0 <= index < len(steps)):
            QMessageBox.information(self, "iPS-UU", "Select a Turdus Merula workflow step first.")
            return
        step = steps[index]
        command = [str(part) for part in (step.get("command") or [])]
        if any(part.startswith("<") and part.endswith(">") for part in command):
            QMessageBox.warning(self, "iPS-UU", "This step still has placeholder file paths. Select the required IPSW/artifacts first.")
            return
        preview = str(step.get("command_preview") or " ".join(command))
        confirmation = QMessageBox.question(
            self,
            "Run Turdus Merula Step",
            f"This will open a new Terminal window and run this external backend command:\n\n{preview}\n\n"
            "This may erase data, require tethered boot, or require recovery if it fails. Continue?",
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        result = run_turdus_terminal_command(command)
        self.tm_output.setPlainText(json.dumps({"step": step, "terminal": result}, indent=2, sort_keys=True))

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

    def pick_restore_options_ipsw(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose IPSW", str(Path.home()), "IPSW firmware (*.ipsw);;All files (*)")
        if path:
            self.restore_options_ipsw_path.setText(path)
            self.refresh_restore_options()

    def pick_restore_options_doc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose Restore Document", str(Path.home()), "Restore documents (*.pr *.plist *.zip);;All files (*)")
        if path:
            self.restore_options_doc_path.setText(path)
            self.refresh_restore_options()

    def refresh_restore_options(self) -> None:
        path = self.restore_options_ipsw_path.text().strip() or None
        doc_path = self.restore_options_doc_path.text().strip() if hasattr(self, "restore_options_doc_path") else None
        if path and not Path(path).exists():
            QMessageBox.warning(self, "iPS-UU", "The selected IPSW does not exist.")
            return
        if doc_path and not Path(doc_path).exists():
            QMessageBox.warning(self, "iPS-UU", "The selected restore document does not exist.")
            return
        self.restore_options_status.setText("Checking device, signing metadata, IPSW compatibility, Purple Restore profile, and restore document options...")
        self.start_worker("restore_options", analyze_restore_options, path, self.device, 10, doc_path or None)

    def render_restore_options(self) -> None:
        if not hasattr(self, "restore_options_output"):
            return
        clear_layout(self.restore_options_device_grid)
        clear_layout(self.restore_options_paths_grid)
        clear_layout(self.restore_options_backend_grid)
        payload = self.restore_options or {}
        device = payload.get("device_status") or {}
        fields = [
            ("ProductType", device.get("product_type") or "Unknown", ""),
            ("Model", device.get("model") or "Unknown", ""),
            ("Chip Family", device.get("chip_family") or "Unknown", ""),
            ("Current iOS", device.get("current_ios_version") or "Unknown", ""),
            ("Mode", device.get("mode") or "Unknown", "normal, recovery, or DFU"),
            ("ECID", device.get("ecid") or "Unavailable", ""),
            ("Serial Number", device.get("serial_number") or "Unavailable", "Classic device browser field"),
            ("USB Location", device.get("usb_location") or "Unavailable", "Classic device browser field"),
            ("Board Config", device.get("board_config") or "Unavailable", "BuildManifest/device-map matching"),
        ]
        for index, (title, value, detail) in enumerate(fields):
            self.restore_options_device_grid.addWidget(Card(title, str(value), str(detail)), index // 3, index % 3)

        firmware = payload.get("firmware_check") or {}
        if firmware:
            status = str(firmware.get("status") or "Unknown")
            tone = {
                "Installable": "ok",
                "Not installable": "bad",
                "Unsupported device": "bad",
                "Signature unavailable": "warn",
                "Requires external research backend": "warn",
                "Tethered only": "warn",
            }.get(status, "neutral")
            self.restore_options_status.setText(status)
            self.restore_options_paths_grid.addWidget(pill(status, tone), 0, 0)
            signature = firmware.get("signature") or {}
            compatibility = firmware.get("compatibility") or {}
            self.restore_options_paths_grid.addWidget(
                Card("Selected IPSW", status, f"{compatibility.get('message') or ''} {signature.get('detail') or ''}".strip()),
                0,
                1,
            )
        else:
            self.restore_options_status.setText("Restore paths refreshed. Select an IPSW to check installability.")

        for index, path_info in enumerate(payload.get("available_restore_paths") or []):
            possible = bool(path_info.get("possible"))
            title = str(path_info.get("name") or "Restore path")
            value = str(path_info.get("status") or ("Installable" if possible else "Not installable"))
            detail = str(path_info.get("command_preview") or path_info.get("guidance") or "")
            self.restore_options_paths_grid.addWidget(Card(title, value, detail), (index + 1) // 2 + 1, (index + 1) % 2)

        for index, backend in enumerate((payload.get("external_backends") or [])[:6]):
            detected = "Detected" if backend.get("detected") else "Missing"
            detail = f"{backend.get('tethered_status')}; {backend.get('supported_ios_versions')}"
            self.restore_options_backend_grid.addWidget(Card(str(backend.get("tool_name") or "Backend"), detected, detail), index // 3, index % 3)

        purple = payload.get("purple_restore_internal") or {}
        offset = len((payload.get("external_backends") or [])[:6])
        for index, backend in enumerate(purple.get("executor_candidates") or []):
            state = "Enabled" if backend.get("enabled") else "Available" if backend.get("available") else "Missing"
            detail = "; ".join(str(item) for item in backend.get("requires") or []) or str(backend.get("value") or "")
            self.restore_options_backend_grid.addWidget(Card(str(backend.get("name") or backend.get("id")), state, detail), (offset + index) // 3, (offset + index) % 3)

        restore_doc = payload.get("restore_document")
        if restore_doc:
            value = str(restore_doc.get("status") or "Unknown")
            detail = f"{restore_doc.get('match_count', 0)} restore option set(s) found"
            if restore_doc.get("error"):
                detail = str(restore_doc.get("error"))
            self.restore_options_backend_grid.addWidget(Card("Restore Document", value, detail), (offset + len(purple.get("executor_candidates") or [])) // 3 + 1, 0)

        classic = payload.get("purple_restore_classic") or {}
        if classic:
            schema = classic.get("pr2_schema") or {}
            state = "Modeled" if classic.get("available") else "Unavailable"
            detail = f"{schema.get('key_path_count', 0)} PR2 key paths; execution backend disabled"
            self.restore_options_backend_grid.addWidget(Card("PurpleRestore Classic", state, detail), (offset + len(purple.get("executor_candidates") or [])) // 3 + 1, 1)

        downgrade = payload.get("downgrade_preflight") or {}
        if downgrade:
            blockers = downgrade.get("blockers") or []
            warnings = downgrade.get("warnings") or []
            state = "Blocked" if blockers else "Review" if warnings else "No downgrade target"
            detail = f"Required mode: {downgrade.get('required_mode')}; force downgrade supported: {downgrade.get('modern_force_downgrade_supported')}"
            self.restore_options_backend_grid.addWidget(Card("Downgrade Preflight", state, detail), (offset + len(purple.get("executor_candidates") or [])) // 3 + 1, 2)
            if blockers or warnings:
                message = "; ".join(str(item) for item in [*blockers[:2], *warnings[:2]])
                self.restore_options_paths_grid.addWidget(Card("Classic/Legacy Policy", state, message), 0, 2)

        self.restore_options_output.setPlainText(json.dumps(payload, indent=2, sort_keys=True))

    def refresh_tool_inventory(self) -> None:
        self.start_worker("tool_inventory", discover_tools)

    def selected_tool_name(self) -> str | None:
        tools = (self.tools_inventory or {}).get("tools") or []
        if not tools:
            return None
        index = self.tools_list.currentRow()
        if 0 <= index < len(tools):
            return str(tools[index].get("name") or "")
        return str(tools[0].get("name") or "")

    def run_selected_tool_diagnostics(self) -> None:
        name = self.selected_tool_name()
        if not name:
            QMessageBox.information(self, "iPS-UU", "Refresh Tools before running diagnostics.")
            return
        self.start_worker("tool_diagnostics", run_diagnostics, name)

    def run_backend_inspector(self) -> None:
        name = self.selected_tool_name()
        if not name:
            QMessageBox.information(self, "iPS-UU", "Refresh Tools before using Backend Inspector.")
            return
        self.start_worker("tool_analysis", analyze_open_source_tool, name)

    def open_selected_tool_folder(self) -> None:
        tools = (self.tools_inventory or {}).get("tools") or []
        index = self.tools_list.currentRow()
        tool = tools[index] if 0 <= index < len(tools) else (tools[0] if tools else None)
        path = Path(str((tool or {}).get("path") or "tools"))
        folder = path if path.is_dir() else path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def render_tools_inventory(self) -> None:
        if not hasattr(self, "tools_list"):
            return
        current_name = self.selected_tool_name()
        self.tools_list.clear()
        for tool in (self.tools_inventory or {}).get("tools") or []:
            status = "detected" if tool.get("detected") else "missing"
            item = QListWidgetItem(f"{tool.get('name')} - {status}")
            item.setData(Qt.ItemDataRole.UserRole, tool.get("name"))
            self.tools_list.addItem(item)
            if current_name and current_name == tool.get("name"):
                self.tools_list.setCurrentItem(item)
        if self.tools_list.count() and self.tools_list.currentRow() < 0:
            self.tools_list.setCurrentRow(0)
        self.render_selected_tool()

    def render_selected_tool(self, _index: int | None = None) -> None:
        if not hasattr(self, "tools_grid"):
            return
        clear_layout(self.tools_grid)
        tools = (self.tools_inventory or {}).get("tools") or []
        index = self.tools_list.currentRow() if hasattr(self, "tools_list") else 0
        tool = tools[index] if 0 <= index < len(tools) else None
        if not tool:
            self.tools_output.setPlainText(json.dumps(self.tools_inventory or {}, indent=2, sort_keys=True))
            return
        workflow = ", ".join(str(item) for item in tool.get("supported_workflows") or [])
        families = ", ".join(str(item) for item in tool.get("supported_device_families") or [])
        architecture_summary = ", ".join(
            f"{component.get('filename')}:{'/'.join((component.get('architecture') or {}).get('architectures') or ['script' if (component.get('architecture') or {}).get('kind') == 'script' else 'unknown'])}"
            for component in (tool.get("components") or [])
        )
        fields = [
            Card("Detected", "Yes" if tool.get("detected") else "Missing", "All required components detected" if tool.get("all_required_detected") else "Some components may be missing"),
            Card("Path", str(tool.get("path") or ""), "Executable" if tool.get("executable") else "Not executable or not present"),
            Card("Version", str(tool.get("version") or "Unavailable"), "Passive --version/-v style checks only."),
            Card("Apple Silicon / Intel", "Ready" if tool.get("universal2_ready") else "Check required", architecture_summary or "No executable architecture metadata."),
            Card("Purpose", str(tool.get("purpose") or ""), workflow),
            Card("Required Device Mode", str(tool.get("required_device_mode") or "Unknown"), families),
            Card("Open-Source License", str(tool.get("open_source_license") or "Verify bundled source"), "Shown when LICENSE/COPYING is available."),
        ]
        for idx, card in enumerate(fields):
            self.tools_grid.addWidget(card, idx // 2, idx % 2)
        self.tools_output.setPlainText(json.dumps(tool, indent=2, sort_keys=True))

    def pick_downgrade_ipsw(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose IPSW", str(Path.home()), "IPSW firmware (*.ipsw);;All files (*)")
        if path:
            self.downgrade_ipsw_path.setText(path)

    def check_downgrade_compatibility(self) -> None:
        path = self.downgrade_ipsw_path.text().strip()
        if not path:
            QMessageBox.information(self, "iPS-UU", "Choose an IPSW before checking compatibility.")
            return
        try:
            ipsw = parse_ipsw(path, (self.device or {}).get("product_type"))
            comp = compatibility_summary(self.device, ipsw)
        except Exception as exc:
            payload = {"status": "error", "error": str(exc)}
        else:
            payload = {"device": self.device, "ipsw": ipsw, "compatibility": comp, "backend": self.downgrade_backend.currentText(), "restore_status": self.downgrade_status.currentText()}
        self.downgrade_output.setPlainText(json_text(payload))

    def run_downgrade_dry_run(self) -> None:
        path = self.downgrade_ipsw_path.text().strip()
        backend = self.downgrade_backend.currentText()
        if not path:
            QMessageBox.information(self, "iPS-UU", "Choose an IPSW before dry-run.")
            return
        if backend == "idevicerestore":
            self.downgrade_plan = build_restore_plan(path, erase=True)
        elif backend == "cfgutil":
            self.downgrade_plan = {
                "purpose": "Signed firmware restore planning through Apple Configurator cfgutil.",
                "backend": backend,
                "command": ["tools/cfgutil", "restore", "<selected-device>", path],
                "command_preview": f"tools/cfgutil restore <selected-device> {path}",
                "device": self.device,
                "ipsw": path,
                "restore_status": self.downgrade_status.currentText(),
                "risks": [
                    "This may erase data.",
                    "This may update the device if the selected firmware is not signed.",
                    "This may affect activation.",
                    "This may fail and require recovery.",
                    "Unsigned firmware is normally refused by Apple restore services.",
                ],
                "execute_requires_confirmation": True,
            }
        else:
            self.downgrade_plan = {"backend": backend, "status": "future signed-restore backend placeholder", "execute_requires_confirmation": True}
        self.downgrade_output.setPlainText(json_text(self.downgrade_plan))

    def generate_mock_signing_response(self) -> None:
        if not self.signing_simulation_toggle.isChecked():
            QMessageBox.warning(self, "Apple Connect", "Enable local signing simulation mode before generating a mock response.")
            return
        status = self.mock_result_selector.currentText()
        try:
            response = generate_mock_response(
                self.mock_device_model.text().strip(),
                self.mock_ecid.text().strip(),
                self.mock_build.text().strip(),
                status,
                simulation=True,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Apple Connect", str(exc))
            return
        self.mock_signing_response = response
        self.mock_signing_output.setPlainText(json_text(response))

    def copy_mock_signing_json(self) -> None:
        if not self.mock_signing_response:
            self.generate_mock_signing_response()
        if self.mock_signing_response:
            QApplication.clipboard().setText(json_text(self.mock_signing_response))
            LOGGER.info("mock signing JSON copied")

    def save_mock_signing_ticket(self) -> None:
        if not self.signing_simulation_toggle.isChecked():
            QMessageBox.warning(self, "Apple Connect", "Enable local signing simulation mode before saving a mock ticket.")
            return
        if not self.mock_signing_response:
            self.generate_mock_signing_response()
        if not self.mock_signing_response:
            return
        default_name = safe_mock_ticket_name(
            str(self.mock_signing_response.get("device") or "device"),
            str(self.mock_signing_response.get("build") or "build"),
        )
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Save Mock Ticket",
            str(Path.home() / default_name),
            "Mock JSON tickets (*.mock.json)",
        )
        if not target:
            return
        try:
            saved = save_mock_ticket(self.mock_signing_response, target)
        except Exception as exc:
            QMessageBox.warning(self, "Apple Connect", str(exc))
            return
        self.mock_signing_output.setPlainText(json_text({**self.mock_signing_response, "saved_to": str(saved)}))
        LOGGER.info("mock signing ticket saved to %s", saved)

    def simulate_mock_restore_flash(self) -> None:
        if not self.signing_simulation_toggle.isChecked():
            QMessageBox.warning(self, "Apple Connect", "Enable local signing simulation mode before simulating restore/flash.")
            return
        if not self.mock_signing_response:
            self.generate_mock_signing_response()
        if not self.mock_signing_response:
            return
        try:
            plan = simulated_restore_flash_plan(self.mock_signing_response, simulation=True)
        except Exception as exc:
            QMessageBox.warning(self, "Apple Connect", str(exc))
            return
        self.mock_signing_output.setPlainText(json_text(plan))

    def pick_purple_ipsw(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose IPSW", str(Path.home()), "IPSW firmware (*.ipsw);;All files (*)")
        if path:
            self.purple_ipsw_path.setText(path)
            self.prepare_purple_restore()

    def prepare_purple_restore(self) -> None:
        device = {
            **(self.device or {}),
            "product_type": self.purple_product_type.text().strip(),
            "ecid": self.purple_ecid.text().strip() or "mock-ecid",
            "current_mode": self.purple_mode.currentText(),
        }
        try:
            self.purple_session = build_purple_restore_session(
                device,
                self.purple_ipsw_path.text().strip() or None,
                simulation=True,
                product_type_override=self.purple_product_type.text().strip() or None,
                mode_override=self.purple_mode.currentText(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Apple Connect", str(exc))
            return
        self.purple_output.setPlainText(json_text(self.purple_session))

    def request_purple_mock_ticket(self) -> None:
        if not self.purple_session:
            self.prepare_purple_restore()
        if not self.purple_session:
            return
        try:
            self.purple_session = request_mock_tatsu_ticket(self.purple_session, simulation=True)
        except Exception as exc:
            QMessageBox.warning(self, "Apple Connect", str(exc))
            return
        self.purple_output.setPlainText(json_text(self.purple_session))

    def run_purple_restore_emulation(self) -> None:
        if not self.purple_session:
            self.prepare_purple_restore()
        if not self.purple_session:
            return
        if not self.purple_session.get("mock_tatsu_ticket"):
            try:
                self.purple_session = request_mock_tatsu_ticket(self.purple_session, simulation=True)
            except Exception as exc:
                QMessageBox.warning(self, "Apple Connect", str(exc))
                return
        try:
            self.purple_session = run_purple_restore_simulation(self.purple_session, simulation=True, succeed=True)
        except Exception as exc:
            QMessageBox.warning(self, "Apple Connect", str(exc))
            return
        self.purple_output.setPlainText(json_text(self.purple_session))

    def copy_purple_restore_json(self) -> None:
        if not self.purple_session:
            self.prepare_purple_restore()
        if self.purple_session:
            QApplication.clipboard().setText(json_text(self.purple_session))
            LOGGER.info("Purple Restore emulator JSON copied")

    def confirm_downgrade_execution(self) -> None:
        QMessageBox.information(self, "iPS-UU", "Execution remains gated by backend-specific confirmation and is not enabled from this generic downgrade page.")

    def prepare_jailbreak_boot_plan(self) -> None:
        device = self.device or {}
        backend = self.jb_backend.currentText()
        self.jb_target.setText(str(device.get("product_type") or device.get("udid") or "No connected device"))
        if backend == "palera1n":
            plan = build_rootless_launch_plan()
            command = list(plan.get("command") or ["tools/palera1n", "-l"])
            mode = "DFU"
        elif backend == "turdus merula":
            command = ["tools/turdus_merula", "<backend-supported args selected by user>"]
            mode = "DFU or recovery"
        else:
            command = ["tools/<future-backend>", "<explicit args>"]
            mode = "backend-defined"
        preview = " ".join(command)
        self.jb_required_mode.setText(mode)
        self.jb_command_preview.setText(preview)
        self.jailbreak_plan = {
            "target_device": device,
            "required_mode": mode,
            "backend_selected": backend,
            "command": command,
            "command_preview": preview,
            "live_output": "Output streams here only after a supported backend command is confirmed.",
            "summary": "No new exploits are implemented and no exploit logic is rewritten by iPS-UU.",
            "risks": [
                "This may erase data.",
                "This may require tethered boot.",
                "This may affect activation.",
                "This may void warranty.",
                "This may fail and require recovery.",
                "Check local law before use.",
            ],
        }
        self.jb_output.setPlainText(json.dumps(self.jailbreak_plan, indent=2, sort_keys=True))

    def run_palera1n_l_terminal(self) -> None:
        try:
            result = launch_rootless_in_terminal()
        except Exception as exc:
            QMessageBox.warning(self, "iPS-UU", str(exc))
            return
        self.jb_command_preview.setText(str(result.get("command_preview") or "tools/palera1n -l"))
        self.jb_output.setPlainText(json.dumps(result, indent=2, sort_keys=True))

    def pick_ipa(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose IPA", str(Path.home()), "iOS app archives (*.ipa);;All files (*)")
        if path:
            self.ipa_path.setText(path)

    def build_app_install_plan(self) -> None:
        path = self.ipa_path.text().strip()
        if not path:
            QMessageBox.information(self, "iPS-UU", "Choose an IPA before building an install plan.")
            return
        self.install_plan = build_install_plan(path, (self.device or {}).get("udid"))
        self.apps_install_output.setPlainText(json.dumps(self.install_plan, indent=2, sort_keys=True))

    def refresh_forsake_device(self) -> None:
        self.refresh_connected_device()
        self.start_worker("forsake_toolchain", find_forsake_toolchain)

    def pick_forsake_ipsw(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose IPSW", str(Path.home()), "IPSW firmware (*.ipsw);;All files (*)")
        if path:
            self.forsake_ipsw_path.setText(path)
            self.parse_forsake_ipsw()

    def pick_forsake_file(self, target: QLineEdit, title: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, title, str(Path.home()), "All files (*)")
        if path:
            target.setText(path)

    def parse_forsake_ipsw(self) -> None:
        path = self.forsake_ipsw_path.text().strip()
        if not path:
            QMessageBox.information(self, "iPS-UU", "Choose an IPSW before parsing.")
            return
        self.start_worker("forsake_ipsw", parse_ipsw, path, (self.device or {}).get("product_type"))

    def forsake_selected_files(self) -> dict[str, str | None]:
        return {
            "shsh_blob": self.forsake_blob_path.text().strip() or None,
            "boot_files": self.forsake_boot_files_path.text().strip() or None,
        }

    def run_forsake_compatibility(self) -> None:
        self.forsake_toolchain = find_forsake_toolchain()
        self.forsake_compatibility = check_forsake_requirements(self.device or {}, self.forsake_ipsw, self.forsake_selected_files())
        self.render_forsake()

    def run_forsake_dry_run(self) -> None:
        self.run_forsake_compatibility()
        self.forsake_plan = build_forsake_dry_run_plan(self.device or {}, self.forsake_ipsw, self.forsake_selected_files())
        self.forsake_session_dir = str(create_forsake_session_dir())
        write_forsake_session_inputs(
            Path(self.forsake_session_dir),
            self.device or {},
            self.forsake_toolchain or {},
            self.forsake_compatibility or {},
            self.forsake_plan,
        )
        self.forsake_plan_output.setPlainText(json.dumps(self.forsake_plan, indent=2, sort_keys=True))
        self.render_forsake()

    def execute_forsake_plan(self) -> None:
        if self.forsake_confirm_text.text().strip() != "I UNDERSTAND THIS MAY ERASE MY DEVICE":
            QMessageBox.warning(self, "iPS-UU", "Type the exact confirmation phrase before executing Forsake.")
            return
        if not self.forsake_plan:
            QMessageBox.warning(self, "iPS-UU", "Build a dry-run plan first.")
            return
        self.forsake_output.append("$ " + str(self.forsake_plan.get("command_preview") or ""))
        self.start_worker("forsake_execute", execute_plan_with_logs, self.forsake_plan, self.forsake_session_dir, _stream_to_ui=True)

    def cancel_forsake(self) -> None:
        result = cancel_forsake_process()
        self.forsake_output.append(json.dumps(result, indent=2, sort_keys=True))

    def copy_forsake_diagnostics(self) -> None:
        payload = {
            "device": self.device,
            "toolchain": self.forsake_toolchain,
            "ipsw": self.forsake_ipsw,
            "compatibility": self.forsake_compatibility,
            "plan": self.forsake_plan,
            "session_dir": self.forsake_session_dir,
        }
        QApplication.clipboard().setText(json.dumps(payload, indent=2, sort_keys=True))
        LOGGER.info("Forsake diagnostics copied")

    def render_forsake(self) -> None:
        if not hasattr(self, "forsake_device_grid"):
            return
        clear_layout(self.forsake_device_grid)
        clear_layout(self.forsake_ipsw_grid)
        clear_layout(self.forsake_compat_grid)
        device = self.device or {}
        diagnostics = device.get("diagnostics") or {}
        recommended = diagnostics.get("recommended_fix") or {}
        device_cards = [
            Card("ProductType", str(device.get("product_type") or "Unknown"), str(device.get("marketing_name") or "")),
            Card("Mode", str(device.get("current_mode") or "Unknown"), f"Backend: {device.get('detection_method') or 'none'}"),
            Card("Chip Family", str(device.get("chip_family") or "Unknown"), str(device.get("board_config") or "")),
            Card("ECID / CPID / BDID", str(device.get("ecid") or "Unavailable"), f"CPID {device.get('cpid') or 'n/a'} BDID {device.get('bdid') or 'n/a'}"),
            Card("Diagnostics", str(recommended.get("issue") or "Unknown"), str(recommended.get("recommended_fix") or "")),
            Card("Tool Commands", "See JSON", "Device diagnostics include stdout/stderr for each backend."),
        ]
        for idx, card in enumerate(device_cards):
            self.forsake_device_grid.addWidget(card, idx // 3, idx % 3)
        if self.forsake_ipsw:
            supported = ", ".join(self.forsake_ipsw.get("supported_product_types") or []) or "Unknown"
            ipsw_cards = [
                Card("IPSW ProductVersion", str(self.forsake_ipsw.get("product_version") or "Unknown"), ""),
                Card("IPSW BuildVersion", str(self.forsake_ipsw.get("product_build_version") or "Unknown"), ""),
                Card("Supported ProductTypes", supported, ""),
            ]
            for idx, card in enumerate(ipsw_cards):
                self.forsake_ipsw_grid.addWidget(card, idx // 3, idx % 3)
        compatibility = self.forsake_compatibility or check_forsake_requirements(device, self.forsake_ipsw, self.forsake_selected_files())
        self.forsake_compatibility = compatibility
        for idx, item in enumerate(compatibility.get("checks") or []):
            self.forsake_compat_grid.addWidget(
                Card(str(item.get("label")), "PASS" if item.get("passed") else "FAIL", str(item.get("detail") or "")),
                idx // 3,
                idx % 3,
            )
        self.forsake_compat_grid.addWidget(Card("Result", str(compatibility.get("status") or "Unknown"), ""), 10, 0)
        ready = compatibility.get("status") == "Ready" and bool(self.forsake_plan and self.forsake_plan.get("command"))
        self.forsake_execute_btn.setEnabled(bool(ready))
        payload = {
            "device": device,
            "toolchain": self.forsake_toolchain,
            "compatibility": compatibility,
            "support": compatibility.get("support"),
            "session_dir": self.forsake_session_dir,
        }
        if not self.forsake_plan_output.toPlainText().strip():
            self.forsake_plan_output.setPlainText(json.dumps(payload, indent=2, sort_keys=True))

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
            override = QMessageBox.warning(
                self,
                "Override Dry-Run Mode",
                "Dry-run only mode is enabled. Continue with a real signed firmware flash for this run only?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
                QMessageBox.StandardButton.Cancel,
            )
            if override != QMessageBox.StandardButton.Ok:
                return
        path = self.ipsw_path.text().strip()
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "iPS-UU", "Choose a valid IPSW before executing a restore.")
            return
        action = self.restore_action_combo.currentText()
        backend = self.backend_combo.currentText()
        product = (self.device or {}).get("product_type")
        try:
            preflight = dry_run_plan(path, "auto", product, None, None, None, action, backend)
        except Exception as exc:
            QMessageBox.warning(self, "iPS-UU", f"Flash preflight failed: {exc}")
            return
        self.plan = preflight
        self.plan_view.setPlainText(json_text(preflight))
        selected_backend = (preflight.get("candidate_restore_backend") or {}).get("selected")
        command = (preflight.get("candidate_restore_backend") or {}).get("command") or []
        warnings = preflight.get("warnings") or []
        if not command:
            detail = "\n".join(str(item) for item in warnings) or "No supported restore backend command is available."
            QMessageBox.warning(self, "iPS-UU", f"Cannot start firmware flash yet.\n\nBackend: {selected_backend or 'none'}\n{detail}")
            return
        first = QMessageBox.warning(
            self,
            "Confirm Firmware Flash",
            "This will force a real signed firmware restore/update attempt through the selected supported backend. "
            "The device may be erased. Backend signing, nonce, SEP/baseband, activation, and compatibility failures are terminal.\n\n"
            f"Command:\n{' '.join(str(part) for part in command)}",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Cancel,
        )
        if first != QMessageBox.StandardButton.Ok:
            return
        second = QMessageBox.warning(
            self,
            "Confirm Device Wipe Risk",
            "Confirm you understand this may wipe data. iPS-UU will not bypass Apple signing, APTicket, nonce, SEP, or baseband validation, and it will not call private reverse-engineered restore APIs.",
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
                ("Signed flash command running", "pending"),
                ("Backend result", "pending"),
            ]
        )
        LOGGER.warning("starting forced signed flash backend=%s action=%s ipsw=%s", backend, action, path)
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
            model = device.get("model_name") or device.get("product_type") or "iOS Device"
            version = device.get("product_version") or "iOS unknown"
            trust = device.get("pairing_status") or device.get("connection_status") or "Unknown"
            label = f"{model} | {version} | {trust}"
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
        if hasattr(self, "ios_device_header"):
            self.ios_device_header.set_device(device)
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
            ("Hardware Model", device.get("hardware_model") or "Unavailable", f"Board: {device.get('board_id') or 'unknown'}"),
            ("Chip / Die", device.get("chip_id") or "Unavailable", f"Die: {device.get('die_id') or 'unknown'}"),
            ("Device Class", device.get("device_class") or "Unavailable", str(device.get("cpu_architecture") or "")),
            ("Firmware Version", device.get("firmware_version") or device.get("product_version") or "Unknown", str(device.get("build_version") or "")),
            ("Activation", device.get("activation_state") or "Unavailable", f"Baseband: {device.get('baseband_version') or 'n/a'}"),
            ("Device Storage", format_bytes(device.get("disk_capacity_bytes")), f"Free: {format_bytes(device.get('disk_free_bytes'))}"),
            ("Battery", f"{device.get('battery_current_capacity')}%" if device.get("battery_current_capacity") is not None else "Unavailable", "Charging" if device.get("battery_is_charging") else ""),
            ("IMEI", device.get("imei") or "Unavailable", "Cellular devices only; requires trusted metadata access."),
            ("Region / Color", device.get("region_info") or "Unavailable", f"{device.get('color') or ''} {device.get('enclosure_color') or ''}".strip()),
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
        if hasattr(self, "device_diagnostics_output"):
            self.device_diagnostics_output.setPlainText(json.dumps((self.device or {}).get("diagnostics") or self.device or {}, indent=2, sort_keys=True))
        if hasattr(self, "connected_device_header"):
            self.connected_device_header.set_device(None, self.device)
        if not self.device or self.device.get("error"):
            self.device_empty.setText("No device detected")
            self.device_empty.show()
            detail = self.device.get("error") if self.device else "No device metadata available."
            self.device_grid.addWidget(Card("Detection", "Unavailable", str(detail)), 0, 0)
            self.render_connected_device_verbose()
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
        self.render_connected_device_verbose()

    def render_connected_device_verbose(self) -> None:
        if not hasattr(self, "connected_verbose_grid"):
            return
        clear_layout(self.connected_verbose_grid)
        devices = (self.device_viewer or {}).get("devices") or []
        device = devices[0] if devices else None
        if hasattr(self, "connected_device_visual"):
            self.connected_device_visual.set_device(device)
        if hasattr(self, "connected_device_header"):
            self.connected_device_header.set_device(device, self.device)
        self.render_connected_screen_preview()
        if not device:
            diagnosis = (self.device_viewer or {}).get("trust_diagnosis") or {}
            usb = (self.device_viewer or {}).get("host_usb") or {}
            self.connected_verbose_grid.addWidget(
                Card("Device Detection", str(diagnosis.get("status") or "No device"), str(diagnosis.get("summary") or "Refresh Detailed Viewer after connecting and trusting the device.")),
                0,
                0,
            )
            self.connected_verbose_grid.addWidget(
                Card("USB Host", "Apple device present" if usb.get("apple_mobile_device_present") else "No Apple USB device", "; ".join(str(item) for item in (usb.get("matched_lines") or [])) or "macOS did not report an iPhone/iPad/iPod on USB."),
                0,
                1,
            )
            self.connected_verbose_grid.addWidget(
                Card("Next Steps", "Check cable/port/device", " ".join(str(item) for item in (diagnosis.get("next_steps") or []))),
                0,
                2,
            )
            return
        fingerprint = device.get("fingerprint") or {}
        identity = fingerprint.get("identity") or {}
        hardware = fingerprint.get("hardware") or {}
        firmware = fingerprint.get("firmware") or {}
        radios = fingerprint.get("radios") or {}
        storage = fingerprint.get("storage") or {}
        battery = fingerprint.get("battery") or {}
        tools = (self.device_viewer or {}).get("tools") or {}
        diagnostics = (self.device_viewer or {}).get("diagnostics") or {}
        domains = ", ".join(str(item) for item in (fingerprint.get("metadata_domains") or [])) or "Unavailable"
        tool_summary = ", ".join(
            f"{name}:{'yes' if item.get('present') else 'no'}"
            for name, item in tools.items()
        ) or "Unavailable"
        rows = [
            ("Trust Diagnosis", (self.device_viewer or {}).get("trust_diagnosis", {}).get("status") or "Unknown", (self.device_viewer or {}).get("trust_diagnosis", {}).get("summary") or ""),
            ("Identity", identity.get("product_type") or device.get("product_type") or "Unknown", f"Model {identity.get('model_number') or device.get('model_id') or 'n/a'}, region {identity.get('region_info') or device.get('region_info') or 'n/a'}"),
            ("Serial / UDID", identity.get("serial_number") or device.get("serial_number") or "Unavailable", f"UDID {device.get('masked_udid') or 'unknown'}"),
            ("ECID", identity.get("ecid") or device.get("ecid") or "Unavailable", "UniqueChipID"),
            ("Hardware", hardware.get("hardware_model") or device.get("hardware_model") or "Unavailable", f"Board {hardware.get('board_id') or device.get('board_id') or 'n/a'}"),
            ("Chip", hardware.get("chip_id") or device.get("chip_id") or "Unavailable", f"Die {hardware.get('die_id') or device.get('die_id') or 'n/a'}"),
            ("Class / CPU", hardware.get("device_class") or device.get("device_class") or "Unavailable", hardware.get("cpu_architecture") or device.get("cpu_architecture") or ""),
            ("Color", hardware.get("device_color") or device.get("color") or "Unavailable", f"Enclosure {hardware.get('enclosure_color') or device.get('enclosure_color') or 'n/a'}"),
            ("Firmware", firmware.get("product_version") or device.get("product_version") or "Unknown", f"Build {firmware.get('build_version') or device.get('build_version') or 'unknown'}"),
            ("Activation", firmware.get("activation_state") or device.get("activation_state") or "Unavailable", "Activation state from lockdown metadata."),
            ("Baseband", firmware.get("baseband_version") or device.get("baseband_version") or "Unavailable", f"Serial {firmware.get('baseband_serial_number') or device.get('baseband_serial_number') or 'n/a'}"),
            ("IMEI / MEID", radios.get("imei") or device.get("imei") or "Unavailable", f"MEID {radios.get('meid') or 'n/a'}"),
            ("Network IDs", radios.get("wifi_address") or device.get("wifi_address") or "Unavailable", f"Bluetooth {radios.get('bluetooth_address') or device.get('bluetooth_address') or 'n/a'}"),
            ("Storage", format_bytes(storage.get("total_bytes") or device.get("disk_capacity_bytes")), f"Free {format_bytes(storage.get('free_bytes') or device.get('disk_free_bytes'))}"),
            ("Battery", f"{battery.get('current_capacity')}%" if battery.get("current_capacity") is not None else "Unavailable", "Charging" if battery.get("is_charging") else "Not charging/unknown"),
            ("Pairing / Lock", device.get("pairing_status") or "Unknown", device.get("lock_status") or ""),
            ("Metadata Domains", domains, "Queried through ideviceinfo where available."),
            ("Bundled Tools", tool_summary, "yes means detected from tools/ or PATH."),
            ("Last Error", diagnostics.get("last_pairing_or_status_error") or "None", "Pairing/status diagnostics."),
        ]
        for index, (title, value, detail) in enumerate(rows):
            self.connected_verbose_grid.addWidget(Card(title, str(value or "Unavailable"), str(detail or "")), index // 3, index % 3)

    def render_connected_screen_preview(self) -> None:
        if not hasattr(self, "connected_screen_preview"):
            return
        screen = (self.device_viewer or {}).get("screen") or {}
        message = str(screen.get("message") or "Screen preview unavailable.")
        path = screen.get("path")
        if screen.get("available") and path and Path(str(path)).exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.connected_screen_preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.connected_screen_preview.setPixmap(scaled)
                self.connected_screen_preview.setText("")
                self.connected_screen_status.setText(f"{message} {path}")
                return
        self.connected_screen_preview.setPixmap(QPixmap())
        self.connected_screen_preview.setText(message)
        self.connected_screen_status.setText(str(screen.get("policy") or "Preview uses idevicescreenshot when available."))

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
        if hasattr(self, "tm_step_list"):
            current_row = self.tm_step_list.currentRow()
            self.tm_step_list.clear()
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
            Card("Workflow", self.tm_mode_combo.currentText(), "Build the guide sequence, then run selected backend steps in Terminal."),
            Card("Artifacts", artifact_state, artifact_detail),
            Card("Activation Risk", "Warning" if ipsw.get("activation_baseband_warning") else "None detected", ipsw.get("activation_baseband_warning_text") or "No iOS 10 cellular A10X/iPhone 7 warning detected."),
            Card("Execution", "Terminal step", "Selected guide commands are opened visibly in Terminal with full command previews."),
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

        if self.tm_guide_workflow is None:
            try:
                artifacts_for_workflow = {name: field.text().strip() or None for name, field in self.tm_artifact_paths.items()}
                self.tm_guide_workflow = build_turdus_guide_workflow(self.tm_device or self.device or {}, self.tm_ipsw or {}, artifacts_for_workflow)
            except Exception:
                self.tm_guide_workflow = None
        for step in (self.tm_guide_workflow or {}).get("steps", []):
            item = QListWidgetItem(f"{step.get('title')} - {step.get('command_preview')}")
            item.setData(Qt.ItemDataRole.UserRole, step)
            self.tm_step_list.addItem(item)
        if self.tm_step_list.count():
            self.tm_step_list.setCurrentRow(current_row if 0 <= current_row < self.tm_step_list.count() else 0)

        output = {
            "toolchain": self.tm_toolchain,
            "device": self.tm_device,
            "ipsw": self.tm_ipsw,
            "artifacts": self.tm_artifacts,
            "preflight": self.tm_preflight,
            "plan": self.tm_plan,
            "guide_workflow": self.tm_guide_workflow,
            "session_dir": self.tm_session_dir,
        }
        self.tm_output.setPlainText(json.dumps(output, indent=2, sort_keys=True))
        self.tm_execute_btn.setEnabled(bool(self.tm_step_list.count()))

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
        findings = self.contents_requirements.get("restore_engine_findings") or []
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
            Card("Restore Findings", str(len(findings)), "New rengineer findings are integrated as guardrails and documentation."),
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
            self.card_dryrun.set(f"Backend: {backend}", f"{warnings} warning(s). Signed flash button remains confirmation-gated.")

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
            self.execute_btn.setEnabled(not restore_running)
            self.execute_btn.setToolTip(
                "Restore is currently running."
                if restore_running
                else "Run a real signed firmware restore/update through the first usable supported backend after confirmations."
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
            #DeviceHeader {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; }}
            #DeviceHeaderTitle {{ color: {text}; font-size: 20px; font-weight: 800; }}
            #MetricBlock {{ background: {bg}; border: 1px solid {border}; border-radius: 6px; }}
            #MetricLabel {{ color: {muted}; font-size: 11px; font-weight: 700; text-transform: uppercase; }}
            #MetricValue {{ color: {text}; font-size: 13px; font-weight: 700; }}
            #SimulationBanner {{ background: #fef3c7; color: #713f12; border: 1px solid #f59e0b; border-radius: 8px; padding: 12px; font-weight: 800; }}
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
