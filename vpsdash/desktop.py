from __future__ import annotations

import html
import hashlib
import inspect
import json
import os
import subprocess
import sys
import time
import traceback
import webbrowser
import ctypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import threading

from PySide6.QtCore import QObject, QEvent, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QFont, QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from werkzeug.serving import make_server

_QWEBENGINE_VIEW_CLASS: Any | None = None
_QWEBENGINE_IMPORT_ATTEMPTED = False

from .service import VpsDashService
from .theme import APP_QSS
from .execution import run_local_command


ACTIVE_PLATFORM_TASK_STATUSES = {"planned", "queued", "running", "cancel-requested"}
RETRYABLE_PLATFORM_TASK_STATUSES = {"failed", "cancelled"}
ARCHIVED_PLATFORM_TASK_STATUSES = {"cancelled", "complete", "completed"}


def _windows_subprocess_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _run_windows_native_command(argv: list[str], timeout: int = 60) -> dict[str, Any]:
    def _decode_output(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        if not isinstance(raw, (bytes, bytearray)):
            return str(raw)
        data = bytes(raw)
        looks_utf16 = data.startswith((b"\xff\xfe", b"\xfe\xff")) or data.count(b"\x00") >= max(2, len(data) // 6)
        if looks_utf16:
            for encoding in ("utf-16", "utf-16-le"):
                try:
                    decoded = data.decode(encoding).replace("\x00", "").strip()
                except UnicodeDecodeError:
                    continue
                if decoded:
                    return decoded
        for encoding in ("utf-8", "cp1252", "cp850", "cp437"):
            try:
                decoded = data.decode(encoding).replace("\x00", "").strip()
            except UnicodeDecodeError:
                continue
            if decoded:
                return decoded
        return data.decode("utf-8", errors="replace").replace("\x00", "").strip()

    completed = subprocess.run(
        argv,
        capture_output=True,
        timeout=timeout,
        check=False,
        **_windows_subprocess_kwargs(),
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": _decode_output(completed.stdout),
        "stderr": _decode_output(completed.stderr),
        "command": subprocess.list2cmdline(argv),
    }


def _resource_root() -> Path:
    runtime_root = getattr(sys, "_MEIPASS", None)
    if runtime_root:
        return Path(runtime_root)
    return Path(__file__).resolve().parent.parent


def _state_root() -> Path:
    runtime_root = getattr(sys, "_MEIPASS", None)
    if runtime_root:
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path.home() / ".local" / "share"
        root = base / "VPSdash"
        root.mkdir(parents=True, exist_ok=True)
        return root
    return Path(__file__).resolve().parent.parent


def _startup_log_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "state"
    return base / "VPSdash" / "desktop-launch.log"


def _app_icon_path() -> Path:
    return _resource_root() / "assets" / "icons" / "vpsdash_app.ico"


def _write_startup_log(message: str) -> None:
    try:
        path = _startup_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            handle.write(f"[{stamp}] {message.rstrip()}\n")
    except Exception:
        pass


def _get_webengine_view_class() -> Any | None:
    global _QWEBENGINE_VIEW_CLASS, _QWEBENGINE_IMPORT_ATTEMPTED
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return None
    if _QWEBENGINE_IMPORT_ATTEMPTED:
        return _QWEBENGINE_VIEW_CLASS
    _QWEBENGINE_IMPORT_ATTEMPTED = True
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView as _View
    except Exception:
        _QWEBENGINE_VIEW_CLASS = None
    else:
        _QWEBENGINE_VIEW_CLASS = _View
    return _QWEBENGINE_VIEW_CLASS


def _style_refresh(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


class AutoHeightButton(QPushButton):
    def __init__(self, text: str, preferred_height: int, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._preferred_height = preferred_height
        self.setMinimumHeight(preferred_height)
        self.setMaximumHeight(preferred_height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self) -> Any:
        hint = super().sizeHint()
        hint.setHeight(max(hint.height(), self._preferred_height))
        return hint

    def minimumSizeHint(self) -> Any:
        hint = super().minimumSizeHint()
        hint.setHeight(max(hint.height(), self._preferred_height))
        return hint


class HoverCopySecretField(QLineEdit):
    def __init__(self, on_copy: Any | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_value = ""
        self._on_copy = on_copy
        self.setReadOnly(True)
        self.setEchoMode(QLineEdit.Password)
        self.setCursor(Qt.PointingHandCursor)
        self.setPlaceholderText("No local SSH key detected")

    def setSecretValue(self, value: str) -> None:
        self._full_value = value or ""
        self.setText(self._full_value)
        self.setEnabled(bool(self._full_value))

    def enterEvent(self, event: QEvent) -> None:
        if self._full_value:
            self.setEchoMode(QLineEdit.Normal)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.setEchoMode(QLineEdit.Password)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QEvent) -> None:
        if self._full_value:
            QApplication.clipboard().setText(self._full_value)
            if callable(self._on_copy):
                self._on_copy()
        super().mousePressEvent(event)


def make_button(label: str, variant: str = "primary") -> QPushButton:
    button = AutoHeightButton(label, 44)
    button.setProperty("variant", variant)
    button.setCursor(Qt.PointingHandCursor)
    _style_refresh(button)
    return button


def make_nav_button(label: str) -> QPushButton:
    button = AutoHeightButton(label, 46)
    button.setProperty("nav", True)
    button.setCheckable(True)
    button.setCursor(Qt.PointingHandCursor)
    _style_refresh(button)
    return button


def make_chip(text: str = "", tone: str = "neutral") -> QLabel:
    chip = QLabel(text)
    chip.setObjectName("ChipLabel")
    chip.setProperty("tone", tone)
    _style_refresh(chip)
    return chip


class AutoHeightLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def heightForWidth(self, width: int) -> int:
        if width <= 0:
            return super().heightForWidth(width)
        margins = self.contentsMargins()
        inner_width = max(1, width - margins.left() - margins.right())
        flags = Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop
        rect = self.fontMetrics().boundingRect(0, 0, inner_width, 10000, flags, self.text())
        return max(
            rect.height() + margins.top() + margins.bottom(),
            super().minimumSizeHint().height(),
            super().heightForWidth(width),
            super().sizeHint().height(),
        )

    def sizeHint(self) -> Any:
        hint = super().sizeHint()
        width = self.width() or hint.width()
        if width > 0:
            hint.setHeight(max(hint.height(), self.heightForWidth(width)))
        return hint

    def minimumSizeHint(self) -> Any:
        hint = super().minimumSizeHint()
        width = self.width() or max(220, hint.width())
        hint.setHeight(max(hint.height(), self.heightForWidth(width)))
        return hint

    def resizeEvent(self, event: QEvent) -> None:
        super().resizeEvent(event)
        self.sync_height()

    def setText(self, text: str) -> None:
        super().setText(text)
        self.sync_height()

    def sync_height(self) -> None:
        width = self.contentsRect().width() or self.width()
        if width <= 0:
            return
        required = self.heightForWidth(width)
        if required > 0:
            self.setMinimumHeight(required)
        parent = self.parentWidget()
        while parent is not None:
            if parent.layout() is not None:
                parent.layout().invalidate()
            parent.updateGeometry()
            parent = parent.parentWidget()
        self.updateGeometry()


def set_chip(chip: QLabel, text: str, tone: str) -> None:
    chip.setText(text)
    chip.setProperty("tone", tone)
    _style_refresh(chip)


def make_section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("class", "SectionTitle")
    return label


def make_card_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("class", "CardTitle")
    return label


def make_helper_text(text: str) -> QLabel:
    label = AutoHeightLabel(text)
    label.setProperty("class", "HelperText")
    return label


def make_wrap_label(text: str = "", *, object_name: str | None = None, css_class: str | None = None) -> AutoHeightLabel:
    label = AutoHeightLabel(text)
    if object_name:
        label.setObjectName(object_name)
    if css_class:
        label.setProperty("class", css_class)
    return label


def make_form_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("class", "FormLabel")
    return label


def add_form_row(form: QFormLayout, text: str, widget: QWidget, tooltip: str = "") -> QLabel:
    label = make_form_label(text)
    if tooltip:
        label.setToolTip(tooltip)
        widget.setToolTip(tooltip)
        if hasattr(widget, "viewport"):
            try:
                widget.viewport().setToolTip(tooltip)  # type: ignore[call-arg]
            except Exception:
                pass
        if isinstance(widget, QComboBox) and widget.lineEdit():
            widget.lineEdit().setToolTip(tooltip)
    form.addRow(label, widget)
    return label


def card_frame(object_name: str | None = None) -> QFrame:
    frame = QFrame()
    frame.setProperty("card", True)
    frame.setMinimumWidth(0)
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    if object_name:
        frame.setObjectName(object_name)
    _style_refresh(frame)
    return frame


class ScrollRelayFilter(QObject):
    def __init__(self, scroll_area: QScrollArea) -> None:
        super().__init__(scroll_area)
        self.scroll_area = scroll_area

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Wheel:
            if QApplication.activePopupWidget() is not None:
                return False
            if isinstance(watched, (QTreeWidget, QTableWidget)) and isinstance(watched, QWidget) and watched.hasFocus():
                return False
            if isinstance(watched, (QPlainTextEdit, QTextBrowser)) and isinstance(watched, QWidget) and watched.hasFocus():
                return False
            bar = self.scroll_area.verticalScrollBar()
            delta = event.angleDelta().y() if hasattr(event, "angleDelta") else 0
            if delta:
                steps = int(delta / 120) if abs(delta) >= 120 else (1 if delta > 0 else -1)
                bar.setValue(bar.value() - (steps * max(36, bar.singleStep() * 3)))
                return True
        return super().eventFilter(watched, event)


def wrap_scroll(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setWidget(widget)
    scroll.setProperty("pageScroll", True)
    relay = ScrollRelayFilter(scroll)
    scroll._scroll_relay_filter = relay  # type: ignore[attr-defined]
    widget.installEventFilter(relay)
    for child in widget.findChildren(QWidget):
        child.installEventFilter(relay)
    return scroll


class TaskSignals(QObject):
    success = Signal(object)
    error = Signal(str, str)
    progress = Signal(int, str)
    finished = Signal()


class BackgroundTask(QRunnable):
    def __init__(self, fn: Any) -> None:
        super().__init__()
        self.fn = fn
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            signature = inspect.signature(self.fn)
            if len(signature.parameters) >= 1:
                result = self.fn(self.signals.progress.emit)
            else:
                result = self.fn()
        except Exception as exc:
            try:
                self.signals.error.emit(str(exc), traceback.format_exc())
            except RuntimeError:
                return
        else:
            try:
                self.signals.success.emit(result)
            except RuntimeError:
                return
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass


class LocalWebAdminServer:
    def __init__(self, state_root: Path, resource_root: Path, host: str = "127.0.0.1", port: int = 8787) -> None:
        self.state_root = state_root
        self.resource_root = resource_root
        self.host = host
        self.port = port
        self._server: Any = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> str:
        if self._thread and self._thread.is_alive():
            return self.url
        from .app import create_app

        app = create_app(self.state_root, resource_root=self.resource_root)
        self._server = make_server(self.host, self.port, app)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


class VpsDashWindow(QMainWindow):
    PAGE_LABELS = ["Overview", "Activity", "Resources", "Doplets"]
    PAGE_OVERVIEW = 0
    PAGE_ACTIVITY = 1
    PAGE_RESOURCES = 2
    PAGE_HARMINOPLETS = 3

    def __init__(self, service: VpsDashService) -> None:
        super().__init__()
        self.service = service
        self.bootstrap_data: dict[str, Any] = {}
        self.templates: list[dict[str, Any]] = []
        self.defaults: list[dict[str, Any]] = []
        self.hosts: list[dict[str, Any]] = []
        self.projects: list[dict[str, Any]] = []
        self.instances: list[dict[str, Any]] = []
        self.local_machine: dict[str, Any] = {}
        self.current_host: dict[str, Any] | None = None
        self.current_project: dict[str, Any] | None = None
        self.current_instance: dict[str, Any] | None = None
        self.current_plan: dict[str, Any] | None = None
        self.current_native_doplet_id: int | None = None
        self.selected_default: dict[str, Any] | None = None
        self.nav_buttons: list[QPushButton] = []
        self.task_buttons: list[QPushButton] = []
        self.thread_pool = QThreadPool.globalInstance()
        self.active_tasks: list[BackgroundTask] = []
        self.busy_tasks = 0
        self._live_updates_suspended = False
        self._default_status_text = "Ready"
        self._responsive_boxes: list[dict[str, Any]] = []
        self._status_reset_timer = QTimer(self)
        self._status_reset_timer.setSingleShot(True)
        self._status_reset_timer.timeout.connect(self._clear_status)
        self._form_refresh_timer = QTimer(self)
        self._form_refresh_timer.setSingleShot(True)
        self._form_refresh_timer.setInterval(120)
        self._form_refresh_timer.timeout.connect(self._apply_form_refresh)
        self._prefill_timer = QTimer(self)
        self._prefill_timer.setSingleShot(True)
        self._prefill_timer.timeout.connect(self._run_next_prefill_step)
        self._prefill_steps: list[tuple[str, Any]] = []
        self._control_plane_height_timer = QTimer(self)
        self._control_plane_height_timer.setInterval(1200)
        self._control_plane_height_timer.timeout.connect(self._refresh_control_plane_height)
        self._task_poll_timer = QTimer(self)
        self._task_poll_timer.setInterval(1600)
        self._task_poll_timer.timeout.connect(self._poll_control_plane_state)
        self._layout_audit_timer = QTimer(self)
        self._layout_audit_timer.setSingleShot(True)
        self._layout_audit_timer.timeout.connect(self._run_layout_audit)
        self._last_layout_issues: list[str] = []
        self._error_events: list[str] = []
        self._bootstrap_poll_inflight = False
        self._watched_task_roots: set[int] = set()
        self._auto_retry_attempts: dict[int, int] = {}
        self._auto_retry_max_attempts = 3
        self.web_admin_server = LocalWebAdminServer(_state_root(), _resource_root())
        self.control_plane_view: Any | None = None
        self.control_plane_placeholder: QTextBrowser | None = None
        self.control_plane_panel_layout: QVBoxLayout | None = None

        self.setWindowTitle("VPSdash")
        self.resize(1520, 980)
        self.setMinimumSize(1280, 860)
        self._build_ui()
        self._wire_live_updates()
        self._schedule_layout_audit()
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            self._load_bootstrap()
        else:
            self._set_status("Loading control plane...", 0)
            QTimer.singleShot(0, self._load_bootstrap_async)

    def _register_responsive_box(
        self,
        layout: QBoxLayout,
        *,
        breakpoint: int,
        vertical_direction: QBoxLayout.Direction = QBoxLayout.TopToBottom,
    ) -> QBoxLayout:
        self._responsive_boxes.append(
            {
                "layout": layout,
                "breakpoint": breakpoint,
                "vertical_direction": vertical_direction,
            }
        )
        return layout

    def _clear_layout_items(self, layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            if child_layout is not None:
                self._clear_layout_items(child_layout)

    def _reflow_grid(self, layout: QGridLayout, widgets: list[QWidget], *, columns: int) -> None:
        columns = max(1, columns)
        self._clear_layout_items(layout)
        for index, widget in enumerate(widgets):
            row = index // columns
            column = index % columns
            layout.addWidget(widget, row, column)
        for column in range(columns):
            layout.setColumnStretch(column, 1)

    def _schedule_layout_audit(self) -> None:
        self._layout_audit_timer.start(0)

    def _sync_wrapped_label_heights(self) -> None:
        root = self.centralWidget()
        if root is None:
            return
        for label in root.findChildren(AutoHeightLabel):
            if label.isVisible():
                label.sync_height()

    def _collect_layout_issues(self) -> list[str]:
        issues: list[str] = []
        root = self.centralWidget()
        if root is None:
            return issues
        for label in root.findChildren(AutoHeightLabel):
            if not label.isVisibleTo(self):
                continue
            available_width = max(1, label.contentsRect().width() or label.width())
            required_height = label.heightForWidth(available_width)
            if label.height() + 2 < required_height:
                snippet = label.text().replace("\n", " ").strip()[:48] or label.objectName() or label.__class__.__name__
                issues.append(f"Label clipped: {snippet}")
        for button in root.findChildren(QPushButton):
            if not button.isVisibleTo(self):
                continue
            metrics = QFontMetrics(button.font())
            required_height = max(button.minimumHeight(), metrics.height() + 18)
            if button.height() + 2 < required_height:
                issues.append(f"Button clipped: {button.text()[:48]}")
        for frame in root.findChildren(QFrame):
            if not frame.property("card"):
                continue
            children = [
                child
                for child in frame.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
                if child.isVisibleTo(self) and child.geometry().width() > 0 and child.geometry().height() > 0
            ]
            for index, first in enumerate(children):
                for second in children[index + 1 :]:
                    intersection = first.geometry().intersected(second.geometry())
                    if intersection.isEmpty():
                        continue
                    if intersection.width() * intersection.height() < 24:
                        continue
                    first_name = getattr(first, "text", lambda: first.objectName() or first.__class__.__name__)()
                    second_name = getattr(second, "text", lambda: second.objectName() or second.__class__.__name__)()
                    frame_name = frame.objectName() or "card"
                    issues.append(f"Overlap in {frame_name}: {str(first_name)[:32]} / {str(second_name)[:32]}")
                    break
                else:
                    continue
                break
        return issues

    def _run_layout_audit(self) -> None:
        root = self.centralWidget()
        if root is None:
            return
        self._sync_wrapped_label_heights()
        root.updateGeometry()
        if root.layout() is not None:
            root.layout().activate()
        self._last_layout_issues = self._collect_layout_issues()
        if self._last_layout_issues:
            _write_startup_log("Layout audit issues: " + " | ".join(self._last_layout_issues))

    def _build_ui(self) -> None:
        root = QFrame()
        root.setObjectName("RootFrame")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        root_layout.addWidget(self._build_sidebar())
        root_layout.addWidget(self._build_content_shell(), 1)

        save_action = QAction("Save current host/project", self)
        save_action.triggered.connect(self._save_all)
        self.addAction(save_action)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("SidebarFrame")
        sidebar.setFixedWidth(292)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(18)

        brand = QFrame()
        brand.setObjectName("SidebarBrand")
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(18, 18, 18, 18)
        brand_layout.setSpacing(8)

        brand_kicker = QLabel("DESKTOP CONTROL PLANE")
        brand_kicker.setProperty("class", "SidebarKicker")
        brand_layout.addWidget(brand_kicker)

        title = QLabel("VPSdash")
        title.setObjectName("SidebarTitle")
        brand_layout.addWidget(title)

        subtitle = make_wrap_label(
            "Launch the local control plane, manage hosts, and keep self-hosted operations in one native workspace."
        )
        subtitle.setObjectName("SidebarBody")
        brand_layout.addWidget(subtitle)
        layout.addWidget(brand)

        nav_label = QLabel("WORKSPACE")
        nav_label.setProperty("class", "SidebarSection")
        layout.addWidget(nav_label)

        nav_box = QFrame()
        nav_box.setObjectName("SidebarNavBox")
        nav_layout = QVBoxLayout(nav_box)
        nav_layout.setContentsMargins(4, 0, 4, 6)
        nav_layout.setSpacing(12)
        for index, label in enumerate(self.PAGE_LABELS):
            button = make_nav_button(label)
            button.clicked.connect(lambda _checked=False, idx=index: self._switch_page(idx))
            nav_layout.addWidget(button)
            self.nav_buttons.append(button)
        layout.addWidget(nav_box)
        layout.addSpacing(10)

        tools_label = QLabel("QUICK ACTIONS")
        tools_label.setProperty("class", "SidebarSection")
        layout.addWidget(tools_label)

        tools = QFrame()
        tools.setObjectName("SidebarToolsCard")
        tools_layout = QVBoxLayout(tools)
        tools_layout.setContentsMargins(18, 18, 18, 18)
        tools_layout.setSpacing(16)

        initial_setup = make_button("Initial Setup", "primary")
        initial_setup.clicked.connect(self._run_initial_setup)
        tools_layout.addWidget(initial_setup)

        self.initial_setup_progress = QProgressBar()
        self.initial_setup_progress.setRange(0, 100)
        self.initial_setup_progress.setValue(0)
        self.initial_setup_progress.setTextVisible(True)
        self.initial_setup_progress.setFormat("Setup idle")
        self.initial_setup_progress.setObjectName("InitialSetupProgress")
        tools_layout.addWidget(self.initial_setup_progress)

        self.initial_setup_status = make_wrap_label("Initial setup has not run yet.")
        self.initial_setup_status.setObjectName("InitialSetupStatus")
        tools_layout.addWidget(self.initial_setup_status)

        open_host_admin = make_button("Open Host Admin", "secondary")
        open_host_admin.clicked.connect(self._open_host_admin)
        tools_layout.addWidget(open_host_admin)

        open_doplet_admin = make_button("Manage Doplets", "secondary")
        open_doplet_admin.clicked.connect(self._open_doplet_admin)
        tools_layout.addWidget(open_doplet_admin)

        open_browser_admin = make_button("Browser Admin (Optional)", "ghost")
        open_browser_admin.clicked.connect(self._open_web_admin)
        tools_layout.addWidget(open_browser_admin)

        refresh_button = make_button("Refresh State", "ghost")
        refresh_button.clicked.connect(self._load_bootstrap)
        tools_layout.addWidget(refresh_button)
        layout.addWidget(tools)

        layout.addStretch(1)

        footer = make_wrap_label(
            "Remote Linux, local Linux, and Windows + WSL workflows stay in the same surface."
        )
        footer.setObjectName("SidebarFooter")
        layout.addWidget(footer)

        self.sidebar_status = make_wrap_label(self._default_status_text)
        self.sidebar_status.setObjectName("SidebarStatus")
        layout.addWidget(self.sidebar_status)

        return sidebar

    def _build_content_shell(self) -> QWidget:
        content = QFrame()
        content.setObjectName("ContentFrame")
        shell = QHBoxLayout(content)
        shell.setContentsMargins(32, 28, 32, 24)
        shell.setSpacing(0)
        self.content_shell_layout = shell
        shell.addStretch(1)

        column = QFrame()
        column.setObjectName("ContentColumn")
        column.setMinimumWidth(0)
        column.setMaximumWidth(1720)
        column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(18)

        column_layout.addWidget(self._build_desktop_topbar())

        self.pages = QStackedWidget()
        self.pages.setObjectName("PageStack")
        self.legacy_setup_page = self._build_setup_page()
        self.legacy_plan_page = self._build_plan_page()
        self.pages.addWidget(self._build_overview_page())
        self.pages.addWidget(self._build_operations_page())
        self.pages.addWidget(self._build_resources_page())
        self.pages.addWidget(self.legacy_setup_page)
        column_layout.addWidget(self.pages, 1)

        shell.addWidget(column, 14)
        shell.addStretch(1)
        return content

    def _build_desktop_topbar(self) -> QWidget:
        topbar = QFrame()
        topbar.setObjectName("DesktopTopbar")
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(18)
        self._register_responsive_box(layout, breakpoint=1180)

        text_block = QVBoxLayout()
        text_block.setSpacing(4)
        kicker = QLabel("Native Control Plane")
        kicker.setProperty("class", "TopbarKicker")
        text_block.addWidget(kicker)

        self.desktop_topbar_title = QLabel("Overview")
        self.desktop_topbar_title.setObjectName("DesktopTopbarTitle")
        text_block.addWidget(self.desktop_topbar_title)

        self.desktop_topbar_body = make_wrap_label(
            "Launch the embedded admin, prepare hosts, and keep Doplet operations in one desktop shell."
        )
        self.desktop_topbar_body.setObjectName("DesktopTopbarBody")
        text_block.addWidget(self.desktop_topbar_body)
        layout.addLayout(text_block, 1)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        self._register_responsive_box(chip_row, breakpoint=980)
        self.desktop_topbar_host_chip = make_chip("HOST PENDING", "neutral")
        self.desktop_topbar_page_chip = make_chip("OVERVIEW", "accent")
        self.desktop_topbar_status_chip = make_chip("LOCAL ADMIN READY", "success")
        chip_row.addWidget(self.desktop_topbar_host_chip)
        chip_row.addWidget(self.desktop_topbar_page_chip)
        chip_row.addWidget(self.desktop_topbar_status_chip)
        chip_row.addStretch(1)
        layout.addLayout(chip_row, 1)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self._register_responsive_box(action_row, breakpoint=980)
        topbar_host = make_button("Host Admin", "secondary")
        topbar_host.clicked.connect(self._open_host_admin)
        topbar_doplets = make_button("Doplets", "secondary")
        topbar_doplets.clicked.connect(self._open_doplet_admin)
        topbar_refresh = make_button("Refresh", "ghost")
        topbar_refresh.clicked.connect(self._load_bootstrap)
        action_row.addWidget(topbar_host)
        action_row.addWidget(topbar_doplets)
        action_row.addWidget(topbar_refresh)
        layout.addLayout(action_row)
        return topbar

    def _build_overview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        hero = card_frame("HeroCard")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(30, 28, 30, 28)
        hero_layout.setSpacing(24)
        self._register_responsive_box(hero_layout, breakpoint=1180)

        hero_text = QVBoxLayout()
        hero_text.setSpacing(10)
        eyebrow = QLabel("NATIVE CONTROL CLIENT")
        eyebrow.setProperty("class", "PageKicker")
        hero_text.addWidget(eyebrow)

        title = make_wrap_label(
            "Launch the control plane, prepare hosts, and operate Doplets all from one dash.",
            object_name="HeroTitle",
        )
        hero_text.addWidget(title)

        body = make_wrap_label(
            "VPSdash keeps host setup, Doplet creation, diagnostics, backups, and the secure admin in one installable desktop surface."
        )
        body.setObjectName("HeroBody")
        hero_text.addWidget(body)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        self._register_responsive_box(chip_row, breakpoint=980)
        self.overview_mode_chip = make_chip("HOST PENDING", "neutral")
        self.overview_project_chip = make_chip("PROJECT PENDING", "neutral")
        self.overview_plan_chip = make_chip("PLAN NOT GENERATED", "warn")
        chip_row.addWidget(self.overview_mode_chip)
        chip_row.addWidget(self.overview_project_chip)
        chip_row.addWidget(self.overview_plan_chip)
        chip_row.addStretch(1)
        hero_text.addLayout(chip_row)
        hero_layout.addLayout(hero_text, 1)

        hero_actions = QVBoxLayout()
        hero_actions.setSpacing(10)
        overview_setup = make_button("Initial Setup", "primary")
        overview_setup.clicked.connect(self._run_initial_setup)
        hero_actions.addWidget(overview_setup)
        overview_host = make_button("Open Host Admin", "primary")
        overview_host.clicked.connect(self._open_host_admin)
        hero_actions.addWidget(overview_host)
        overview_doplets = make_button("Open Doplet Builder", "secondary")
        overview_doplets.clicked.connect(self._open_doplet_builder)
        hero_actions.addWidget(overview_doplets)
        overview_ops = make_button("Open Activity", "ghost")
        overview_ops.clicked.connect(lambda: self._switch_page(self.PAGE_ACTIVITY))
        hero_actions.addWidget(overview_ops)
        overview_control = make_button("Browser Admin (Optional)", "ghost")
        overview_control.clicked.connect(self._open_web_admin)
        hero_actions.addWidget(overview_control)
        hero_actions.addStretch(1)
        hero_layout.addLayout(hero_actions)
        layout.addWidget(hero)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(16)
        metrics.setVerticalSpacing(16)
        self.metrics_grid = metrics
        self.metric_templates = self._metric_card("HOSTS", "Configured host nodes.")
        self.metric_hosts = self._metric_card("HARMINOPLETS", "Managed droplets on known hosts.")
        self.metric_projects = self._metric_card("NETWORKS", "NAT, bridged, and private segments.")
        self.metric_steps = self._metric_card("TASKS", "Queued and recent platform work.")
        self.metric_cards = [
            self.metric_templates["frame"],
            self.metric_hosts["frame"],
            self.metric_projects["frame"],
            self.metric_steps["frame"],
        ]
        self._reflow_grid(metrics, self.metric_cards, columns=4)
        layout.addLayout(metrics)

        insight_grid = QGridLayout()
        insight_grid.setHorizontalSpacing(16)
        insight_grid.setVerticalSpacing(16)
        self.overview_insight_grid = insight_grid

        workspace = card_frame()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(22, 22, 22, 22)
        workspace_layout.setSpacing(12)
        workspace_layout.addWidget(make_card_title("Current host draft"))
        workspace_layout.addWidget(make_helper_text("Selected host mode, address, distro, and readiness."))
        self.overview_workspace_body = make_wrap_label(css_class="CardBody")
        workspace_layout.addWidget(self.overview_workspace_body)
        workspace_chips = QHBoxLayout()
        workspace_chips.setSpacing(8)
        self._register_responsive_box(workspace_chips, breakpoint=980)
        self.workspace_domain_chip = make_chip("DOMAINS MISSING", "warn")
        self.workspace_backup_chip = make_chip("BACKUPS UNKNOWN", "neutral")
        self.workspace_health_chip = make_chip("HEALTH CHECKS UNKNOWN", "neutral")
        workspace_chips.addWidget(self.workspace_domain_chip)
        workspace_chips.addWidget(self.workspace_backup_chip)
        workspace_chips.addWidget(self.workspace_health_chip)
        workspace_chips.addStretch(1)
        workspace_layout.addLayout(workspace_chips)

        next_actions = card_frame()
        next_layout = QVBoxLayout(next_actions)
        next_layout.setContentsMargins(22, 22, 22, 22)
        next_layout.setSpacing(12)
        next_layout.addWidget(make_card_title("Next actions"))
        next_layout.addWidget(make_helper_text("The shortest path from blank machine to a managed Doplet."))
        self.overview_next_steps = make_wrap_label(css_class="CardBody")
        next_layout.addWidget(self.overview_next_steps)

        template = card_frame()
        template_layout = QVBoxLayout(template)
        template_layout.setContentsMargins(22, 22, 22, 22)
        template_layout.setSpacing(12)
        template_layout.addWidget(make_card_title("Doplet manager"))
        template_layout.addWidget(make_helper_text("Where to set host capacity, vCPU, RAM, disk, networks, storage, GPU, and backup behavior."))
        self.overview_template_body = make_wrap_label(css_class="CardBody")
        template_layout.addWidget(self.overview_template_body)
        template_chips = QHBoxLayout()
        template_chips.setSpacing(8)
        self._register_responsive_box(template_chips, breakpoint=980)
        self.template_branch_chip = make_chip("BRANCH UNSPECIFIED", "neutral")
        self.template_build_chip = make_chip("NO BUILD STEPS", "neutral")
        self.template_storage_chip = make_chip("NO PERSISTENT PATHS", "neutral")
        template_chips.addWidget(self.template_branch_chip)
        template_chips.addWidget(self.template_build_chip)
        template_chips.addWidget(self.template_storage_chip)
        template_chips.addStretch(1)
        template_layout.addLayout(template_chips)

        coverage = card_frame()
        coverage_layout = QVBoxLayout(coverage)
        coverage_layout.setContentsMargins(22, 22, 22, 22)
        coverage_layout.setSpacing(12)
        coverage_layout.addWidget(make_card_title("Activity center"))
        coverage_layout.addWidget(make_helper_text("Diagnostics, tasks, snapshots, backups, and recent control-plane work."))
        self.overview_coverage_body = make_wrap_label(css_class="CardBody")
        coverage_layout.addWidget(self.overview_coverage_body)
        self.overview_insight_cards = [workspace, next_actions, template, coverage]
        self._reflow_grid(insight_grid, self.overview_insight_cards, columns=2)
        layout.addLayout(insight_grid)
        layout.addStretch(1)
        self.overview_scroll = wrap_scroll(page)
        return self.overview_scroll

    def _build_resources_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        header = card_frame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(28, 24, 28, 24)
        header_layout.setSpacing(24)
        self._register_responsive_box(header_layout, breakpoint=1180)

        header_text = QVBoxLayout()
        header_text.setSpacing(10)
        header_text.addWidget(make_section_title("Resources"))
        self.resources_context = make_wrap_label(
            "Track what the current machine is actually giving to active Doplets, then review remote-host capacity separately."
        )
        self.resources_context.setProperty("class", "CardBody")
        header_text.addWidget(self.resources_context)
        resource_chip_row = QHBoxLayout()
        resource_chip_row.setSpacing(8)
        self._register_responsive_box(resource_chip_row, breakpoint=980)
        self.resources_local_chip = make_chip("CURRENT MACHINE UNKNOWN", "neutral")
        self.resources_active_chip = make_chip("0 ACTIVE VPS", "neutral")
        self.resources_storage_chip = make_chip("ROOT UNKNOWN", "neutral")
        resource_chip_row.addWidget(self.resources_local_chip)
        resource_chip_row.addWidget(self.resources_active_chip)
        resource_chip_row.addWidget(self.resources_storage_chip)
        resource_chip_row.addStretch(1)
        header_text.addLayout(resource_chip_row)
        header_layout.addLayout(header_text, 1)

        header_actions = QVBoxLayout()
        header_actions.setSpacing(10)
        open_builder = make_button("Open Doplet Builder", "primary")
        open_builder.clicked.connect(self._open_doplet_builder)
        capture_inventory = make_button("Refresh Capacity", "secondary")
        capture_inventory.clicked.connect(self._capture_selected_or_local_platform_host_inventory)
        reclaim_runtime = make_button("Reclaim WSL Memory", "ghost")
        reclaim_runtime.clicked.connect(self._reclaim_selected_or_local_platform_host_runtime)
        refresh_button = make_button("Refresh State", "ghost")
        refresh_button.clicked.connect(self._load_bootstrap)
        self.task_buttons.extend([capture_inventory, reclaim_runtime, refresh_button])
        header_actions.addWidget(open_builder)
        header_actions.addWidget(capture_inventory)
        header_actions.addWidget(reclaim_runtime)
        header_actions.addWidget(refresh_button)
        header_actions.addStretch(1)
        header_layout.addLayout(header_actions)
        layout.addWidget(header)

        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(16)
        summary_grid.setVerticalSpacing(16)
        self.resources_summary_grid = summary_grid

        current_machine_card = card_frame()
        current_machine_layout = QVBoxLayout(current_machine_card)
        current_machine_layout.setContentsMargins(22, 22, 22, 22)
        current_machine_layout.setSpacing(12)
        current_machine_layout.addWidget(make_card_title("Current machine"))
        current_machine_layout.addWidget(make_helper_text("This section is only about the machine you are currently on."))
        self.current_machine_resources_body = make_wrap_label(css_class="CardBody")
        current_machine_layout.addWidget(self.current_machine_resources_body)

        root_card = card_frame()
        root_layout = QVBoxLayout(root_card)
        root_layout.setContentsMargins(22, 22, 22, 22)
        root_layout.setSpacing(12)
        root_layout.addWidget(make_card_title("Storage root"))
        root_layout.addWidget(make_helper_text("Where VPSdash is placing runtime disks, images, backups, and seeds on the current machine."))
        self.current_machine_storage_body = make_wrap_label(css_class="CardBody")
        root_layout.addWidget(self.current_machine_storage_body)

        self.resources_summary_cards = [current_machine_card, root_card]
        self._reflow_grid(summary_grid, self.resources_summary_cards, columns=2)
        layout.addLayout(summary_grid)

        resources_grid = QGridLayout()
        resources_grid.setHorizontalSpacing(16)
        resources_grid.setVerticalSpacing(16)
        self.resources_grid = resources_grid

        local_vps_card = card_frame()
        local_vps_layout = QVBoxLayout(local_vps_card)
        local_vps_layout.setContentsMargins(22, 22, 22, 22)
        local_vps_layout.setSpacing(12)
        local_vps_layout.addWidget(make_card_title("Active VPS on this machine"))
        local_vps_layout.addWidget(make_helper_text("Live and draft Doplets on local hosts appear here with the resources they take from this PC."))
        self.resources_local_doplets_tree = QTreeWidget()
        self.resources_local_doplets_tree.setColumnCount(6)
        self.resources_local_doplets_tree.setHeaderLabels(["Doplet", "Host", "Status", "CPU", "RAM", "Disk"])
        self.resources_local_doplets_tree.setRootIsDecorated(False)
        self.resources_local_doplets_tree.setMinimumHeight(280)
        self.resources_local_doplets_tree.itemSelectionChanged.connect(self._resources_doplet_selection_changed)
        local_header = self.resources_local_doplets_tree.header()
        local_header.setStretchLastSection(False)
        local_header.setSectionResizeMode(0, QHeaderView.Stretch)
        for idx in range(1, 6):
            local_header.setSectionResizeMode(idx, QHeaderView.ResizeToContents)
        local_vps_layout.addWidget(self.resources_local_doplets_tree, 1)
        self.resources_local_doplet_detail = QTextBrowser()
        self.resources_local_doplet_detail.setProperty("output", True)
        self.resources_local_doplet_detail.setMinimumHeight(180)
        local_vps_layout.addWidget(self.resources_local_doplet_detail)
        local_actions = QHBoxLayout()
        local_actions.setSpacing(10)
        self._register_responsive_box(local_actions, breakpoint=1180)
        self.resources_open_terminal_button = make_button("Open Terminal", "secondary")
        self.resources_open_terminal_button.clicked.connect(self._open_selected_resources_doplet_terminal)
        self.resources_show_ips_button = make_button("Show IPs", "ghost")
        self.resources_show_ips_button.clicked.connect(self._show_selected_resources_doplet_ips)
        self.resources_copy_ips_button = make_button("Copy IPs", "ghost")
        self.resources_copy_ips_button.clicked.connect(self._copy_selected_resources_doplet_ips)
        self.resources_copy_terminal_button = make_button("Copy Terminal", "ghost")
        self.resources_copy_terminal_button.clicked.connect(self._copy_selected_resources_doplet_terminal)
        self.resources_start_button = make_button("Start", "ghost")
        self.resources_start_button.clicked.connect(lambda: self._queue_resources_doplet_lifecycle("start"))
        self.resources_resize_button = make_button("Resize VPS", "ghost")
        self.resources_resize_button.clicked.connect(self._resize_selected_resources_doplet)
        self.resources_reprovision_button = make_button("Reprovision VPS", "ghost")
        self.resources_reprovision_button.clicked.connect(self._reprovision_selected_resources_doplet)
        self.resources_shutdown_button = make_button("Shutdown", "ghost")
        self.resources_shutdown_button.clicked.connect(lambda: self._queue_resources_doplet_lifecycle("shutdown"))
        self.resources_force_stop_button = make_button("Force Stop", "ghost")
        self.resources_force_stop_button.clicked.connect(lambda: self._queue_resources_doplet_lifecycle("force-stop"))
        self.resources_delete_button = make_button("Delete VPS", "ghost")
        self.resources_delete_button.clicked.connect(self._delete_selected_resources_doplet)
        self.task_buttons.extend(
            [
                self.resources_open_terminal_button,
                self.resources_show_ips_button,
                self.resources_copy_ips_button,
                self.resources_copy_terminal_button,
                self.resources_start_button,
                self.resources_resize_button,
                self.resources_reprovision_button,
                self.resources_shutdown_button,
                self.resources_force_stop_button,
                self.resources_delete_button,
            ]
        )
        local_actions.addWidget(self.resources_open_terminal_button)
        local_actions.addWidget(self.resources_show_ips_button)
        local_actions.addWidget(self.resources_copy_ips_button)
        local_actions.addWidget(self.resources_copy_terminal_button)
        local_actions.addWidget(self.resources_start_button)
        local_actions.addWidget(self.resources_resize_button)
        local_actions.addWidget(self.resources_reprovision_button)
        local_actions.addWidget(self.resources_shutdown_button)
        local_actions.addWidget(self.resources_force_stop_button)
        local_actions.addWidget(self.resources_delete_button)
        local_actions.addStretch(1)
        local_vps_layout.addLayout(local_actions)

        remote_hosts_card = card_frame()
        remote_hosts_layout = QVBoxLayout(remote_hosts_card)
        remote_hosts_layout.setContentsMargins(22, 22, 22, 22)
        remote_hosts_layout.setSpacing(12)
        remote_hosts_layout.addWidget(make_card_title("Remote host resources"))
        remote_hosts_layout.addWidget(make_helper_text("Remote hosts are shown separately so they do not get mixed with this machineâ€™s usage."))
        self.resources_remote_hosts_tree = QTreeWidget()
        self.resources_remote_hosts_tree.setColumnCount(5)
        self.resources_remote_hosts_tree.setHeaderLabels(["Host", "Mode", "Status", "CPU", "RAM"])
        self.resources_remote_hosts_tree.setRootIsDecorated(False)
        self.resources_remote_hosts_tree.setMinimumHeight(280)
        self.resources_remote_hosts_tree.itemSelectionChanged.connect(self._resources_remote_host_selection_changed)
        remote_header = self.resources_remote_hosts_tree.header()
        remote_header.setStretchLastSection(False)
        remote_header.setSectionResizeMode(0, QHeaderView.Stretch)
        for idx in range(1, 5):
            remote_header.setSectionResizeMode(idx, QHeaderView.ResizeToContents)
        remote_hosts_layout.addWidget(self.resources_remote_hosts_tree, 1)
        self.resources_remote_host_detail = QTextBrowser()
        self.resources_remote_host_detail.setProperty("output", True)
        self.resources_remote_host_detail.setMinimumHeight(180)
        remote_hosts_layout.addWidget(self.resources_remote_host_detail)

        self.resources_cards = [local_vps_card, remote_hosts_card]
        self._reflow_grid(resources_grid, self.resources_cards, columns=2)
        layout.addLayout(resources_grid)
        layout.addStretch(1)
        return wrap_scroll(page)

    def _build_setup_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        header = card_frame()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(28, 24, 28, 24)
        header_layout.setSpacing(10)
        header_layout.addWidget(make_section_title("Setup workspace"))
        header_layout.addWidget(
            make_helper_text(
                "Define the host, repo, domains, and environment once, then generate an opinionated deployment plan."
            )
        )
        setup_chip_row = QHBoxLayout()
        setup_chip_row.setSpacing(8)
        self._register_responsive_box(setup_chip_row, breakpoint=980)
        self.setup_host_chip = make_chip("HOST UNNAMED", "neutral")
        self.setup_project_chip = make_chip("PROJECT UNNAMED", "neutral")
        self.setup_template_chip = make_chip("TEMPLATE READY", "accent")
        setup_chip_row.addWidget(self.setup_host_chip)
        setup_chip_row.addWidget(self.setup_project_chip)
        setup_chip_row.addWidget(self.setup_template_chip)
        setup_chip_row.addStretch(1)
        header_layout.addLayout(setup_chip_row)
        self.setup_context_body = make_wrap_label(css_class="CardBody")
        header_layout.addWidget(self.setup_context_body)

        header_actions = QHBoxLayout()
        header_actions.setSpacing(10)
        header_actions.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self._register_responsive_box(header_actions, breakpoint=860)
        save_all = make_button("Save All", "secondary")
        save_all.clicked.connect(self._save_all)
        save_instance = make_button("Save Instance", "ghost")
        save_instance.clicked.connect(self._save_instance)
        generate_plan = make_button("Generate Plan", "primary")
        generate_plan.clicked.connect(self._generate_plan)
        header_actions.addWidget(save_all)
        header_actions.addWidget(save_instance)
        header_actions.addWidget(generate_plan)
        header_layout.addLayout(header_actions)
        layout.addWidget(header)

        quickstart_grid = QGridLayout()
        quickstart_grid.setHorizontalSpacing(16)
        quickstart_grid.setVerticalSpacing(16)
        self.setup_quickstart_grid = quickstart_grid

        local_host_card = card_frame()
        local_host_layout = QVBoxLayout(local_host_card)
        local_host_layout.setContentsMargins(22, 22, 22, 22)
        local_host_layout.setSpacing(12)
        local_host_layout.addWidget(make_card_title("Local host quick start"))
        local_host_layout.addWidget(
            make_helper_text(
                "Use this when the current machine will actually host Doplets. Windows host mode runs the Linux hypervisor stack inside WSL."
            )
        )
        self.local_host_quickstart_body = make_wrap_label(css_class="CardBody")
        local_host_layout.addWidget(self.local_host_quickstart_body)
        local_host_actions = QHBoxLayout()
        local_host_actions.setSpacing(10)
        self._register_responsive_box(local_host_actions, breakpoint=1080)
        use_windows_local = make_button("Use This Windows PC", "primary")
        use_windows_local.clicked.connect(self._configure_windows_local_host)
        use_linux_local = make_button("Use This Linux PC", "secondary")
        use_linux_local.clicked.connect(self._configure_linux_local_host)
        open_host_admin = make_button("Open Host Admin", "ghost")
        open_host_admin.clicked.connect(self._open_host_admin)
        local_host_actions.addWidget(use_windows_local)
        local_host_actions.addWidget(use_linux_local)
        local_host_actions.addWidget(open_host_admin)
        local_host_actions.addStretch(1)
        local_host_layout.addLayout(local_host_actions)

        doplet_card = card_frame()
        doplet_layout = QVBoxLayout(doplet_card)
        doplet_layout.setContentsMargins(22, 22, 22, 22)
        doplet_layout.setSpacing(12)
        doplet_layout.addWidget(make_card_title("Where Doplet size lives"))
        doplet_layout.addWidget(
            make_helper_text(
                "CPU, RAM, disk, image, network, storage backend, GPU assignments, and resize actions live in the Doplet admin workspace."
            )
        )
        self.doplet_builder_body = make_wrap_label(css_class="CardBody")
        doplet_layout.addWidget(self.doplet_builder_body)
        doplet_actions = QHBoxLayout()
        doplet_actions.setSpacing(10)
        self._register_responsive_box(doplet_actions, breakpoint=1080)
        save_and_open_builder = make_button("Save Host + Open Builder", "primary")
        save_and_open_builder.clicked.connect(self._save_host_and_open_doplet_admin)
        open_builder = make_button("Open Doplet Builder", "secondary")
        open_builder.clicked.connect(self._open_doplet_builder)
        open_builder_browser = make_button("Browser Admin (Optional)", "ghost")
        open_builder_browser.clicked.connect(self._open_web_admin)
        doplet_actions.addWidget(save_and_open_builder)
        doplet_actions.addWidget(open_builder)
        doplet_actions.addWidget(open_builder_browser)
        doplet_actions.addStretch(1)
        doplet_layout.addLayout(doplet_actions)

        self.setup_quickstart_cards = [local_host_card, doplet_card]
        self._reflow_grid(quickstart_grid, self.setup_quickstart_cards, columns=2)
        layout.addLayout(quickstart_grid)

        native_builder_card = card_frame()
        self.native_builder_card = native_builder_card
        native_builder_layout = QVBoxLayout(native_builder_card)
        native_builder_layout.setContentsMargins(22, 22, 22, 22)
        native_builder_layout.setSpacing(16)
        native_builder_layout.addWidget(make_section_title("Native Doplet builder"))
        native_builder_layout.addWidget(
            make_helper_text(
                "Create and manage a Doplet directly from the native app. Use small defaults first, then open a terminal once it is created."
            )
        )

        builder_grid = QGridLayout()
        builder_grid.setHorizontalSpacing(16)
        builder_grid.setVerticalSpacing(16)

        builder_form = QFormLayout()
        builder_form.setContentsMargins(0, 0, 0, 0)
        builder_form.setHorizontalSpacing(18)
        builder_form.setVerticalSpacing(14)
        builder_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.native_doplet_name = QLineEdit()
        self.native_doplet_name.setPlaceholderText("builder-01")
        self.native_doplet_slug = QLineEdit()
        self.native_doplet_slug.setPlaceholderText("builder-01")
        self.native_doplet_host = QComboBox()
        self.native_doplet_host.currentIndexChanged.connect(self._native_host_changed)
        self.native_doplet_image = QComboBox()
        self.native_doplet_flavor = QComboBox()
        self.native_doplet_flavor.currentIndexChanged.connect(self._native_flavor_changed)
        self.native_doplet_network = QComboBox()
        self.native_doplet_vcpu = QSpinBox()
        self.native_doplet_vcpu.setRange(1, 128)
        self.native_doplet_vcpu.setValue(1)
        self.native_doplet_ram = QSpinBox()
        self.native_doplet_ram.setRange(256, 1048576)
        self.native_doplet_ram.setSingleStep(256)
        self.native_doplet_ram.setValue(1024)
        self.native_doplet_disk = QSpinBox()
        self.native_doplet_disk.setRange(4, 4096)
        self.native_doplet_disk.setValue(20)
        self.native_doplet_storage = QComboBox()
        self.native_doplet_storage.addItem("Files / qcow2", "files")
        self.native_doplet_storage.addItem("ZFS", "zfs")
        self.native_doplet_storage.addItem("LVM-thin", "lvm-thin")
        self.native_doplet_auth_mode = QComboBox()
        self.native_doplet_auth_mode.addItem("SSH key only", "ssh")
        self.native_doplet_auth_mode.addItem("Password + SSH", "password+ssh")
        self.native_doplet_auth_mode.addItem("Password login", "password")
        self.native_doplet_auth_mode.currentIndexChanged.connect(self._native_auth_mode_changed)
        self.native_doplet_bootstrap_user = QLineEdit("ubuntu")
        self.native_doplet_bootstrap_password = QLineEdit()
        self.native_doplet_bootstrap_password.setText("bypass")
        self.native_doplet_bootstrap_password.setPlaceholderText("Default sudo password is bypass")
        self.native_doplet_bootstrap_password.setEchoMode(QLineEdit.Normal)
        self.native_doplet_keys = QPlainTextEdit()
        self.native_doplet_keys.setPlaceholderText("One public SSH key per line")
        self.native_doplet_keys.setFixedHeight(112)

        add_form_row(builder_form, "Name", self.native_doplet_name, "The display name for this VPS in VPSdash.")
        add_form_row(builder_form, "Slug", self.native_doplet_slug, "A machine-friendly identifier used in runtime names, files, and commands. Usually lowercase with dashes, like builder-01.")
        add_form_row(builder_form, "Host", self.native_doplet_host, "Which prepared machine will actually run this VPS.")
        add_form_row(builder_form, "Image", self.native_doplet_image, "The operating system image used for the first boot of the VPS.")
        add_form_row(builder_form, "Flavor", self.native_doplet_flavor, "A preset size for CPU, RAM, and disk. You can still override the exact numbers below.")
        add_form_row(builder_form, "Primary network", self.native_doplet_network, "The first network this VPS should attach to. For a simple first VPS, use the default NAT network.")
        add_form_row(builder_form, "vCPU", self.native_doplet_vcpu, "How many virtual CPU threads this VPS reserves from the host.")
        add_form_row(builder_form, "RAM (MB)", self.native_doplet_ram, "How much memory this VPS reserves from the host, in megabytes.")
        add_form_row(builder_form, "Disk (GB)", self.native_doplet_disk, "How much virtual disk space this VPS gets on the host SSD.")
        add_form_row(builder_form, "Storage", self.native_doplet_storage, "Where the VPS disk is backed on the host. Files / qcow2 is the simplest starting option.")
        add_form_row(builder_form, "Login method", self.native_doplet_auth_mode, "Choose whether SSH password login is disabled, enabled with keys, or password-only. SSH key only still keeps a local sudo password unless you change it.")
        add_form_row(builder_form, "Bootstrap user", self.native_doplet_bootstrap_user, "The first Linux username created inside the VPS for initial access.")
        add_form_row(builder_form, "Login / sudo password", self.native_doplet_bootstrap_password, "The bootstrap user's local password. In SSH key only mode this still becomes the default sudo/local login password, but SSH password login stays disabled.")
        add_form_row(builder_form, "SSH public keys", self.native_doplet_keys, "Paste one full public .pub key line per row. Never paste a private key here.")
        builder_grid.addLayout(builder_form, 0, 0)

        builder_side = QVBoxLayout()
        builder_side.setSpacing(12)
        builder_side.addWidget(make_card_title("Builder state"))
        builder_side.addWidget(make_helper_text("Selected Doplet summary, access posture, and next recommended action appear here."))
        self.native_doplet_summary = QTextBrowser()
        self.native_doplet_summary.setProperty("output", True)
        self.native_doplet_summary.setMinimumHeight(260)
        self.native_doplet_summary.setOpenExternalLinks(False)
        builder_side.addWidget(self.native_doplet_summary, 1)

        security_card = card_frame()
        security_layout = QVBoxLayout(security_card)
        security_layout.setContentsMargins(16, 16, 16, 16)
        security_layout.setSpacing(10)
        security_layout.addWidget(make_card_title("Current computer SSH key"))
        security_layout.addWidget(
            make_helper_text(
                "This app will not auto-insert your key into the VPS. Hover to reveal the current computer key. Click the field to copy it, then paste it into the SSH keys box only if you want to use it."
            )
        )
        self.current_ssh_key_label = make_wrap_label("No SSH public key found on this computer yet.", css_class="CardBody")
        security_layout.addWidget(self.current_ssh_key_label)
        self.current_ssh_key_field = HoverCopySecretField(on_copy=lambda: self._set_status("Current computer SSH key copied", 4000))
        security_layout.addWidget(self.current_ssh_key_field)
        self.current_ssh_key_path = make_wrap_label("", css_class="HelperText")
        security_layout.addWidget(self.current_ssh_key_path)
        builder_side.addWidget(security_card)
        builder_grid.addLayout(builder_side, 0, 1)
        builder_grid.setColumnStretch(0, 3)
        builder_grid.setColumnStretch(1, 2)
        native_builder_layout.addLayout(builder_grid)

        native_builder_actions = QHBoxLayout()
        native_builder_actions.setSpacing(10)
        self._register_responsive_box(native_builder_actions, breakpoint=1080)
        self.native_save_doplet_button = make_button("Save Draft", "secondary")
        self.native_save_doplet_button.clicked.connect(self._save_native_doplet)
        self.native_create_doplet_button = make_button("Create VPS", "primary")
        self.native_create_doplet_button.clicked.connect(self._create_native_doplet)
        self.native_show_ips_button = make_button("Show IPs", "ghost")
        self.native_show_ips_button.clicked.connect(self._show_selected_native_doplet_ips)
        self.native_copy_ips_button = make_button("Copy IPs", "ghost")
        self.native_copy_ips_button.clicked.connect(self._copy_selected_native_doplet_ips)
        self.native_open_terminal_button = make_button("Open Terminal", "ghost")
        self.native_open_terminal_button.clicked.connect(self._open_selected_native_doplet_terminal)
        self.native_copy_terminal_button = make_button("Copy Terminal Command", "ghost")
        self.native_copy_terminal_button.clicked.connect(self._copy_selected_native_doplet_terminal)
        self.task_buttons.extend(
            [
                self.native_save_doplet_button,
                self.native_create_doplet_button,
                self.native_show_ips_button,
                self.native_copy_ips_button,
                self.native_open_terminal_button,
                self.native_copy_terminal_button,
            ]
        )
        native_builder_actions.addWidget(self.native_save_doplet_button)
        native_builder_actions.addWidget(self.native_create_doplet_button)
        native_builder_actions.addWidget(self.native_show_ips_button)
        native_builder_actions.addWidget(self.native_copy_ips_button)
        native_builder_actions.addWidget(self.native_open_terminal_button)
        native_builder_actions.addWidget(self.native_copy_terminal_button)
        native_builder_actions.addStretch(1)
        native_builder_layout.addLayout(native_builder_actions)
        self.native_doplet_auth_mode.setCurrentIndex(0)
        self._native_auth_mode_changed()
        layout.addWidget(native_builder_card)

        defaults_card = card_frame()
        defaults_layout = QVBoxLayout(defaults_card)
        defaults_layout.setContentsMargins(22, 22, 22, 22)
        defaults_layout.setSpacing(14)
        defaults_layout.addWidget(make_section_title("Setup defaults"))
        defaults_layout.addWidget(
            make_helper_text(
                "Load a built-in default, save the current host + project form as your own reusable default, or add more built-ins through defaults/*.json."
            )
        )

        defaults_top = QHBoxLayout()
        defaults_top.setSpacing(10)
        self._register_responsive_box(defaults_top, breakpoint=1160)
        self.default_select = QComboBox()
        self.default_select.currentIndexChanged.connect(self._default_selection_changed)
        defaults_top.addWidget(self.default_select, 1)
        load_default = make_button("Load Default", "primary")
        load_default.clicked.connect(self._load_selected_default_into_form)
        save_default = make_button("Save As New Default", "secondary")
        save_default.clicked.connect(self._save_default_as_new)
        update_default = make_button("Update Selected Default", "ghost")
        update_default.clicked.connect(self._update_selected_default)
        self.default_load_button = load_default
        self.default_save_button = save_default
        self.default_update_button = update_default
        defaults_top.addWidget(load_default)
        defaults_top.addWidget(save_default)
        defaults_top.addWidget(update_default)
        defaults_layout.addLayout(defaults_top)

        defaults_meta = QHBoxLayout()
        defaults_meta.setSpacing(8)
        self._register_responsive_box(defaults_meta, breakpoint=980)
        self.default_kind_chip = make_chip("NO DEFAULT SELECTED", "neutral")
        self.default_source_chip = make_chip("PROJECT + HOST PRESET", "neutral")
        defaults_meta.addWidget(self.default_kind_chip)
        defaults_meta.addWidget(self.default_source_chip)
        defaults_meta.addStretch(1)
        defaults_layout.addLayout(defaults_meta)

        default_form = QFormLayout()
        default_form.setContentsMargins(0, 0, 0, 0)
        default_form.setHorizontalSpacing(18)
        default_form.setVerticalSpacing(14)
        default_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.default_name = QLineEdit()
        self.default_name.setPlaceholderText("My Basement Server Default")
        self.default_description = QLineEdit()
        self.default_description.setPlaceholderText("Remote Linux server with Docker stack bootstrap")
        default_form.addRow(make_form_label("Default name"), self.default_name)
        default_form.addRow(make_form_label("Description"), self.default_description)
        defaults_layout.addLayout(default_form)

        defaults_preview_grid = QGridLayout()
        defaults_preview_grid.setHorizontalSpacing(16)
        defaults_preview_grid.setVerticalSpacing(16)
        self.defaults_preview_grid = defaults_preview_grid

        default_preview_card = card_frame()
        default_preview_layout = QVBoxLayout(default_preview_card)
        default_preview_layout.setContentsMargins(18, 18, 18, 18)
        default_preview_layout.setSpacing(10)
        default_preview_layout.addWidget(make_card_title("What this default fills"))
        default_preview_layout.addWidget(
            make_helper_text("Host and project values that will be written into the form when you load the default.")
        )
        self.default_preview_output = QTextBrowser()
        self.default_preview_output.setMinimumWidth(0)
        self.default_preview_output.setMinimumHeight(220)
        self.default_preview_output.setProperty("output", True)
        self.default_preview_output.setOpenExternalLinks(False)
        default_preview_layout.addWidget(self.default_preview_output, 1)

        default_activity_card = card_frame()
        default_activity_layout = QVBoxLayout(default_activity_card)
        default_activity_layout.setContentsMargins(18, 18, 18, 18)
        default_activity_layout.setSpacing(10)
        default_activity_layout.addWidget(make_card_title("Fill activity"))
        default_activity_layout.addWidget(
            make_helper_text("When you load a default, fields are filled step by step here and stay editable afterward.")
        )
        self.default_activity_output = QTextBrowser()
        self.default_activity_output.setMinimumWidth(0)
        self.default_activity_output.setMinimumHeight(220)
        self.default_activity_output.setProperty("output", True)
        self.default_activity_output.setOpenExternalLinks(False)
        default_activity_layout.addWidget(self.default_activity_output, 1)
        self.defaults_preview_cards = [default_preview_card, default_activity_card]
        self._reflow_grid(defaults_preview_grid, self.defaults_preview_cards, columns=2)
        defaults_layout.addLayout(defaults_preview_grid)
        layout.addWidget(defaults_card)

        self.setup_cards_row = QHBoxLayout()
        self.setup_cards_row.setSpacing(16)

        host_card = card_frame()
        self.host_profile_card = host_card
        host_card.setMinimumWidth(0)
        host_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        host_layout = QVBoxLayout(host_card)
        host_layout.setContentsMargins(22, 22, 22, 22)
        host_layout.setSpacing(16)
        host_layout.addWidget(make_section_title("Host profile"))
        host_layout.addWidget(
            make_helper_text("Pick how commands should run, load a saved host profile, or save the current host draft here.")
        )

        host_top = QHBoxLayout()
        host_top.setSpacing(12)
        self._register_responsive_box(host_top, breakpoint=980)
        self.saved_host_select = QComboBox()
        self.saved_host_select.currentIndexChanged.connect(self._load_selected_host)
        host_top.addWidget(self.saved_host_select, 1)
        save_host = make_button("Save Host", "secondary")
        save_host.clicked.connect(self._save_host)
        host_top.addWidget(save_host)
        host_layout.addLayout(host_top)

        host_form = QFormLayout()
        host_form.setContentsMargins(0, 0, 0, 0)
        host_form.setHorizontalSpacing(18)
        host_form.setVerticalSpacing(14)
        host_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.host_name = QLineEdit()
        self.host_name.setPlaceholderText("Studio desktop, VPS-01, or Basement Node")
        self.host_mode = QComboBox()
        self.host_mode.addItems(["remote-linux", "linux-local", "windows-local", "windows-remote"])
        self.host_device_role = QComboBox()
        self.host_device_role.addItem("Computer A - main control machine", "computer-a-main")
        self.host_device_role.addItem("Computer B - server being prepared", "computer-b-server")
        self.host_bootstrap_auth = QComboBox()
        self.host_bootstrap_auth.addItem("Password bootstrap first", "password-bootstrap")
        self.host_bootstrap_auth.addItem("SSH key already ready", "ssh-key-ready")
        self.host_ssh_user = QLineEdit()
        self.host_ssh_user.setPlaceholderText("operator")
        self.host_ssh_host = QLineEdit()
        self.host_ssh_host.setPlaceholderText("164.92.64.157")
        self.host_ssh_port = QSpinBox()
        self.host_ssh_port.setRange(1, 65535)
        self.host_ssh_port.setValue(22)
        self.host_ssh_key = QComboBox()
        self.host_ssh_key.setEditable(True)
        if self.host_ssh_key.lineEdit():
            self.host_ssh_key.lineEdit().setPlaceholderText("C:/Users/you/.ssh/id_ed25519")
        self.host_wsl_distribution = QLineEdit("Ubuntu")
        self.host_wsl_distribution.setPlaceholderText("Ubuntu")

        add_form_row(host_form, "Host label", self.host_name, "A human-friendly name for this machine inside VPSdash.")
        add_form_row(host_form, "Execution mode", self.host_mode, "How VPSdash reaches and runs commands on this host: remote Linux, local Linux, local Windows + WSL, or remote Windows + WSL.")
        add_form_row(host_form, "This device role", self.host_device_role, "Whether this machine is your main control machine or the server machine being prepared.")
        add_form_row(host_form, "Bootstrap auth", self.host_bootstrap_auth, "How the first connection happens before permanent SSH access is ready.")
        add_form_row(host_form, "SSH user", self.host_ssh_user, "The username VPSdash should use when connecting to this host over SSH.")
        add_form_row(host_form, "SSH host / IP", self.host_ssh_host, "The hostname or IP address for this host when connecting remotely.")
        add_form_row(host_form, "SSH port", self.host_ssh_port, "The SSH port on the target host. Standard SSH uses 22.")
        add_form_row(host_form, "SSH key path", self.host_ssh_key, "Path to the private key file used to SSH into this host. This is a local path on the current machine.")
        add_form_row(host_form, "WSL distro", self.host_wsl_distribution, "Which WSL Linux distribution should host the hypervisor stack on a Windows machine.")
        host_layout.addLayout(host_form)

        self.host_mode_hint = make_helper_text("")
        host_layout.addWidget(self.host_mode_hint)

        host_actions = QHBoxLayout()
        host_actions.setSpacing(10)
        self._register_responsive_box(host_actions, breakpoint=980)
        diagnostics_button = make_button("Run Diagnostics", "ghost")
        diagnostics_button.clicked.connect(self._run_diagnostics)
        monitor_button = make_button("Capture Snapshot", "ghost")
        monitor_button.clicked.connect(self._run_monitor_snapshot)
        adopt_local = make_button("Use This Machine For Computer B", "secondary")
        adopt_local.clicked.connect(self._adopt_local_machine_for_server)
        self.task_buttons.extend([diagnostics_button, monitor_button, adopt_local])
        host_actions.addWidget(diagnostics_button)
        host_actions.addWidget(monitor_button)
        host_actions.addWidget(adopt_local)
        host_actions.addStretch(1)
        host_layout.addLayout(host_actions)
        self.setup_cards_row.addWidget(host_card, 5)

        project_card = card_frame()
        self.project_profile_card = project_card
        project_card.setMinimumWidth(0)
        project_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        project_layout = QVBoxLayout(project_card)
        project_layout.setContentsMargins(22, 22, 22, 22)
        project_layout.setSpacing(16)
        project_layout.addWidget(make_section_title("Project profile"))
        project_layout.addWidget(
            make_helper_text("Choose a template, load a saved project profile, or save the current project draft here.")
        )

        project_top = QHBoxLayout()
        project_top.setSpacing(12)
        self._register_responsive_box(project_top, breakpoint=980)
        self.saved_project_select = QComboBox()
        self.saved_project_select.currentIndexChanged.connect(self._load_selected_project)
        project_top.addWidget(self.saved_project_select, 1)
        save_project = make_button("Save Project", "secondary")
        save_project.clicked.connect(self._save_project)
        project_top.addWidget(save_project)
        project_layout.addLayout(project_top)

        project_form = QFormLayout()
        project_form.setContentsMargins(0, 0, 0, 0)
        project_form.setHorizontalSpacing(18)
        project_form.setVerticalSpacing(14)
        project_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.template_select = QComboBox()
        self.template_select.currentIndexChanged.connect(self._template_changed)
        self.apply_template_button = make_button("Apply Template Fields", "ghost")
        self.apply_template_button.clicked.connect(self._apply_selected_template)
        self.project_name = QLineEdit()
        self.project_name.setPlaceholderText("VPSdash or My Docker Stack")
        self.project_repo_url = QLineEdit()
        self.project_repo_url.setPlaceholderText("https://github.com/org/repo.git")
        self.project_branch = QLineEdit()
        self.project_branch.setPlaceholderText("main")
        self.project_deploy_path = QLineEdit()
        self.project_deploy_path.setPlaceholderText("~/apps/my-app")
        self.project_primary_domain = QLineEdit()
        self.project_primary_domain.setPlaceholderText("app.example.com")
        self.project_letsencrypt_email = QLineEdit()
        self.project_letsencrypt_email.setPlaceholderText("ops@example.com")
        self.project_domains = QPlainTextEdit()
        self.project_domains.setPlaceholderText("www.example.com\napi.example.com")
        self.project_domains.setFixedHeight(104)

        template_row = QWidget()
        template_row_layout = QHBoxLayout(template_row)
        template_row_layout.setContentsMargins(0, 0, 0, 0)
        template_row_layout.setSpacing(10)
        self._register_responsive_box(template_row_layout, breakpoint=980)
        template_row_layout.addWidget(self.template_select, 1)
        template_row_layout.addWidget(self.apply_template_button)

        project_form.addRow(make_form_label("Template"), template_row)
        project_form.addRow(make_form_label("Project name"), self.project_name)
        project_form.addRow(make_form_label("Repository URL"), self.project_repo_url)
        project_form.addRow(make_form_label("Branch"), self.project_branch)
        project_form.addRow(make_form_label("Deploy path"), self.project_deploy_path)
        project_form.addRow(make_form_label("Primary domain"), self.project_primary_domain)
        project_form.addRow(make_form_label("TLS email"), self.project_letsencrypt_email)
        project_form.addRow(make_form_label("Additional domains"), self.project_domains)
        project_layout.addLayout(project_form)

        self.project_template_hint = make_helper_text("")
        project_layout.addWidget(self.project_template_hint)
        self.setup_cards_row.addWidget(project_card, 7)

        layout.addLayout(self.setup_cards_row)

        remote_card = card_frame()
        self.remote_flow_card = remote_card
        remote_layout = QVBoxLayout(remote_card)
        remote_layout.setContentsMargins(22, 22, 22, 22)
        remote_layout.setSpacing(14)
        remote_layout.addWidget(make_section_title("Computer A / Computer B remote flow"))
        remote_layout.addWidget(
            make_helper_text(
                "Use this when Computer A is your main machine and Computer B is the machine you are turning into the server."
            )
        )
        remote_grid = QGridLayout()
        remote_grid.setHorizontalSpacing(16)
        remote_grid.setVerticalSpacing(16)
        self.remote_grid = remote_grid

        remote_guide_card = card_frame()
        remote_guide_layout = QVBoxLayout(remote_guide_card)
        remote_guide_layout.setContentsMargins(18, 18, 18, 18)
        remote_guide_layout.setSpacing(10)
        remote_guide_layout.addWidget(make_card_title("Remote setup guide"))
        self.remote_flow_output = QTextBrowser()
        self.remote_flow_output.setMinimumWidth(0)
        self.remote_flow_output.setMinimumHeight(220)
        self.remote_flow_output.setProperty("output", True)
        self.remote_flow_output.setOpenExternalLinks(False)
        remote_guide_layout.addWidget(self.remote_flow_output, 1)

        remote_packet_card = card_frame()
        remote_packet_layout = QVBoxLayout(remote_packet_card)
        remote_packet_layout.setContentsMargins(18, 18, 18, 18)
        remote_packet_layout.setSpacing(10)
        remote_packet_layout.addWidget(make_card_title("Connection packet for Computer A"))
        remote_packet_actions = QHBoxLayout()
        remote_packet_actions.setSpacing(10)
        remote_packet_actions.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self._register_responsive_box(remote_packet_actions, breakpoint=980)
        self.copy_ssh_button = make_button("Copy SSH Command", "ghost")
        self.copy_ssh_button.clicked.connect(self._copy_remote_ssh_command)
        self.copy_packet_button = make_button("Copy Packet", "ghost")
        self.copy_packet_button.clicked.connect(self._copy_remote_packet)
        remote_packet_actions.addWidget(self.copy_ssh_button)
        remote_packet_actions.addWidget(self.copy_packet_button)
        remote_packet_layout.addLayout(remote_packet_actions)
        self.remote_packet_output = QTextBrowser()
        self.remote_packet_output.setMinimumWidth(0)
        self.remote_packet_output.setMinimumHeight(260)
        self.remote_packet_output.setProperty("output", True)
        self.remote_packet_output.setOpenExternalLinks(False)
        remote_packet_layout.addWidget(self.remote_packet_output, 1)
        self.remote_cards = [remote_guide_card, remote_packet_card]
        self._reflow_grid(remote_grid, self.remote_cards, columns=2)
        remote_layout.addLayout(remote_grid)
        layout.addWidget(remote_card)

        env_card = card_frame()
        self.env_card = env_card
        env_layout = QVBoxLayout(env_card)
        env_layout.setContentsMargins(22, 22, 22, 22)
        env_layout.setSpacing(16)
        env_layout.addWidget(make_section_title("Environment variables"))
        env_layout.addWidget(
            make_helper_text("Track runtime variables in one table. Mark secrets and required fields instead of encoding that in notes.")
        )

        env_header = QHBoxLayout()
        env_header.setSpacing(10)
        env_header.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self._register_responsive_box(env_header, breakpoint=860)
        add_env = make_button("Add Variable", "ghost")
        add_env.clicked.connect(self._add_env_row)
        remove_env = make_button("Remove Selected", "ghost")
        remove_env.clicked.connect(self._remove_selected_env_rows)
        env_header.addWidget(add_env)
        env_header.addWidget(remove_env)
        env_layout.addLayout(env_header)

        self.env_table = QTableWidget(0, 5)
        self.env_table.setMinimumWidth(0)
        self.env_table.setHorizontalHeaderLabels(["Key", "Label", "Value", "Secret", "Required"])
        self.env_table.verticalHeader().setVisible(False)
        self.env_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.env_table.setSelectionMode(QTableWidget.SingleSelection)
        self.env_table.setAlternatingRowColors(False)
        self.env_table.setShowGrid(False)
        self.env_table.setMinimumHeight(280)
        env_header_view = self.env_table.horizontalHeader()
        env_header_view.setStretchLastSection(False)
        env_header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        env_header_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        env_header_view.setSectionResizeMode(2, QHeaderView.Stretch)
        env_header_view.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        env_header_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        env_layout.addWidget(self.env_table)
        layout.addWidget(env_card)

        layout.addStretch(1)
        self.setup_scroll = wrap_scroll(content)
        return self.setup_scroll

    def _build_plan_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        header = card_frame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(28, 24, 28, 24)
        header_layout.setSpacing(24)
        self._register_responsive_box(header_layout, breakpoint=1180)

        header_text = QVBoxLayout()
        header_text.setSpacing(10)
        header_text.addWidget(make_section_title("Deployment plan"))
        self.plan_summary = make_wrap_label(
            "Generate a plan from Setup to stage the host, repo, env, proxy, deploy, backup, and verification steps.",
            css_class="CardBody",
        )
        header_text.addWidget(self.plan_summary)

        plan_chip_row = QHBoxLayout()
        plan_chip_row.setSpacing(8)
        self._register_responsive_box(plan_chip_row, breakpoint=980)
        self.plan_mode_chip = make_chip("MODE UNKNOWN", "neutral")
        self.plan_warning_chip = make_chip("NO PLAN", "warn")
        self.plan_step_chip = make_chip("0 STEPS", "neutral")
        plan_chip_row.addWidget(self.plan_mode_chip)
        plan_chip_row.addWidget(self.plan_warning_chip)
        plan_chip_row.addWidget(self.plan_step_chip)
        plan_chip_row.addStretch(1)
        header_text.addLayout(plan_chip_row)
        header_layout.addLayout(header_text, 1)

        header_actions = QVBoxLayout()
        header_actions.setSpacing(10)
        dry_button = make_button("Dry Run Plan", "secondary")
        dry_button.clicked.connect(lambda: self._execute_plan(True))
        execute_button = make_button("Execute Live Plan", "primary")
        execute_button.clicked.connect(lambda: self._execute_plan(False))
        self.task_buttons.extend([dry_button, execute_button])
        header_actions.addWidget(dry_button)
        header_actions.addWidget(execute_button)
        header_actions.addStretch(1)
        header_layout.addLayout(header_actions)
        layout.addWidget(header)

        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(16)
        summary_grid.setVerticalSpacing(16)
        self.plan_summary_grid = summary_grid

        target_card = card_frame()
        target_layout = QVBoxLayout(target_card)
        target_layout.setContentsMargins(22, 22, 22, 22)
        target_layout.setSpacing(12)
        target_layout.addWidget(make_card_title("Plan target"))
        target_layout.addWidget(make_helper_text("Host mode, deploy path, repo branch, and live execution shell."))
        self.plan_target_body = make_wrap_label(css_class="CardBody")
        target_layout.addWidget(self.plan_target_body)

        warning_card = card_frame("WarningCard")
        warning_layout = QVBoxLayout(warning_card)
        warning_layout.setContentsMargins(22, 22, 22, 22)
        warning_layout.setSpacing(12)
        warning_layout.addWidget(make_card_title("Warnings and blockers"))
        warning_layout.addWidget(make_helper_text("Fix missing host, repo, or domain details before live execution."))
        self.warning_box = QTextBrowser()
        self.warning_box.setMinimumWidth(0)
        self.warning_box.setProperty("role", "warning")
        self.warning_box.setOpenExternalLinks(False)
        self.warning_box.setMinimumHeight(160)
        _style_refresh(self.warning_box)
        warning_layout.addWidget(self.warning_box, 1)
        self.plan_summary_cards = [target_card, warning_card]
        self._reflow_grid(summary_grid, self.plan_summary_cards, columns=2)
        layout.addLayout(summary_grid)

        tree_card = card_frame()
        tree_layout = QVBoxLayout(tree_card)
        tree_layout.setContentsMargins(22, 22, 22, 22)
        tree_layout.setSpacing(12)
        tree_layout.addWidget(make_card_title("Generated steps"))
        tree_layout.addWidget(make_helper_text("Each stage expands to concrete commands so dry runs and live runs stay inspectable."))
        self.plan_tree = QTreeWidget()
        self.plan_tree.setMinimumWidth(0)
        self.plan_tree.setColumnCount(3)
        self.plan_tree.setHeaderLabels(["Stage / Step", "Detail", "Command"])
        self.plan_tree.setRootIsDecorated(True)
        self.plan_tree.setMinimumHeight(420)
        plan_header = self.plan_tree.header()
        plan_header.setStretchLastSection(False)
        plan_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        plan_header.setSectionResizeMode(1, QHeaderView.Stretch)
        plan_header.setSectionResizeMode(2, QHeaderView.Stretch)
        tree_layout.addWidget(self.plan_tree, 1)
        layout.addWidget(tree_card, 1)
        return page

    def _build_operations_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        header = card_frame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(28, 24, 28, 24)
        header_layout.setSpacing(24)
        self._register_responsive_box(header_layout, breakpoint=1180)

        header_text = QVBoxLayout()
        header_text.setSpacing(10)
        header_text.addWidget(make_section_title("Activity"))
        self.operations_context = make_wrap_label(
            "Review diagnostics, tasks, snapshots, backups, and execution logs without leaving the control plane."
        )
        self.operations_context.setProperty("class", "CardBody")
        header_text.addWidget(self.operations_context)
        ops_chip_row = QHBoxLayout()
        ops_chip_row.setSpacing(8)
        self._register_responsive_box(ops_chip_row, breakpoint=980)
        self.ops_host_chip = make_chip("NO HOST", "warn")
        self.ops_project_chip = make_chip("NO PROJECT", "warn")
        self.ops_plan_chip = make_chip("PLAN NOT READY", "warn")
        ops_chip_row.addWidget(self.ops_host_chip)
        ops_chip_row.addWidget(self.ops_project_chip)
        ops_chip_row.addWidget(self.ops_plan_chip)
        ops_chip_row.addStretch(1)
        header_text.addLayout(ops_chip_row)
        header_layout.addLayout(header_text, 1)

        header_actions = QVBoxLayout()
        header_actions.setSpacing(10)
        open_builder = make_button("Open Doplet Builder", "primary")
        open_builder.clicked.connect(self._open_doplet_builder)
        open_host_admin = make_button("Open Host Admin", "secondary")
        open_host_admin.clicked.connect(self._open_host_admin)
        run_diag = make_button("Run Diagnostics", "ghost")
        run_diag.clicked.connect(self._run_diagnostics)
        run_snap = make_button("Capture Snapshot", "ghost")
        run_snap.clicked.connect(self._run_monitor_snapshot)
        run_dry = make_button("Dry Run Plan", "secondary")
        run_dry.clicked.connect(lambda: self._execute_plan(True))
        self.task_buttons.extend([run_diag, run_snap, run_dry])
        header_actions.addWidget(open_builder)
        header_actions.addWidget(open_host_admin)
        header_actions.addWidget(run_diag)
        header_actions.addWidget(run_snap)
        header_actions.addWidget(run_dry)
        header_actions.addStretch(1)
        header_layout.addLayout(header_actions)
        layout.addWidget(header)

        management_grid = QGridLayout()
        management_grid.setHorizontalSpacing(16)
        management_grid.setVerticalSpacing(16)
        self.instance_management_grid = management_grid

        instance_list_card = card_frame()
        instance_list_layout = QVBoxLayout(instance_list_card)
        instance_list_layout.setContentsMargins(22, 22, 22, 22)
        instance_list_layout.setSpacing(12)
        instance_list_layout.addWidget(make_card_title("Managed instances"))
        instance_list_layout.addWidget(
            make_helper_text("Instances combine a host snapshot, a project snapshot, and backup history into one manageable deployment record.")
        )
        self.instances_tree = QTreeWidget()
        self.instances_tree.setMinimumWidth(0)
        self.instances_tree.setColumnCount(4)
        self.instances_tree.setHeaderLabels(["Instance", "Host", "Project", "Backups"])
        self.instances_tree.setRootIsDecorated(False)
        self.instances_tree.setMinimumHeight(260)
        self.instances_tree.itemSelectionChanged.connect(self._instance_selection_changed)
        instance_tree_header = self.instances_tree.header()
        instance_tree_header.setStretchLastSection(False)
        instance_tree_header.setSectionResizeMode(0, QHeaderView.Stretch)
        instance_tree_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        instance_tree_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        instance_tree_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        instance_list_layout.addWidget(self.instances_tree, 1)

        instance_actions = QHBoxLayout()
        instance_actions.setSpacing(10)
        self._register_responsive_box(instance_actions, breakpoint=1080)
        save_instance_button = make_button("Save Current as Instance", "secondary")
        save_instance_button.clicked.connect(self._save_instance)
        update_instance_button = make_button("Update Selected", "ghost")
        update_instance_button.clicked.connect(self._update_selected_instance)
        load_instance_button = make_button("Load Draft", "ghost")
        load_instance_button.clicked.connect(self._load_selected_instance)
        self.save_instance_button = save_instance_button
        self.update_instance_button = update_instance_button
        self.load_instance_button = load_instance_button
        instance_actions.addWidget(save_instance_button)
        instance_actions.addWidget(update_instance_button)
        instance_actions.addWidget(load_instance_button)
        instance_actions.addStretch(1)
        instance_list_layout.addLayout(instance_actions)

        instance_detail_card = card_frame()
        instance_detail_layout = QVBoxLayout(instance_detail_card)
        instance_detail_layout.setContentsMargins(22, 22, 22, 22)
        instance_detail_layout.setSpacing(12)
        instance_detail_layout.addWidget(make_card_title("Instance management"))
        instance_detail_layout.addWidget(
            make_helper_text("Review the selected instance, create a backup, or remove the managed record when you no longer need it.")
        )
        self.instance_detail_output = QTextBrowser()
        self.instance_detail_output.setMinimumWidth(0)
        self.instance_detail_output.setMinimumHeight(180)
        self.instance_detail_output.setProperty("output", True)
        self.instance_detail_output.setOpenExternalLinks(False)
        instance_detail_layout.addWidget(self.instance_detail_output, 1)

        backup_actions = QHBoxLayout()
        backup_actions.setSpacing(10)
        self._register_responsive_box(backup_actions, breakpoint=980)
        backup_instance_button = make_button("Create Backup Now", "primary")
        backup_instance_button.clicked.connect(self._create_backup_for_selected_instance)
        delete_instance_button = make_button("Delete Instance", "ghost")
        delete_instance_button.clicked.connect(self._delete_selected_instance)
        self.backup_instance_button = backup_instance_button
        self.delete_instance_button = delete_instance_button
        self.task_buttons.append(backup_instance_button)
        backup_actions.addWidget(backup_instance_button)
        backup_actions.addWidget(delete_instance_button)
        backup_actions.addStretch(1)
        instance_detail_layout.addLayout(backup_actions)

        self.instance_management_cards = [instance_list_card, instance_detail_card]
        self._reflow_grid(management_grid, self.instance_management_cards, columns=2)
        layout.addLayout(management_grid)

        platform_manager_grid = QGridLayout()
        platform_manager_grid.setHorizontalSpacing(16)
        platform_manager_grid.setVerticalSpacing(16)
        self.native_manager_grid = platform_manager_grid

        native_hosts_card = card_frame()
        native_hosts_layout = QVBoxLayout(native_hosts_card)
        native_hosts_layout.setContentsMargins(22, 22, 22, 22)
        native_hosts_layout.setSpacing(12)
        native_hosts_layout.addWidget(make_card_title("Native hosts"))
        native_hosts_layout.addWidget(make_helper_text("Prepared platform hosts stay manageable here without opening the browser control plane."))
        self.native_hosts_tree = QTreeWidget()
        self.native_hosts_tree.setColumnCount(4)
        self.native_hosts_tree.setHeaderLabels(["Host", "Mode", "Status", "Storage"])
        self.native_hosts_tree.setRootIsDecorated(False)
        self.native_hosts_tree.setMinimumHeight(220)
        host_header = self.native_hosts_tree.header()
        host_header.setStretchLastSection(False)
        host_header.setSectionResizeMode(0, QHeaderView.Stretch)
        host_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        host_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        host_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        native_hosts_layout.addWidget(self.native_hosts_tree, 1)
        host_actions = QHBoxLayout()
        host_actions.setSpacing(10)
        self._register_responsive_box(host_actions, breakpoint=1080)
        self.native_capture_inventory_button = make_button("Capture Inventory", "secondary")
        self.native_capture_inventory_button.clicked.connect(self._capture_selected_platform_host_inventory)
        self.native_prepare_host_button = make_button("Prepare Host", "ghost")
        self.native_prepare_host_button.clicked.connect(self._prepare_selected_platform_host)
        self.native_reclaim_runtime_button = make_button("Reclaim WSL Memory", "ghost")
        self.native_reclaim_runtime_button.clicked.connect(self._reclaim_selected_platform_host_runtime)
        self.task_buttons.extend([self.native_capture_inventory_button, self.native_prepare_host_button, self.native_reclaim_runtime_button])
        host_actions.addWidget(self.native_capture_inventory_button)
        host_actions.addWidget(self.native_prepare_host_button)
        host_actions.addWidget(self.native_reclaim_runtime_button)
        host_actions.addStretch(1)
        native_hosts_layout.addLayout(host_actions)

        native_doplets_card = card_frame()
        native_doplets_layout = QVBoxLayout(native_doplets_card)
        native_doplets_layout.setContentsMargins(22, 22, 22, 22)
        native_doplets_layout.setSpacing(12)
        native_doplets_layout.addWidget(make_card_title("Native Doplets"))
        native_doplets_layout.addWidget(make_helper_text("Select a Doplet to open a terminal, queue lifecycle actions, or delete the record natively."))
        self.native_doplets_tree = QTreeWidget()
        self.native_doplets_tree.setColumnCount(4)
        self.native_doplets_tree.setHeaderLabels(["Doplet", "Host", "Status", "Access"])
        self.native_doplets_tree.setRootIsDecorated(False)
        self.native_doplets_tree.setMinimumHeight(220)
        self.native_doplets_tree.itemSelectionChanged.connect(self._native_doplet_selection_changed)
        doplet_header = self.native_doplets_tree.header()
        doplet_header.setStretchLastSection(False)
        doplet_header.setSectionResizeMode(0, QHeaderView.Stretch)
        doplet_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        doplet_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        doplet_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        native_doplets_layout.addWidget(self.native_doplets_tree, 1)
        self.native_doplet_detail = QTextBrowser()
        self.native_doplet_detail.setProperty("output", True)
        self.native_doplet_detail.setMinimumHeight(180)
        self.native_doplet_detail.setOpenExternalLinks(False)
        native_doplets_layout.addWidget(self.native_doplet_detail)
        doplet_actions = QHBoxLayout()
        doplet_actions.setSpacing(10)
        self._register_responsive_box(doplet_actions, breakpoint=1080)
        self.native_manage_open_button = make_button("Open Terminal", "secondary")
        self.native_manage_open_button.clicked.connect(self._open_selected_native_doplet_terminal)
        self.native_manage_show_ips_button = make_button("Show IPs", "ghost")
        self.native_manage_show_ips_button.clicked.connect(self._show_selected_native_doplet_ips)
        self.native_manage_copy_ips_button = make_button("Copy IPs", "ghost")
        self.native_manage_copy_ips_button.clicked.connect(self._copy_selected_native_doplet_ips)
        self.native_manage_copy_button = make_button("Copy Terminal", "ghost")
        self.native_manage_copy_button.clicked.connect(self._copy_selected_native_doplet_terminal)
        self.native_manage_start_button = make_button("Start", "ghost")
        self.native_manage_start_button.clicked.connect(lambda: self._queue_native_doplet_lifecycle("start"))
        self.native_manage_resize_button = make_button("Resize VPS", "ghost")
        self.native_manage_resize_button.clicked.connect(self._resize_selected_native_doplet)
        self.native_manage_reprovision_button = make_button("Reprovision VPS", "ghost")
        self.native_manage_reprovision_button.clicked.connect(self._reprovision_selected_native_doplet)
        self.native_manage_shutdown_button = make_button("Shutdown", "ghost")
        self.native_manage_shutdown_button.clicked.connect(lambda: self._queue_native_doplet_lifecycle("shutdown"))
        self.native_manage_force_stop_button = make_button("Force Stop", "ghost")
        self.native_manage_force_stop_button.clicked.connect(lambda: self._queue_native_doplet_lifecycle("force-stop"))
        self.native_manage_delete_button = make_button("Delete VPS", "ghost")
        self.native_manage_delete_button.clicked.connect(self._delete_selected_native_doplet)
        self.task_buttons.extend(
            [
                self.native_manage_open_button,
                self.native_manage_show_ips_button,
                self.native_manage_copy_ips_button,
                self.native_manage_copy_button,
                self.native_manage_start_button,
                self.native_manage_resize_button,
                self.native_manage_reprovision_button,
                self.native_manage_shutdown_button,
                self.native_manage_force_stop_button,
                self.native_manage_delete_button,
            ]
        )
        doplet_actions.addWidget(self.native_manage_open_button)
        doplet_actions.addWidget(self.native_manage_show_ips_button)
        doplet_actions.addWidget(self.native_manage_copy_ips_button)
        doplet_actions.addWidget(self.native_manage_copy_button)
        doplet_actions.addWidget(self.native_manage_start_button)
        doplet_actions.addWidget(self.native_manage_resize_button)
        doplet_actions.addWidget(self.native_manage_reprovision_button)
        doplet_actions.addWidget(self.native_manage_shutdown_button)
        doplet_actions.addWidget(self.native_manage_force_stop_button)
        doplet_actions.addWidget(self.native_manage_delete_button)
        doplet_actions.addStretch(1)
        native_doplets_layout.addLayout(doplet_actions)

        self.native_manager_cards = [native_hosts_card, native_doplets_card]
        self._reflow_grid(platform_manager_grid, self.native_manager_cards, columns=2)
        layout.addLayout(platform_manager_grid)

        native_grid = QGridLayout()
        native_grid.setHorizontalSpacing(16)
        native_grid.setVerticalSpacing(16)
        self.native_platform_grid = native_grid

        native_tasks_card = card_frame()
        native_tasks_layout = QVBoxLayout(native_tasks_card)
        native_tasks_layout.setContentsMargins(22, 22, 22, 22)
        native_tasks_layout.setSpacing(12)
        native_tasks_layout.addWidget(make_card_title("Native task center"))
        native_tasks_layout.addWidget(make_helper_text("Review recent control-plane work, inspect progress, and trigger task actions without leaving the native shell."))
        self.native_task_tree = QTreeWidget()
        self.native_task_tree.setColumnCount(4)
        self.native_task_tree.setHeaderLabels(["Task", "Status", "Progress", "Target"])
        self.native_task_tree.setRootIsDecorated(False)
        self.native_task_tree.setMinimumHeight(280)
        self.native_task_tree.itemSelectionChanged.connect(self._native_task_selection_changed)
        native_task_header = self.native_task_tree.header()
        native_task_header.setStretchLastSection(False)
        native_task_header.setSectionResizeMode(0, QHeaderView.Stretch)
        native_task_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        native_task_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        native_task_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        native_tasks_layout.addWidget(self.native_task_tree, 1)
        native_task_actions = QHBoxLayout()
        native_task_actions.setSpacing(10)
        self._register_responsive_box(native_task_actions, breakpoint=1080)
        self.native_task_launch_button = make_button("Launch", "secondary")
        self.native_task_launch_button.clicked.connect(self._launch_selected_platform_task)
        self.native_task_cancel_button = make_button("Cancel", "ghost")
        self.native_task_cancel_button.clicked.connect(self._cancel_selected_platform_task)
        self.native_task_retry_button = make_button("Retry", "ghost")
        self.native_task_retry_button.clicked.connect(self._retry_selected_platform_task)
        self.task_buttons.extend([self.native_task_launch_button, self.native_task_cancel_button, self.native_task_retry_button])
        native_task_actions.addWidget(self.native_task_launch_button)
        native_task_actions.addWidget(self.native_task_cancel_button)
        native_task_actions.addWidget(self.native_task_retry_button)
        self.native_task_auto_retry = QCheckBox("Auto-retry failed tasks")
        self.native_task_auto_retry.setChecked(True)
        self.native_task_auto_retry.setToolTip(
            "When enabled, VPSdash will automatically retry failed or cancelled native control-plane tasks "
            "up to 3 times while watched work is still active."
        )
        self.native_task_auto_retry.stateChanged.connect(lambda _state: self._sync_task_polling())
        native_task_actions.addWidget(self.native_task_auto_retry)
        native_task_actions.addStretch(1)
        native_tasks_layout.addLayout(native_task_actions)

        native_assets_card = card_frame()
        native_assets_layout = QVBoxLayout(native_assets_card)
        native_assets_layout.setContentsMargins(22, 22, 22, 22)
        native_assets_layout.setSpacing(12)
        native_assets_layout.addWidget(make_card_title("Native platform inventory"))
        native_assets_layout.addWidget(make_helper_text("Hosts, Doplets, networks, providers, snapshots, and backups stay visible here even when the embedded admin is closed."))
        self.native_asset_tree = QTreeWidget()
        self.native_asset_tree.setColumnCount(3)
        self.native_asset_tree.setHeaderLabels(["Type", "Name", "Detail"])
        self.native_asset_tree.setRootIsDecorated(False)
        self.native_asset_tree.setMinimumHeight(280)
        native_asset_header = self.native_asset_tree.header()
        native_asset_header.setStretchLastSection(False)
        native_asset_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        native_asset_header.setSectionResizeMode(1, QHeaderView.Stretch)
        native_asset_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        native_assets_layout.addWidget(self.native_asset_tree, 1)

        native_detail_card = card_frame()
        native_detail_layout = QVBoxLayout(native_detail_card)
        native_detail_layout.setContentsMargins(22, 22, 22, 22)
        native_detail_layout.setSpacing(12)
        native_detail_layout.addWidget(make_card_title("Native task detail"))
        native_detail_layout.addWidget(make_helper_text("Selected task payload, logs, and result metadata are mirrored here for the native workspace."))
        self.native_task_detail = QTextBrowser()
        self.native_task_detail.setMinimumHeight(220)
        self.native_task_detail.setProperty("output", True)
        self.native_task_detail.setOpenExternalLinks(False)
        self.native_task_detail.setPlainText("Select a task to inspect it.")
        native_detail_layout.addWidget(self.native_task_detail, 1)

        native_history_card = card_frame()
        native_history_layout = QVBoxLayout(native_history_card)
        native_history_layout.setContentsMargins(22, 22, 22, 22)
        native_history_layout.setSpacing(12)
        native_history_layout.addWidget(make_card_title("Task history"))
        native_history_layout.addWidget(
            make_helper_text("Completed and cancelled control-plane work is moved here so the active task center stays focused on live operations.")
        )
        self.native_task_history = QTextBrowser()
        self.native_task_history.setMinimumHeight(220)
        self.native_task_history.setProperty("output", True)
        self.native_task_history.setOpenExternalLinks(False)
        self.native_task_history.setPlainText("No archived task history yet.")
        native_history_layout.addWidget(self.native_task_history, 1)

        self.native_platform_cards = [native_tasks_card, native_assets_card, native_detail_card, native_history_card]
        self._reflow_grid(native_grid, self.native_platform_cards, columns=2)
        layout.addLayout(native_grid)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        self.operations_grid = grid
        self.diagnostics_output = self._output_card(
            "Diagnostics",
            "Machine identity, package availability, container tooling, and execution prerequisites appear here.",
        )
        self.monitor_output = self._output_card(
            "Snapshot",
            "Memory, listeners, running containers, and quick host telemetry appear here.",
        )
        self.error_output = self._output_card(
            "Error console",
            "Native operation failures, task errors, and stack traces appear here so you can inspect why a Doplet or host action failed.",
        )
        self.error_output["output"].setPlainText("No errors logged yet.")
        self.execution_output = self._output_card(
            "Execution log",
            "Dry runs and live runs stream their command-by-command results here.",
        )
        self.operations_grid_cards = [
            self.diagnostics_output["frame"],
            self.monitor_output["frame"],
            self.error_output["frame"],
            self.execution_output["frame"],
        ]
        grid.addWidget(self.diagnostics_output["frame"], 0, 0)
        grid.addWidget(self.monitor_output["frame"], 0, 1)
        grid.addWidget(self.error_output["frame"], 1, 0)
        grid.addWidget(self.execution_output["frame"], 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
        layout.addLayout(grid, 1)
        self.operations_scroll = wrap_scroll(page)
        return self.operations_scroll

    def _build_control_plane_page(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(0)
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        header = card_frame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 22, 24, 22)
        header_layout.setSpacing(18)
        self._register_responsive_box(header_layout, breakpoint=1180)

        header_text = QVBoxLayout()
        header_text.setSpacing(8)
        header_text.addWidget(make_section_title("Doplet admin"))
        header_text.addWidget(
            make_helper_text(
                "The full admin now lives inside the native shell. Use this page for host setup, WSL-backed Windows hosts, Doplets, CPU/RAM/disk sizing, storage, networks, GPUs, users, backups, and task orchestration."
            )
        )
        self.control_plane_status = make_wrap_label("Doplet admin not loaded yet.", css_class="CardBody")
        header_text.addWidget(self.control_plane_status)
        header_layout.addLayout(header_text, 1)

        action_box = QVBoxLayout()
        action_box.setSpacing(10)
        open_manage = make_button("Open Manager", "primary")
        open_manage.clicked.connect(self._open_doplet_admin)
        open_builder = make_button("Open Builder", "secondary")
        open_builder.clicked.connect(self._open_doplet_builder)
        refresh_embedded = make_button("Refresh Embedded Admin", "secondary")
        refresh_embedded.clicked.connect(lambda: self._load_embedded_control_plane(force_reload=True))
        open_external = make_button("Open In Browser", "ghost")
        open_external.clicked.connect(self._open_web_admin)
        action_box.addWidget(open_manage)
        action_box.addWidget(open_builder)
        action_box.addWidget(refresh_embedded)
        action_box.addWidget(open_external)
        action_box.addStretch(1)
        header_layout.addLayout(action_box)
        layout.addWidget(header)

        panel = card_frame()
        panel.setMinimumHeight(0)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        self.control_plane_panel_layout = panel_layout
        self.control_plane_placeholder = QTextBrowser()
        self.control_plane_placeholder.setMinimumHeight(720)
        self.control_plane_placeholder.setProperty("output", True)
        self.control_plane_placeholder.setOpenExternalLinks(True)
        self.control_plane_placeholder.setHtml(
            "<h3>Embedded Doplet admin is not loaded yet.</h3>"
            "<p>Use <b>Load Doplet Admin</b> when you need the embedded web control plane. "
            "Deferring WebEngine startup keeps the desktop shell faster to open.</p>"
        )
        panel_layout.addWidget(self.control_plane_placeholder, 1)
        layout.addWidget(panel)
        self.control_plane_scroll = wrap_scroll(page)
        self.control_plane_scroll.verticalScrollBar().setSingleStep(48)
        return self.control_plane_scroll

    def _metric_card(self, label: str, description: str) -> dict[str, Any]:
        frame = card_frame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)
        label_widget = QLabel(label)
        label_widget.setProperty("class", "MetricLabel")
        value = QLabel("0")
        value.setProperty("class", "MetricValue")
        body = make_wrap_label(description, css_class="CardBody")
        layout.addWidget(label_widget)
        layout.addWidget(value)
        layout.addWidget(body)
        return {"frame": frame, "value": value, "body": body}

    def _output_card(self, title: str, placeholder: str) -> dict[str, Any]:
        frame = card_frame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        layout.addWidget(make_card_title(title))
        layout.addWidget(make_helper_text(placeholder))
        output = QTextBrowser()
        output.setMinimumWidth(0)
        output.setMinimumHeight(180)
        output.setProperty("output", True)
        output.setOpenExternalLinks(False)
        output.setLineWrapMode(QTextEdit.WidgetWidth)
        output.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        output.setPlainText("No run yet.")
        _style_refresh(output)
        layout.addWidget(output, 1)
        return {"frame": frame, "output": output}

    def _wire_live_updates(self) -> None:
        self.host_name.textChanged.connect(self._handle_form_changed)
        self.host_mode.currentTextChanged.connect(self._host_mode_changed)
        self.host_mode.currentTextChanged.connect(self._handle_form_changed)
        self.host_device_role.currentIndexChanged.connect(self._host_mode_changed)
        self.host_device_role.currentIndexChanged.connect(self._handle_form_changed)
        self.host_bootstrap_auth.currentIndexChanged.connect(self._host_mode_changed)
        self.host_bootstrap_auth.currentIndexChanged.connect(self._handle_form_changed)
        self.host_ssh_user.textChanged.connect(self._handle_form_changed)
        self.host_ssh_host.textChanged.connect(self._handle_form_changed)
        self.host_ssh_port.valueChanged.connect(self._handle_form_changed)
        self.host_ssh_key.currentTextChanged.connect(self._handle_form_changed)
        self.host_wsl_distribution.textChanged.connect(self._handle_form_changed)

        self.project_name.textChanged.connect(self._handle_form_changed)
        self.project_repo_url.textChanged.connect(self._handle_form_changed)
        self.project_branch.textChanged.connect(self._handle_form_changed)
        self.project_deploy_path.textChanged.connect(self._handle_form_changed)
        self.project_primary_domain.textChanged.connect(self._handle_form_changed)
        self.project_letsencrypt_email.textChanged.connect(self._handle_form_changed)
        self.project_domains.textChanged.connect(self._handle_form_changed)
        self.template_select.currentIndexChanged.connect(self._handle_form_changed)
        self.env_table.itemChanged.connect(self._handle_form_changed)

    def _clear_status(self) -> None:
        self.sidebar_status.setText(self._default_status_text)
        self.sidebar_status.setToolTip("")

    def _set_status(self, message: str, timeout_ms: int = 0) -> None:
        text = message.strip() or self._default_status_text
        self.sidebar_status.setText(text)
        self.sidebar_status.setToolTip(text if len(text) > 48 else "")
        self._status_reset_timer.stop()
        if timeout_ms > 0 and text != self._default_status_text:
            self._status_reset_timer.start(timeout_ms)

    def _set_task_controls_enabled(self, enabled: bool) -> None:
        for button in self.task_buttons:
            button.setEnabled(enabled)

    def _begin_async_task(self, message: str) -> None:
        self.busy_tasks += 1
        if self.busy_tasks == 1:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._set_task_controls_enabled(False)
        self._set_status(message)

    def _finish_async_task(self) -> None:
        self.busy_tasks = max(0, self.busy_tasks - 1)
        if self.busy_tasks == 0:
            self._set_task_controls_enabled(True)
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()

    def _set_initial_setup_progress(self, percent: int, message: str) -> None:
        if not hasattr(self, "initial_setup_progress"):
            return
        compact = self._summarize_status_message(message)
        value = max(0, min(int(percent), 100))
        self.initial_setup_progress.setValue(value)
        self.initial_setup_progress.setFormat(f"{value}%")
        self.initial_setup_status.setText(compact)
        self.initial_setup_status.setToolTip(str(message or ""))

    def _reset_initial_setup_progress(self, message: str = "Initial setup has not run yet.") -> None:
        if not hasattr(self, "initial_setup_progress"):
            return
        self.initial_setup_progress.setValue(0)
        self.initial_setup_progress.setFormat("Setup idle")
        compact = self._summarize_status_message(message)
        self.initial_setup_status.setText(compact)
        self.initial_setup_status.setToolTip(str(message or ""))

    def _run_async_task(
        self,
        *,
        start_message: str,
        work: Any,
        on_success: Any,
        error_title: str,
        done_message: str,
        on_progress: Any | None = None,
    ) -> None:
        if self.busy_tasks > 0:
            self._set_status("Another task is already running", 3000)
            return

        task = BackgroundTask(work)
        self.active_tasks.append(task)

        def _success(result: Any) -> None:
            on_success(result)
            self._set_status(done_message, 4000)

        def _error(message: str, details: str) -> None:
            if on_progress is not None:
                on_progress(0, f"{error_title}: inspect Activity for details.")
            self._show_error(error_title, message, details)

        def _finished() -> None:
            if task in self.active_tasks:
                self.active_tasks.remove(task)
            self._finish_async_task()

        task.signals.success.connect(_success)
        task.signals.error.connect(_error)
        if on_progress is not None:
            task.signals.progress.connect(on_progress)
        task.signals.finished.connect(_finished)

        self._begin_async_task(start_message)
        self.thread_pool.start(task)

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for idx, button in enumerate(self.nav_buttons):
            button.setChecked(idx == index)
            _style_refresh(button)
        self._refresh_topbar()
        self._update_responsive_layouts()

    def _show_native_doplets_page(self, target: QWidget | None = None) -> None:
        self._switch_page(self.PAGE_HARMINOPLETS)
        if hasattr(self, "setup_scroll"):
            scroll = self.setup_scroll.verticalScrollBar()
            if scroll is not None:
                scroll.setValue(0 if target is None else max(0, scroll.value()))
            if target is not None:
                QTimer.singleShot(0, lambda: self.setup_scroll.ensureWidgetVisible(target, 0, 48))

    def _apply_form_refresh(self) -> None:
        if self._live_updates_suspended:
            return
        self.current_plan = None
        self._render_plan()
        self._refresh_dashboard()

    def _update_responsive_layouts(self, available_width: int | None = None) -> None:
        current_page = self.pages.currentWidget() if hasattr(self, "pages") else None
        current_page_width = 0
        if isinstance(current_page, QScrollArea):
            current_page_width = current_page.viewport().width()
        elif current_page is not None:
            current_page_width = current_page.width()
        page_width = available_width or current_page_width or (self.pages.width() if hasattr(self, "pages") else 0) or self.width()
        operations_width = available_width or (
            self.operations_scroll.viewport().width() if hasattr(self, "operations_scroll") else 0
        ) or page_width
        setup_width = available_width or (self.setup_scroll.viewport().width() if hasattr(self, "setup_scroll") else 0) or page_width

        for config in self._responsive_boxes:
            layout = config["layout"]
            vertical = page_width < int(config["breakpoint"])
            layout.setDirection(config["vertical_direction"] if vertical else QBoxLayout.LeftToRight)

        if hasattr(self, "setup_scroll"):
            stacked = setup_width < 1180
            self.setup_cards_row.setDirection(QBoxLayout.TopToBottom if stacked else QBoxLayout.LeftToRight)
            self.setup_cards_row.setStretch(0, 0 if stacked else 5)
            self.setup_cards_row.setStretch(1, 0 if stacked else 7)

        if hasattr(self, "metrics_grid"):
            metric_columns = 4 if page_width >= 1320 else (2 if page_width >= 860 else 1)
            self._reflow_grid(self.metrics_grid, self.metric_cards, columns=metric_columns)

        if hasattr(self, "overview_insight_grid"):
            self._reflow_grid(self.overview_insight_grid, self.overview_insight_cards, columns=2 if page_width >= 1080 else 1)

        if hasattr(self, "defaults_preview_grid"):
            self._reflow_grid(self.defaults_preview_grid, self.defaults_preview_cards, columns=2 if page_width >= 1080 else 1)

        if hasattr(self, "setup_quickstart_grid"):
            self._reflow_grid(self.setup_quickstart_grid, self.setup_quickstart_cards, columns=2 if setup_width >= 1080 else 1)

        if hasattr(self, "remote_grid"):
            self._reflow_grid(self.remote_grid, self.remote_cards, columns=2 if page_width >= 1080 else 1)

        if hasattr(self, "plan_summary_grid"):
            self._reflow_grid(self.plan_summary_grid, self.plan_summary_cards, columns=2 if page_width >= 1080 else 1)

        if hasattr(self, "instance_management_grid"):
            self._reflow_grid(self.instance_management_grid, self.instance_management_cards, columns=2 if operations_width >= 1180 else 1)

        if hasattr(self, "native_platform_grid"):
            self._reflow_grid(self.native_platform_grid, self.native_platform_cards, columns=2 if operations_width >= 1180 else 1)

        if hasattr(self, "operations_grid"):
            self._clear_layout_items(self.operations_grid)
            if operations_width >= 1160:
                self.operations_grid.addWidget(self.operations_grid_cards[0], 0, 0)
                self.operations_grid.addWidget(self.operations_grid_cards[1], 0, 1)
                self.operations_grid.addWidget(self.operations_grid_cards[2], 1, 0)
                self.operations_grid.addWidget(self.operations_grid_cards[3], 1, 1)
                self.operations_grid.setColumnStretch(0, 1)
                self.operations_grid.setColumnStretch(1, 1)
                self.operations_grid.setRowStretch(0, 0)
                self.operations_grid.setRowStretch(1, 1)
            else:
                self.operations_grid.addWidget(self.operations_grid_cards[0], 0, 0)
                self.operations_grid.addWidget(self.operations_grid_cards[1], 1, 0)
                self.operations_grid.addWidget(self.operations_grid_cards[2], 2, 0)
                self.operations_grid.addWidget(self.operations_grid_cards[3], 3, 0)
                self.operations_grid.setColumnStretch(0, 1)
                self.operations_grid.setColumnStretch(1, 0)
                self.operations_grid.setRowStretch(0, 0)
                self.operations_grid.setRowStretch(1, 0)
                self.operations_grid.setRowStretch(2, 0)
                self.operations_grid.setRowStretch(3, 1)
        self._schedule_layout_audit()

    def resizeEvent(self, event: QEvent) -> None:
        super().resizeEvent(event)
        self._update_responsive_layouts()

    def _apply_bootstrap_data(self, bootstrap_data: dict[str, Any]) -> None:
        self._form_refresh_timer.stop()
        self.bootstrap_data = bootstrap_data
        self.templates = self.bootstrap_data["templates"]
        self.defaults = self.bootstrap_data["defaults"]
        self.hosts = self.bootstrap_data["state"]["hosts"]
        self.projects = self.bootstrap_data["state"]["projects"]
        self.instances = self.bootstrap_data["state"].get("instances", [])
        self.local_machine = self.bootstrap_data.get("local_machine", {})

        self._populate_key_candidates(self.bootstrap_data["key_candidates"])
        self._populate_templates()
        self._populate_defaults()
        self._populate_hosts()
        self._populate_projects()
        self._populate_instances()
        self._populate_native_platform_views()

        self._refresh_dashboard()
        self._render_plan()

        self._host_mode_changed()
        self._switch_page(self.pages.currentIndex())
        self._maybe_auto_retry_watched_tasks()
        self._sync_task_polling()
        self._schedule_layout_audit()

    def _load_bootstrap(self) -> None:
        try:
            self._apply_bootstrap_data(self.service.bootstrap())
            self._set_status("State refreshed", 3000)
        except Exception as exc:
            self._show_error("State refresh failed", str(exc), traceback.format_exc())

    def _load_bootstrap_async(self) -> None:
        self._run_async_task(
            start_message="Loading control plane...",
            work=self.service.bootstrap,
            on_success=lambda payload: self._apply_bootstrap_data(payload),
            error_title="State refresh failed",
            done_message="State refreshed",
        )

    def _task_root_id(self, task: dict[str, Any], task_map: dict[int, dict[str, Any]]) -> int:
        current = task
        seen: set[int] = set()
        while True:
            payload = dict(current.get("result_payload") or {})
            parent_id = int(payload.get("retry_of_task_id") or 0)
            if not parent_id or parent_id in seen or parent_id not in task_map:
                return int(current.get("id", 0))
            seen.add(parent_id)
            current = task_map[parent_id]

    def _task_leafs_by_root(self, tasks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        task_map = {int(item.get("id", 0)): item for item in tasks if int(item.get("id", 0))}
        child_ids = {
            int(dict(item.get("result_payload") or {}).get("retry_of_task_id") or 0)
            for item in tasks
            if int(dict(item.get("result_payload") or {}).get("retry_of_task_id") or 0)
        }
        leaves: dict[int, dict[str, Any]] = {}
        for task_id, task in task_map.items():
            if task_id in child_ids:
                continue
            root_id = self._task_root_id(task, task_map)
            current = leaves.get(root_id)
            if current is None or int(task.get("id", 0)) > int(current.get("id", 0)):
                leaves[root_id] = task
        return leaves

    def _watch_platform_task(self, task_id: int) -> None:
        if task_id <= 0:
            return
        root_id = int(task_id)
        found_task = False
        if hasattr(self, "bootstrap_data"):
            tasks = list((self.bootstrap_data.get("control_plane", {}) or {}).get("tasks", []) or [])
            task_map = {int(item.get("id", 0)): item for item in tasks if int(item.get("id", 0))}
            task = task_map.get(int(task_id))
            if task:
                found_task = True
                root_id = self._task_root_id(task, task_map)
        self._watched_task_roots.add(root_id)
        self._auto_retry_attempts.setdefault(root_id, 0)
        if found_task:
            self._sync_task_polling()

    def _launch_queued_platform_task(self, queue_work: Any, *, actor: str = "desktop") -> dict[str, Any]:
        queued = queue_work()
        task_id = int(queued.get("id", 0))
        if task_id <= 0:
            raise ValueError("Queued task did not return a valid task id.")
        launched = self.service.launch_platform_task(task_id, actor=actor, dry_run=False)
        return {"queued": queued, "launched": launched, "task_id": task_id}

    def _retry_and_launch_platform_task(self, task_id: int, *, actor: str = "desktop") -> dict[str, Any]:
        queued = self.service.retry_platform_task(task_id, actor=actor)
        retry_id = int(queued.get("id", 0))
        if retry_id <= 0:
            raise ValueError("Retried task did not return a valid task id.")
        launched = self.service.launch_platform_task(retry_id, actor=actor, dry_run=False)
        return {"queued": queued, "launched": launched, "task_id": retry_id}

    def _sync_task_polling(self) -> None:
        if not hasattr(self, "bootstrap_data"):
            return
        control_plane = self.bootstrap_data.get("control_plane", {}) or {}
        tasks = list(control_plane.get("tasks", []) or [])
        leafs = self._task_leafs_by_root(tasks)
        active_watched = False
        retryable_watched = False
        finished_roots: set[int] = set()
        for root_id in list(self._watched_task_roots):
            leaf = leafs.get(root_id)
            if not leaf:
                finished_roots.add(root_id)
                continue
            status = str(leaf.get("status") or "")
            if status in ACTIVE_PLATFORM_TASK_STATUSES:
                active_watched = True
                continue
            if (
                self.native_task_auto_retry.isChecked()
                and status in RETRYABLE_PLATFORM_TASK_STATUSES
                and self._auto_retry_attempts.get(root_id, 0) < self._auto_retry_max_attempts
            ):
                retryable_watched = True
                continue
            finished_roots.add(root_id)

        if finished_roots:
            self._watched_task_roots.difference_update(finished_roots)

        if active_watched or retryable_watched or self._bootstrap_poll_inflight:
            self._task_poll_timer.setInterval(1600)
            if not self._task_poll_timer.isActive():
                self._task_poll_timer.start()
        else:
            self._task_poll_timer.setInterval(9000)
            if not self._task_poll_timer.isActive():
                self._task_poll_timer.start()

    def _maybe_auto_retry_watched_tasks(self) -> None:
        if not hasattr(self, "bootstrap_data") or not self.native_task_auto_retry.isChecked():
            return
        control_plane = self.bootstrap_data.get("control_plane", {}) or {}
        tasks = list(control_plane.get("tasks", []) or [])
        leafs = self._task_leafs_by_root(tasks)
        if any(str(task.get("status") or "") in ACTIVE_PLATFORM_TASK_STATUSES for task in leafs.values()):
            return
        candidates: list[tuple[int, dict[str, Any]]] = []
        for root_id in sorted(self._watched_task_roots):
            leaf = leafs.get(root_id)
            if not leaf:
                continue
            status = str(leaf.get("status") or "")
            if status not in RETRYABLE_PLATFORM_TASK_STATUSES:
                continue
            if self._auto_retry_attempts.get(root_id, 0) >= self._auto_retry_max_attempts:
                continue
            candidates.append((root_id, leaf))
        if not candidates:
            return
        root_id, leaf = max(candidates, key=lambda item: int(item[1].get("id", 0)))
        leaf_id = int(leaf.get("id", 0))
        if leaf_id <= 0:
            return
        try:
            self._auto_retry_attempts[root_id] = self._auto_retry_attempts.get(root_id, 0) + 1
            result = self._retry_and_launch_platform_task(leaf_id, actor="desktop")
            self._watch_platform_task(int(result.get("task_id", 0)))
            self._set_status(
                f"Auto-retrying task {leaf_id} ({self._auto_retry_attempts[root_id]}/{self._auto_retry_max_attempts})",
                5000,
            )
        except Exception as exc:
            self._set_status(f"Auto-retry failed for task {leaf_id}: {exc}", 5000)

    def _poll_control_plane_state(self) -> None:
        if self._bootstrap_poll_inflight:
            return
        self._bootstrap_poll_inflight = True

        task = BackgroundTask(self.service.bootstrap)
        self.active_tasks.append(task)

        def _success(payload: dict[str, Any]) -> None:
            self._apply_bootstrap_data(payload)

        def _error(message: str, _details: str) -> None:
            self._set_status(f"Background refresh failed: {message}", 4000)

        def _finished() -> None:
            if task in self.active_tasks:
                self.active_tasks.remove(task)
            self._bootstrap_poll_inflight = False
            self._sync_task_polling()

        task.signals.success.connect(_success)
        task.signals.error.connect(_error)
        task.signals.finished.connect(_finished)
        self.thread_pool.start(task)

    def _populate_key_candidates(self, candidates: list[str]) -> None:
        current_value = self.host_ssh_key.currentText().strip()
        self.host_ssh_key.blockSignals(True)
        self.host_ssh_key.clear()
        self.host_ssh_key.addItem("")
        for path in candidates:
            self.host_ssh_key.addItem(path)
        if current_value and self.host_ssh_key.findText(current_value) < 0:
            self.host_ssh_key.addItem(current_value)
        self.host_ssh_key.setCurrentText(current_value)
        self.host_ssh_key.blockSignals(False)

    def _populate_templates(self) -> None:
        selected_template = self.template_select.currentData()
        self.template_select.blockSignals(True)
        self.template_select.clear()
        for template in self.templates:
            self.template_select.addItem(template["name"], template["id"])
        if selected_template:
            index = self.template_select.findData(selected_template)
            if index >= 0:
                self.template_select.setCurrentIndex(index)
        self.template_select.blockSignals(False)

    def _populate_defaults(self) -> None:
        current_id = self.selected_default.get("id") if self.selected_default else None
        self.default_select.blockSignals(True)
        self.default_select.clear()
        for default_item in self.defaults:
            suffix = "built-in" if default_item.get("kind") == "builtin" else "custom"
            self.default_select.addItem(f"{default_item['name']}  -  {suffix}", default_item["id"])
        if current_id:
            index = self.default_select.findData(current_id)
            if index >= 0:
                self.default_select.setCurrentIndex(index)
        elif self.default_select.count():
            self.default_select.setCurrentIndex(0)
        self.default_select.blockSignals(False)
        self._default_selection_changed()

    def _populate_hosts(self) -> None:
        current_id = self.current_host.get("id") if self.current_host else None
        self.saved_host_select.blockSignals(True)
        self.saved_host_select.clear()
        self.saved_host_select.addItem("Choose a saved host", None)
        for host in self.hosts:
            self.saved_host_select.addItem(f"{host['name']}  -  {host['mode']}", host["id"])
        if current_id:
            index = self.saved_host_select.findData(current_id)
            if index >= 0:
                self.saved_host_select.setCurrentIndex(index)
        self.saved_host_select.blockSignals(False)

    def _populate_projects(self) -> None:
        current_id = self.current_project.get("id") if self.current_project else None
        self.saved_project_select.blockSignals(True)
        self.saved_project_select.clear()
        self.saved_project_select.addItem("Choose a saved project", None)
        for project in self.projects:
            self.saved_project_select.addItem(f"{project['name']}  -  {project['template_id']}", project["id"])
        if current_id:
            index = self.saved_project_select.findData(current_id)
            if index >= 0:
                self.saved_project_select.setCurrentIndex(index)
        self.saved_project_select.blockSignals(False)

    def _populate_instances(self) -> None:
        if not hasattr(self, "instances_tree"):
            return
        selected_id = self.current_instance.get("id") if self.current_instance else None
        self.instances_tree.blockSignals(True)
        self.instances_tree.clear()
        for instance in self.instances:
            host = instance.get("host", {})
            project = instance.get("project", {})
            item = QTreeWidgetItem(
                [
                    instance.get("name", "Unnamed instance"),
                    host.get("name", "Host"),
                    project.get("name", "Project"),
                    str(len(instance.get("backups", []))),
                ]
            )
            item.setData(0, Qt.UserRole, instance.get("id"))
            self.instances_tree.addTopLevelItem(item)
            if selected_id and instance.get("id") == selected_id:
                self.instances_tree.setCurrentItem(item)
        self.instances_tree.blockSignals(False)
        self._instance_selection_changed()

    def _populate_native_platform_views(self) -> None:
        control_plane = self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}
        if hasattr(self, "native_doplet_host"):
            selected_host_id = self.native_doplet_host.currentData()
            selected_image_id = self.native_doplet_image.currentData()
            selected_flavor_id = self.native_doplet_flavor.currentData()
            selected_network_id = self.native_doplet_network.currentData()
            self.native_doplet_host.blockSignals(True)
            self.native_doplet_host.clear()
            for host in control_plane.get("hosts", []):
                label = f"{host.get('name', 'Host')}  -  {host.get('status', 'unknown')}"
                self.native_doplet_host.addItem(label, int(host.get("id", 0)))
            self.native_doplet_host.blockSignals(False)
            if selected_host_id is not None:
                index = self.native_doplet_host.findData(selected_host_id)
                if index >= 0:
                    self.native_doplet_host.setCurrentIndex(index)
            elif self.native_doplet_host.count():
                self.native_doplet_host.setCurrentIndex(0)

            self.native_doplet_image.blockSignals(True)
            self.native_doplet_image.clear()
            for image in control_plane.get("images", []):
                self.native_doplet_image.addItem(image.get("name", "Image"), int(image.get("id", 0)))
            self.native_doplet_image.blockSignals(False)
            if selected_image_id is not None:
                index = self.native_doplet_image.findData(selected_image_id)
                if index >= 0:
                    self.native_doplet_image.setCurrentIndex(index)
            elif self.native_doplet_image.count():
                self.native_doplet_image.setCurrentIndex(0)

            self.native_doplet_flavor.blockSignals(True)
            self.native_doplet_flavor.clear()
            for flavor in control_plane.get("flavors", []):
                label = f"{flavor.get('name', 'Flavor')} ({flavor.get('vcpu', 1)} CPU / {flavor.get('ram_mb', 0)} MB / {flavor.get('disk_gb', 0)} GB)"
                self.native_doplet_flavor.addItem(label, int(flavor.get("id", 0)))
            self.native_doplet_flavor.blockSignals(False)
            if selected_flavor_id is not None:
                index = self.native_doplet_flavor.findData(selected_flavor_id)
                if index >= 0:
                    self.native_doplet_flavor.setCurrentIndex(index)
            elif self.native_doplet_flavor.count():
                self.native_doplet_flavor.setCurrentIndex(0)

            self.native_doplet_network.blockSignals(True)
            self.native_doplet_network.clear()
            self.native_doplet_network.addItem("No primary network", None)
            for network in control_plane.get("networks", []):
                self.native_doplet_network.addItem(network.get("name", "Network"), int(network.get("id", 0)))
            self.native_doplet_network.blockSignals(False)
            if selected_network_id is not None:
                index = self.native_doplet_network.findData(selected_network_id)
                if index >= 0:
                    self.native_doplet_network.setCurrentIndex(index)
            if not self.native_doplet_name.text().strip():
                self.native_doplet_name.setText("builder-01")
            if not self.native_doplet_slug.text().strip():
                self.native_doplet_slug.setText("builder-01")
            local_keys = list((self.local_machine or {}).get("ssh_public_keys") or [])
            current_key = local_keys[0] if local_keys else {}
            key_value = str(current_key.get("public_key") or "")
            key_label = str(current_key.get("label") or "")
            key_path = str(current_key.get("path") or "")
            if hasattr(self, "current_ssh_key_label"):
                if key_value:
                    self.current_ssh_key_label.setText(
                        f"Current machine key: <b>{_escape(key_label or 'SSH public key')}</b>. Hover to reveal. Click to copy."
                    )
                else:
                    self.current_ssh_key_label.setText("No SSH public key found on this computer yet.")
            if hasattr(self, "current_ssh_key_field"):
                self.current_ssh_key_field.setSecretValue(key_value)
            if hasattr(self, "current_ssh_key_path"):
                self.current_ssh_key_path.setText(_escape(key_path) if key_path else "")
            self._native_auth_mode_changed()

        if hasattr(self, "native_hosts_tree"):
            self.native_hosts_tree.clear()
            for host in control_plane.get("hosts", []):
                item = QTreeWidgetItem(
                    [
                        str(host.get("name") or "Host"),
                        str(host.get("host_mode") or "-"),
                        str(host.get("status") or "unknown"),
                        str(host.get("primary_storage_backend") or "-"),
                    ]
                )
                item.setData(0, Qt.UserRole, int(host.get("id", 0)))
                self.native_hosts_tree.addTopLevelItem(item)

        if hasattr(self, "native_doplets_tree"):
            selected_id = self.current_native_doplet_id
            self.native_doplets_tree.blockSignals(True)
            self.native_doplets_tree.clear()
            for doplet in self._control_plane_doplets():
                host = next((item for item in control_plane.get("hosts", []) if _coerce_int(item.get("id")) == _coerce_int(doplet.get("host_id"))), {})
                access = ", ".join(doplet.get("ip_addresses") or []) or doplet.get("bootstrap_user", "-")
                item = QTreeWidgetItem(
                    [
                        str(doplet.get("name") or "Doplet"),
                        str(host.get("name") or "-"),
                        str(doplet.get("status") or "unknown"),
                        access,
                    ]
                )
                item.setData(0, Qt.UserRole, int(doplet.get("id", 0)))
                self.native_doplets_tree.addTopLevelItem(item)
                if selected_id and int(doplet.get("id", 0)) == int(selected_id):
                    self.native_doplets_tree.setCurrentItem(item)
            self.native_doplets_tree.blockSignals(False)
            if self.native_doplets_tree.currentItem() is None and self.native_doplets_tree.topLevelItemCount():
                self.native_doplets_tree.setCurrentItem(self.native_doplets_tree.topLevelItem(0))
            elif self.native_doplets_tree.currentItem() is None:
                self.current_native_doplet_id = None
            self._native_doplet_selection_changed()

        if hasattr(self, "native_task_tree"):
            selected_task_id = self._selected_native_task_id()
            self.native_task_tree.blockSignals(True)
            self.native_task_tree.clear()
            for task in self._active_control_plane_tasks():
                item = QTreeWidgetItem(
                    [
                        task.get("task_type", "task"),
                        task.get("status", "unknown"),
                        f"{task.get('progress', 0)}%",
                        f"{task.get('target_type', '')} {task.get('target_id', '')}".strip(),
                    ]
                )
                item.setData(0, Qt.UserRole, int(task.get("id", 0)))
                self.native_task_tree.addTopLevelItem(item)
                if selected_task_id and int(task.get("id", 0)) == selected_task_id:
                    self.native_task_tree.setCurrentItem(item)
            self.native_task_tree.blockSignals(False)
            if self.native_task_tree.currentItem() is None and self.native_task_tree.topLevelItemCount():
                self.native_task_tree.setCurrentItem(self.native_task_tree.topLevelItem(0))
            self._native_task_selection_changed()

        if hasattr(self, "native_task_history"):
            history_lines: list[str] = []
            for task in self._archived_control_plane_tasks()[:20]:
                updated = str(task.get("updated_at") or task.get("created_at") or "").replace("T", " ")
                history_lines.append(
                    f"{task.get('task_type', 'task')} | {task.get('status', 'unknown')} | "
                    f"{task.get('target_type', '')} {task.get('target_id', '')}".strip() + (f" | {updated}" if updated else "")
                )
            self.native_task_history.setPlainText("\n".join(history_lines) if history_lines else "No archived task history yet.")
        self._refresh_error_console()

        if hasattr(self, "native_asset_tree"):
            self.native_asset_tree.clear()
            collections = [
                ("Host", control_plane.get("hosts", []), lambda item: f"{item.get('status', 'unknown')} | {item.get('primary_storage_backend', 'unknown')}"),
                ("Doplet", self._control_plane_doplets(), lambda item: f"{item.get('status', 'unknown')} | {item.get('vcpu', 0)} CPU / {item.get('ram_mb', 0)} MB"),
                ("Archived Doplet", self._archived_control_plane_doplets(), lambda item: f"deleted {item.get('deleted_at', '') or item.get('updated_at', '')}"),
                ("Network", control_plane.get("networks", []), lambda item: f"{item.get('mode', 'unknown')} | {item.get('cidr', '') or item.get('bridge_name', '-') }"),
                ("Provider", control_plane.get("providers", []), lambda item: f"{item.get('provider_type', 'unknown')} | {item.get('bucket', '') or item.get('root_path', '-') }"),
                ("Backup", control_plane.get("backups", []), lambda item: f"{item.get('status', 'unknown')} | {item.get('artifact_reference', '-') }"),
            ]
            for label, items, detail_fn in collections:
                for item in items:
                    tree_item = QTreeWidgetItem([label, str(item.get("name") or item.get("slug") or item.get("id") or label), str(detail_fn(item))])
                    self.native_asset_tree.addTopLevelItem(tree_item)

        if hasattr(self, "resources_local_doplets_tree"):
            selected_resource_id = self._selected_resources_doplet_id()
            local_hosts = self._local_control_plane_hosts()
            local_host_ids = {int(item.get("id", 0)) for item in local_hosts}
            local_doplets = [item for item in self._local_control_plane_doplets() if int(item.get("host_id", 0) or 0) in local_host_ids]
            active_local = [item for item in local_doplets if str(item.get("status") or "").lower() not in {"deleted"}]
            self.resources_local_doplets_tree.blockSignals(True)
            self.resources_local_doplets_tree.clear()
            for doplet in active_local:
                host = next((item for item in local_hosts if _coerce_int(item.get("id")) == _coerce_int(doplet.get("host_id"))), {})
                item = QTreeWidgetItem(
                    [
                        str(doplet.get("name") or "Doplet"),
                        str(host.get("name") or "-"),
                        str(doplet.get("status") or "unknown"),
                        str(doplet.get("vcpu") or 0),
                        str(doplet.get("ram_mb") or 0),
                        str(doplet.get("disk_gb") or 0),
                    ]
                )
                item.setData(0, Qt.UserRole, int(doplet.get("id", 0)))
                self.resources_local_doplets_tree.addTopLevelItem(item)
                if selected_resource_id and int(doplet.get("id", 0)) == int(selected_resource_id):
                    self.resources_local_doplets_tree.setCurrentItem(item)
            self.resources_local_doplets_tree.blockSignals(False)
            if self.resources_local_doplets_tree.currentItem() is None and self.resources_local_doplets_tree.topLevelItemCount():
                self.resources_local_doplets_tree.setCurrentItem(self.resources_local_doplets_tree.topLevelItem(0))
            self._resources_doplet_selection_changed()

            local_host = local_hosts[0] if local_hosts else {}
            runtime_root = str(((local_host.get("config") or {}).get("runtime_root")) or "Main SSD / default runtime root")
            if hasattr(self, "current_machine_resources_body"):
                if local_host:
                    self.current_machine_resources_body.setText(
                        f"<b>{_escape(local_host.get('name') or 'Current machine')}</b><br>"
                        f"{_escape(self._format_capacity_line(local_host))}<br><br>"
                        f"Active VPS count: {len(active_local)}"
                    )
                else:
                    self.current_machine_resources_body.setText(
                        "No local host is configured yet. Use Host Admin to adopt this machine, capture inventory, and prepare it before creating a VPS."
                    )
            if hasattr(self, "current_machine_storage_body"):
                if local_host:
                    self.current_machine_storage_body.setText(
                        f"<b>Runtime root</b><br>{_escape(runtime_root)}<br><br>"
                        "For now, local Doplets are expected to use the main SSD through this one common root so storage does not end up scattered."
                    )
                else:
                    self.current_machine_storage_body.setText("Storage root will appear after a local host is saved and inventory is captured.")
            set_chip(self.resources_local_chip, (local_host.get("name") or "CURRENT MACHINE PENDING").upper()[:30] if local_host else "CURRENT MACHINE PENDING", "accent" if local_host else "warn")
            set_chip(self.resources_active_chip, f"{len(active_local)} ACTIVE VPS", "accent" if active_local else "neutral")
            set_chip(self.resources_storage_chip, "ROOT READY" if local_host else "ROOT UNKNOWN", "accent" if local_host else "neutral")

        if hasattr(self, "resources_remote_hosts_tree"):
            self.resources_remote_hosts_tree.blockSignals(True)
            self.resources_remote_hosts_tree.clear()
            for host in self._remote_control_plane_hosts():
                capacity_line = self._format_capacity_line(host)
                cpu_used = capacity_line.split("|")[0].strip()
                ram_used = capacity_line.split("|")[1].strip() if "|" in capacity_line else "-"
                item = QTreeWidgetItem(
                    [
                        str(host.get("name") or "Host"),
                        str(self._mode_label(host.get("host_mode") or host.get("mode"))),
                        str(host.get("status") or "unknown"),
                        cpu_used,
                        ram_used,
                    ]
                )
                item.setData(0, Qt.UserRole, int(host.get("id", 0)))
                self.resources_remote_hosts_tree.addTopLevelItem(item)
            self.resources_remote_hosts_tree.blockSignals(False)
            if self.resources_remote_hosts_tree.currentItem() is None and self.resources_remote_hosts_tree.topLevelItemCount():
                self.resources_remote_hosts_tree.setCurrentItem(self.resources_remote_hosts_tree.topLevelItem(0))
            self._resources_remote_host_selection_changed()

    def _control_plane_hosts(self) -> list[dict[str, Any]]:
        return list((self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("hosts", []))

    def _control_plane_doplets(self) -> list[dict[str, Any]]:
        return list((self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("doplets", []))

    def _archived_control_plane_doplets(self) -> list[dict[str, Any]]:
        return list((self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("archived_doplets", []))

    def _local_control_plane_hosts(self) -> list[dict[str, Any]]:
        local_targets = {str(item).strip().lower() for item in (self.local_machine or {}).get("ssh_targets", []) if str(item).strip()}
        local_targets.update({str(item).strip().lower() for item in (self.local_machine or {}).get("ip_candidates", []) if str(item).strip()})
        local_targets.add(str((self.local_machine or {}).get("hostname") or "").strip().lower())
        local_targets.add(str((self.local_machine or {}).get("fqdn") or "").strip().lower())
        local_hosts: list[dict[str, Any]] = []
        for host in self._control_plane_hosts():
            mode = str(host.get("host_mode") or host.get("mode") or "").strip().lower()
            ssh_host = str(host.get("ssh_host") or "").strip().lower()
            if mode in {"windows-local", "linux-local"} or (ssh_host and ssh_host in local_targets):
                local_hosts.append(host)
        return local_hosts

    def _remote_control_plane_hosts(self) -> list[dict[str, Any]]:
        local_ids = {int(item.get("id", 0)) for item in self._local_control_plane_hosts()}
        return [host for host in self._control_plane_hosts() if int(host.get("id", 0)) not in local_ids]

    def _local_control_plane_doplets(self) -> list[dict[str, Any]]:
        local_ids = {int(item.get("id", 0)) for item in self._local_control_plane_hosts()}
        return [item for item in self._control_plane_doplets() if int(item.get("host_id", 0) or 0) in local_ids]

    def _format_capacity_line(self, host: dict[str, Any]) -> str:
        capacity = dict(host.get("capacity") or {})
        totals = dict(capacity.get("totals") or {})
        remaining = dict(capacity.get("remaining") or {})
        total_cpu = int(totals.get("vcpu") or 0)
        remaining_cpu = int(remaining.get("vcpu") or 0)
        total_ram = int(totals.get("ram_mb") or 0)
        remaining_ram = int(remaining.get("ram_mb") or 0)
        total_disk = float(totals.get("disk_gb") or 0)
        remaining_disk = float(remaining.get("disk_gb") or 0)
        used_cpu = max(total_cpu - remaining_cpu, 0)
        used_ram = max(total_ram - remaining_ram, 0)
        used_disk = max(total_disk - remaining_disk, 0.0)
        return (
            f"{used_cpu}/{total_cpu} CPU used | "
            f"{used_ram}/{total_ram} MB RAM used | "
            f"{used_disk:.0f}/{total_disk:.0f} GB disk used"
        )

    def _control_plane_images(self) -> list[dict[str, Any]]:
        return list((self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("images", []))

    def _control_plane_flavors(self) -> list[dict[str, Any]]:
        return list((self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("flavors", []))

    def _active_control_plane_tasks(self) -> list[dict[str, Any]]:
        tasks = list((self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("tasks", []))
        active: list[dict[str, Any]] = []
        for task in tasks:
            status = str(task.get("status") or "").strip().lower()
            task_id = int(task.get("id", 0) or 0)
            root_id = self._task_root_id(task, {int(item.get("id", 0) or 0): item for item in tasks if int(item.get("id", 0) or 0)})
            if status in ARCHIVED_PLATFORM_TASK_STATUSES and root_id not in self._watched_task_roots and task_id not in self._watched_task_roots:
                continue
            active.append(task)
        return active

    def _archived_control_plane_tasks(self) -> list[dict[str, Any]]:
        tasks = list((self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("tasks", []))
        archived: list[dict[str, Any]] = []
        task_map = {int(item.get("id", 0) or 0): item for item in tasks if int(item.get("id", 0) or 0)}
        for task in tasks:
            status = str(task.get("status") or "").strip().lower()
            if status not in ARCHIVED_PLATFORM_TASK_STATUSES:
                continue
            root_id = self._task_root_id(task, task_map)
            if root_id in self._watched_task_roots or int(task.get("id", 0) or 0) in self._watched_task_roots:
                continue
            archived.append(task)
        return archived

    def _failing_control_plane_tasks(self) -> list[dict[str, Any]]:
        tasks = list((self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("tasks", []))
        failing = [dict(task) for task in tasks if str(task.get("status") or "").strip().lower() in {"failed", "cancelled"}]
        failing.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        return failing

    def _task_error_entry(self, task: dict[str, Any]) -> str:
        timestamp = str(task.get("updated_at") or task.get("created_at") or "").replace("T", " ")
        task_id = int(task.get("id", 0) or 0)
        task_type = str(task.get("task_type") or "task")
        status = str(task.get("status") or "unknown").upper()
        target = f"{task.get('target_type', '')} {task.get('target_id', '')}".strip() or "unscoped target"
        payload = dict(task.get("result_payload") or {})
        error_text = str(payload.get("error") or "").strip()
        log_output = str(task.get("log_output") or "").strip()
        sections = [
            f"[{timestamp}] Task {task_id} {task_type} {status}",
            f"Target: {target}",
        ]
        if error_text:
            sections.append(f"Error: {error_text}")
        if log_output:
            sections.extend(["", log_output])
        return "\n".join(section for section in sections if section is not None)

    def _refresh_error_console(self) -> None:
        if not hasattr(self, "error_output"):
            return
        task_entries = [self._task_error_entry(task) for task in self._failing_control_plane_tasks()[:12]]
        entries = list(self._error_events) + task_entries
        if not entries:
            self.error_output["output"].setPlainText("No errors logged yet.")
            return
        self.error_output["output"].setPlainText(("\n\n" + ("-" * 72) + "\n\n").join(entries))

    def _selected_platform_host_id(self) -> int | None:
        if not hasattr(self, "native_hosts_tree"):
            return None
        item = self.native_hosts_tree.currentItem()
        if not item:
            return None
        host_id = item.data(0, Qt.UserRole)
        return int(host_id) if host_id else None

    def _selected_native_doplet_id(self) -> int | None:
        if hasattr(self, "native_doplets_tree"):
            item = self.native_doplets_tree.currentItem()
            if item:
                doplet_id = item.data(0, Qt.UserRole)
                if doplet_id:
                    return int(doplet_id)
        return int(self.current_native_doplet_id) if self.current_native_doplet_id else None

    def _selected_resources_doplet_id(self) -> int | None:
        if hasattr(self, "resources_local_doplets_tree"):
            item = self.resources_local_doplets_tree.currentItem()
            if item:
                doplet_id = item.data(0, Qt.UserRole)
                if doplet_id:
                    return int(doplet_id)
        return None

    def _selected_resources_remote_host_id(self) -> int | None:
        if hasattr(self, "resources_remote_hosts_tree"):
            item = self.resources_remote_hosts_tree.currentItem()
            if item:
                host_id = item.data(0, Qt.UserRole)
                if host_id:
                    return int(host_id)
        return None

    def _doplet_by_id(self, doplet_id: int, *, local_only: bool = False) -> dict[str, Any] | None:
        source = self._local_control_plane_doplets() if local_only else self._control_plane_doplets()
        return next((item for item in source if _coerce_int(item.get("id")) == _coerce_int(doplet_id)), None)

    def _prompt_resize_spec(self, doplet: dict[str, Any]) -> dict[str, int] | None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Resize VPS {doplet.get('name', 'Doplet')}")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(
            make_wrap_label(
                "Choose the new VPS size. This updates the Doplet allocation and queues a real resize task on the selected host.",
                css_class="CardBody",
            )
        )
        form = QFormLayout()
        form.setSpacing(12)
        vcpu = QSpinBox(dialog)
        vcpu.setRange(1, 256)
        vcpu.setValue(int(doplet.get("vcpu") or 1))
        ram_mb = QSpinBox(dialog)
        ram_mb.setRange(256, 1048576)
        ram_mb.setSingleStep(256)
        ram_mb.setValue(int(doplet.get("ram_mb") or 1024))
        disk_gb = QSpinBox(dialog)
        disk_gb.setRange(1, 65536)
        disk_gb.setValue(int(doplet.get("disk_gb") or 20))
        form.addRow("vCPU", vcpu)
        form.addRow("RAM (MB)", ram_mb)
        form.addRow("Disk (GB)", disk_gb)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return None
        return {
            "vcpu": int(vcpu.value()),
            "ram_mb": int(ram_mb.value()),
            "disk_gb": int(disk_gb.value()),
        }

    def _reprovision_payload_for_doplet(self, doplet: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(doplet.get("metadata_json") or {})
        return {
            "id": doplet.get("id"),
            "name": doplet.get("name"),
            "slug": doplet.get("slug"),
            "host_id": doplet.get("host_id"),
            "image_id": doplet.get("image_id"),
            "flavor_id": doplet.get("flavor_id"),
            "primary_network_id": doplet.get("primary_network_id"),
            "network_ids": list(doplet.get("network_ids") or []),
            "vcpu": int(doplet.get("vcpu") or 1),
            "ram_mb": int(doplet.get("ram_mb") or 1024),
            "disk_gb": int(doplet.get("disk_gb") or 20),
            "storage_backend": doplet.get("storage_backend") or "files",
            "bootstrap_user": doplet.get("bootstrap_user") or "ubuntu",
            "bootstrap_password": str(metadata.get("bootstrap_password") or "").strip(),
            "ssh_public_keys": list(doplet.get("ssh_public_keys") or []),
            "gpu_assignments": list(doplet.get("gpu_assignments") or []),
            "metadata_json": metadata,
            "status": "draft",
        }

    def _native_host_changed(self, *_args: Any) -> None:
        host = next((item for item in self._control_plane_hosts() if int(item.get("id", 0)) == int(self.native_doplet_host.currentData() or 0)), None)
        backend = str((host or {}).get("primary_storage_backend") or "files")
        index = self.native_doplet_storage.findData(backend)
        if index >= 0:
            self.native_doplet_storage.setCurrentIndex(index)

    def _native_flavor_changed(self, *_args: Any) -> None:
        flavor = next((item for item in self._control_plane_flavors() if int(item.get("id", 0)) == int(self.native_doplet_flavor.currentData() or 0)), None)
        if not flavor:
            return
        self.native_doplet_vcpu.setValue(int(flavor.get("vcpu") or 1))
        self.native_doplet_ram.setValue(int(flavor.get("ram_mb") or 1024))
        self.native_doplet_disk.setValue(int(flavor.get("disk_gb") or 20))

    def _native_auth_mode_changed(self, *_args: Any) -> None:
        auth_mode = str(self.native_doplet_auth_mode.currentData() or "ssh")
        ssh_enabled = auth_mode in {"ssh", "password+ssh"}
        self.native_doplet_bootstrap_password.setEnabled(True)
        self.native_doplet_keys.setEnabled(ssh_enabled)
        if ssh_enabled:
            self.native_doplet_keys.setPlaceholderText("One public SSH key per line")
        else:
            self.native_doplet_keys.setPlaceholderText("SSH keys are disabled for password-only login")
        if auth_mode == "ssh":
            if not self.native_doplet_bootstrap_password.text().strip():
                self.native_doplet_bootstrap_password.setText("bypass")
            self.native_doplet_bootstrap_password.setPlaceholderText("Local/sudo password. SSH password login stays disabled in this mode.")
        elif auth_mode == "password+ssh":
            if not self.native_doplet_bootstrap_password.text().strip():
                self.native_doplet_bootstrap_password.setText("bypass")
            self.native_doplet_bootstrap_password.setPlaceholderText("Used for sudo, console login, and SSH password login.")
        else:
            if not self.native_doplet_bootstrap_password.text().strip():
                self.native_doplet_bootstrap_password.setText("bypass")
            self.native_doplet_bootstrap_password.setPlaceholderText("Used for password login and sudo.")

    def _resources_doplet_selection_changed(self) -> None:
        doplet_id = self._selected_resources_doplet_id()
        doplet = next((item for item in self._local_control_plane_doplets() if _coerce_int(item.get("id")) == _coerce_int(doplet_id)), None)
        if not doplet:
            if hasattr(self, "resources_local_doplet_detail"):
                self.resources_local_doplet_detail.setPlainText("Select a local Doplet to inspect its current CPU, RAM, disk, access, and lifecycle state.")
            return
        host = next((item for item in self._local_control_plane_hosts() if _coerce_int(item.get("id")) == _coerce_int(doplet.get("host_id"))), {})
        try:
            terminal = self.service.describe_doplet_terminal(int(doplet["id"]), establish_localhost_endpoint=False)
        except Exception as exc:
            terminal = {"supported": False, "reason": str(exc), "transport": "", "target": "", "preview_command": ""}
        detail = {
            "name": doplet.get("name"),
            "status": doplet.get("status"),
            "host": host.get("name"),
            "usage": {
                "vcpu": doplet.get("vcpu"),
                "ram_mb": doplet.get("ram_mb"),
                "disk_gb": doplet.get("disk_gb"),
            },
            "ips": doplet.get("ip_addresses", []),
            "bootstrap_user": doplet.get("bootstrap_user"),
            "storage_backend": doplet.get("storage_backend"),
            "runtime_root": (host.get("config") or {}).get("runtime_root") or "",
            "access": {
                "mode": terminal.get("transport") or ("pending" if not terminal.get("supported") else "unknown"),
                "label": terminal.get("access_label") or "",
                "target": terminal.get("target") or "",
                "ips": terminal.get("ip_addresses") or doplet.get("ip_addresses", []),
                "command": terminal.get("preview_command") or "",
                "note": terminal.get("access_note") or "",
                "reason": terminal.get("reason") or "",
            },
        }
        self.resources_local_doplet_detail.setPlainText(json.dumps(detail, indent=2))

    def _resources_remote_host_selection_changed(self) -> None:
        host_id = self._selected_resources_remote_host_id()
        host = next((item for item in self._remote_control_plane_hosts() if _coerce_int(item.get("id")) == _coerce_int(host_id)), None)
        if not host:
            if hasattr(self, "resources_remote_host_detail"):
                self.resources_remote_host_detail.setPlainText("Select a remote host to inspect its available CPU, RAM, disk, and storage posture separately from this machine.")
            return
        detail = {
            "name": host.get("name"),
            "mode": host.get("host_mode") or host.get("mode"),
            "status": host.get("status"),
            "capacity": host.get("capacity") or {},
            "storage_backend": host.get("primary_storage_backend"),
            "ssh_target": f"{host.get('ssh_user') or 'user'}@{host.get('ssh_host') or 'host'}:{host.get('ssh_port') or 22}",
        }
        self.resources_remote_host_detail.setPlainText(json.dumps(detail, indent=2))

    def _native_doplet_selection_changed(self) -> None:
        doplet_id = self._selected_native_doplet_id()
        doplet = next((item for item in self._control_plane_doplets() if _coerce_int(item.get("id")) == _coerce_int(doplet_id)), None)
        if not doplet:
            if hasattr(self, "native_doplet_detail"):
                self.native_doplet_detail.setPlainText("Select a Doplet to manage it natively.")
            return
        self.current_native_doplet_id = int(doplet["id"])
        host = next((item for item in self._control_plane_hosts() if _coerce_int(item.get("id")) == _coerce_int(doplet.get("host_id"))), {})
        image = next((item for item in self._control_plane_images() if _coerce_int(item.get("id")) == _coerce_int(doplet.get("image_id"))), {})
        try:
            terminal = self.service.describe_doplet_terminal(int(doplet["id"]), establish_localhost_endpoint=False)
        except Exception as exc:
            terminal = {"supported": False, "reason": str(exc), "transport": "", "target": "", "preview_command": ""}
        summary = {
            "name": doplet.get("name"),
            "status": doplet.get("status"),
            "host": host.get("name"),
            "image": image.get("name"),
            "size": f"{doplet.get('vcpu', 0)} CPU / {doplet.get('ram_mb', 0)} MB / {doplet.get('disk_gb', 0)} GB",
            "network": doplet.get("primary_network_id"),
            "ips": doplet.get("ip_addresses", []),
            "bootstrap_user": doplet.get("bootstrap_user"),
            "auth_mode": self._doplet_auth_mode(doplet),
            "access": {
                "mode": terminal.get("transport") or ("pending" if not terminal.get("supported") else "unknown"),
                "label": terminal.get("access_label") or "",
                "target": terminal.get("target") or "",
                "ips": terminal.get("ip_addresses") or doplet.get("ip_addresses", []),
                "command": terminal.get("preview_command") or "",
                "note": terminal.get("access_note") or "",
                "reason": terminal.get("reason") or "",
            },
        }
        if hasattr(self, "native_doplet_detail"):
            self.native_doplet_detail.setPlainText(json.dumps(summary, indent=2))
        if hasattr(self, "native_doplet_summary"):
            self.native_doplet_summary.setPlainText(json.dumps(summary, indent=2))
        self._fill_native_doplet_form(doplet)

    def _fill_native_doplet_form(self, doplet: dict[str, Any]) -> None:
        self.native_doplet_name.setText(str(doplet.get("name") or ""))
        self.native_doplet_slug.setText(str(doplet.get("slug") or ""))
        for widget, value in (
            (self.native_doplet_host, doplet.get("host_id")),
            (self.native_doplet_image, doplet.get("image_id")),
            (self.native_doplet_flavor, doplet.get("flavor_id")),
            (self.native_doplet_network, doplet.get("primary_network_id")),
            (self.native_doplet_storage, doplet.get("storage_backend")),
        ):
            index = widget.findData(value)
            if index >= 0:
                widget.setCurrentIndex(index)
        self.native_doplet_vcpu.setValue(int(doplet.get("vcpu") or 1))
        self.native_doplet_ram.setValue(int(doplet.get("ram_mb") or 1024))
        self.native_doplet_disk.setValue(int(doplet.get("disk_gb") or 20))
        self.native_doplet_bootstrap_user.setText(str(doplet.get("bootstrap_user") or "ubuntu"))
        bootstrap_password = str(doplet.get("bootstrap_password") or "bypass")
        auth_mode = self._doplet_auth_mode(doplet)
        auth_index = self.native_doplet_auth_mode.findData(auth_mode)
        if auth_index >= 0:
            self.native_doplet_auth_mode.setCurrentIndex(auth_index)
        self.native_doplet_bootstrap_password.setText(bootstrap_password)
        self.native_doplet_keys.setPlainText("\n".join(doplet.get("ssh_public_keys") or []))
        self._native_auth_mode_changed()

    def _collect_native_doplet_payload(self) -> dict[str, Any]:
        auth_mode = str(self.native_doplet_auth_mode.currentData() or "ssh")
        include_keys = auth_mode in {"ssh", "password+ssh"}
        keys = [line.strip() for line in self.native_doplet_keys.toPlainText().splitlines() if line.strip()] if include_keys else []
        bootstrap_password = self.native_doplet_bootstrap_password.text().strip() or "bypass"
        metadata_json: dict[str, Any] = {"auth_mode": auth_mode}
        existing = next(
            (item for item in self._control_plane_doplets() if _coerce_int(item.get("id")) == _coerce_int(self.current_native_doplet_id)),
            None,
        )
        local_private_key_path = self._matching_local_private_key_path(keys)
        if local_private_key_path:
            metadata_json["local_private_key_path"] = local_private_key_path
        payload: dict[str, Any] = {
            "id": self.current_native_doplet_id,
            "name": self.native_doplet_name.text().strip() or "New Doplet",
            "slug": self.native_doplet_slug.text().strip() or self.native_doplet_name.text().strip() or "doplet",
            "host_id": self.native_doplet_host.currentData(),
            "image_id": self.native_doplet_image.currentData(),
            "flavor_id": self.native_doplet_flavor.currentData(),
            "primary_network_id": self.native_doplet_network.currentData(),
            "vcpu": self.native_doplet_vcpu.value(),
            "ram_mb": self.native_doplet_ram.value(),
            "disk_gb": self.native_doplet_disk.value(),
            "storage_backend": self.native_doplet_storage.currentData(),
            "bootstrap_user": self.native_doplet_bootstrap_user.text().strip() or "ubuntu",
            "bootstrap_password": bootstrap_password,
            "ssh_public_keys": keys,
            "metadata_json": metadata_json,
            "status": str((existing or {}).get("status") or "draft"),
        }
        if payload["primary_network_id"] in {"", None}:
            payload.pop("primary_network_id")
        return payload

    def _matching_local_private_key_path(self, keys: list[str]) -> str:
        normalized = {str(item or "").strip() for item in keys if str(item or "").strip()}
        if not normalized:
            return ""
        for entry in (self.local_machine or {}).get("ssh_public_keys") or []:
            public_key = str(entry.get("public_key") or "").strip()
            if public_key and public_key in normalized:
                private_key_path = str(entry.get("private_key_path") or "").strip()
                if private_key_path:
                    return private_key_path
                public_key_path = str(entry.get("path") or "").strip()
                if public_key_path.lower().endswith(".pub"):
                    return public_key_path[:-4]
        return ""

    def _doplet_auth_mode(self, doplet: dict[str, Any]) -> str:
        metadata = dict(doplet.get("metadata_json") or {})
        stored = str(metadata.get("auth_mode") or "").strip()
        if stored in {"password", "ssh", "password+ssh"}:
            return stored
        if str(doplet.get("bootstrap_password") or "").strip() and not (doplet.get("ssh_public_keys") or []):
            return "password"
        has_password = bool(str(doplet.get("bootstrap_password") or "").strip())
        has_keys = bool(doplet.get("ssh_public_keys") or [])
        if has_password and has_keys:
            return "password+ssh"
        if has_keys:
            return "ssh"
        return "password"

    def _save_native_doplet(self) -> None:
        try:
            result = self.service.upsert_doplet(self._collect_native_doplet_payload(), actor="desktop")
            doplet = result
            self.current_native_doplet_id = int(doplet.get("id", 0))
            self._load_bootstrap()
            self._set_status(f"Saved Doplet {doplet.get('name', 'draft')}", 4000)
        except Exception as exc:
            self._show_error("Save Doplet failed", str(exc), traceback.format_exc())

    def _create_native_doplet(self) -> None:
        try:
            doplet = self.service.upsert_doplet(self._collect_native_doplet_payload(), actor="desktop")
            self.current_native_doplet_id = int(doplet.get("id", 0))
            self._run_async_task(
                start_message=f"Creating Doplet {doplet.get('name', '')}...",
                work=lambda: self._launch_queued_platform_task(
                    lambda: self.service.queue_doplet_create(int(doplet["id"]), actor="desktop"),
                    actor="desktop",
                ),
                on_success=lambda result: (self._watch_platform_task(int((result or {}).get("task_id", 0))), self._load_bootstrap()),
                error_title="Create Doplet failed",
                done_message="Doplet create started",
            )
        except Exception as exc:
            self._show_error("Create Doplet failed", str(exc), traceback.format_exc())

    def _terminal_command_for_selected_native_doplet(self) -> str:
        doplet_id = self._selected_native_doplet_id()
        if not doplet_id:
            raise ValueError("Choose a Doplet first.")
        terminal = self.service.describe_doplet_terminal(doplet_id, establish_localhost_endpoint=False)
        if not terminal.get("supported"):
            raise ValueError(str(terminal.get("reason") or "Terminal is not available yet."))
        return str(terminal.get("preview_command") or "")

    def _ip_text_for_doplet(self, doplet_id: int) -> tuple[str, dict[str, Any]]:
        details = self.service.describe_doplet_terminal(doplet_id, establish_localhost_endpoint=False)
        ips = [str(item).strip() for item in details.get("ip_addresses") or [] if str(item).strip()]
        if not ips and not str(details.get("forward_host") or "").strip():
            raise ValueError(str(details.get("reason") or "No guest IP has been detected yet."))
        lines: list[str] = []
        if ips:
            lines.append("Guest IPs:")
            lines.extend(ips)
        forward_host = str(details.get("forward_host") or "").strip()
        forward_port = str(details.get("forward_port") or "").strip()
        if forward_host and forward_port:
            if lines:
                lines.append("")
            lines.append("Local SSH endpoint:")
            lines.append(f"{forward_host}:{forward_port}")
        access_label = str(details.get("access_label") or "").strip()
        preview_command = str(details.get("preview_command") or "").strip()
        if access_label:
            if lines:
                lines.append("")
            lines.append("Access:")
            lines.append(access_label)
        if preview_command:
            if lines:
                lines.append("")
            lines.append("Command:")
            lines.append(preview_command)
        note = str(details.get("access_note") or "").strip()
        ip_text = "\n".join(lines)
        if note:
            ip_text = f"{ip_text}\n\n{note}"
        return ip_text, details

    def _show_selected_native_doplet_ips(self) -> None:
        doplet_id = self._selected_native_doplet_id()
        if not doplet_id:
            self._set_status("Choose a Doplet first", 3000)
            return
        try:
            ip_text, details = self._ip_text_for_doplet(doplet_id)
            QMessageBox.information(self, "Doplet IPs", ip_text)
            self._set_status(f"Showing IPs for {details.get('access_label') or 'selected Doplet'}", 4000)
        except Exception as exc:
            self._show_error("Show IPs failed", str(exc), traceback.format_exc())

    def _copy_selected_native_doplet_ips(self) -> None:
        doplet_id = self._selected_native_doplet_id()
        if not doplet_id:
            self._set_status("Choose a Doplet first", 3000)
            return
        try:
            ip_text, _details = self._ip_text_for_doplet(doplet_id)
            QApplication.clipboard().setText(ip_text)
            self._set_status("Doplet IPs copied", 4000)
        except Exception as exc:
            self._show_error("Copy IPs failed", str(exc), traceback.format_exc())

    def _open_selected_native_doplet_terminal(self) -> None:
        doplet_id = self._selected_native_doplet_id()
        if not doplet_id:
            self._set_status("Choose a Doplet first", 3000)
            return
        try:
            details = self.service.describe_doplet_terminal(doplet_id, establish_localhost_endpoint=True)
            access_label = str(details.get("access_label") or details.get("transport") or "terminal")
        except Exception:
            access_label = "terminal"
        self._run_async_task(
            start_message=f"Opening {access_label} for Doplet {doplet_id}...",
            work=lambda: self.service.open_doplet_terminal(doplet_id, actor="desktop"),
            on_success=lambda _result: self._set_status("Terminal launch requested", 4000),
            error_title="Open terminal failed",
            done_message="Terminal opened",
        )

    def _copy_selected_native_doplet_terminal(self) -> None:
        try:
            command = self._terminal_command_for_selected_native_doplet()
            QApplication.clipboard().setText(command)
            self._set_status("Terminal command copied", 4000)
        except Exception as exc:
            self._show_error("Copy terminal command failed", str(exc), traceback.format_exc())

    def _terminal_command_for_selected_resources_doplet(self) -> str:
        doplet_id = self._selected_resources_doplet_id()
        if not doplet_id:
            raise ValueError("Choose a local Doplet first.")
        terminal = self.service.describe_doplet_terminal(doplet_id, establish_localhost_endpoint=False)
        if not terminal.get("supported"):
            raise ValueError(str(terminal.get("reason") or "Terminal is not available yet."))
        return str(terminal.get("preview_command") or "")

    def _show_selected_resources_doplet_ips(self) -> None:
        doplet_id = self._selected_resources_doplet_id()
        if not doplet_id:
            self._set_status("Choose a local Doplet first", 3000)
            return
        try:
            ip_text, details = self._ip_text_for_doplet(doplet_id)
            QMessageBox.information(self, "Doplet IPs", ip_text)
            self._set_status(f"Showing IPs for {details.get('access_label') or 'selected Doplet'}", 4000)
        except Exception as exc:
            self._show_error("Show IPs failed", str(exc), traceback.format_exc())

    def _copy_selected_resources_doplet_ips(self) -> None:
        doplet_id = self._selected_resources_doplet_id()
        if not doplet_id:
            self._set_status("Choose a local Doplet first", 3000)
            return
        try:
            ip_text, _details = self._ip_text_for_doplet(doplet_id)
            QApplication.clipboard().setText(ip_text)
            self._set_status("Doplet IPs copied", 4000)
        except Exception as exc:
            self._show_error("Copy IPs failed", str(exc), traceback.format_exc())

    def _open_selected_resources_doplet_terminal(self) -> None:
        doplet_id = self._selected_resources_doplet_id()
        if not doplet_id:
            self._set_status("Choose a local Doplet first", 3000)
            return
        try:
            details = self.service.describe_doplet_terminal(doplet_id, establish_localhost_endpoint=True)
            access_label = str(details.get("access_label") or details.get("transport") or "terminal")
        except Exception:
            access_label = "terminal"
        self._run_async_task(
            start_message=f"Opening {access_label} for Doplet {doplet_id}...",
            work=lambda: self.service.open_doplet_terminal(doplet_id, actor="desktop"),
            on_success=lambda _result: self._set_status("Terminal launch requested", 4000),
            error_title="Open terminal failed",
            done_message="Terminal opened",
        )

    def _copy_selected_resources_doplet_terminal(self) -> None:
        try:
            command = self._terminal_command_for_selected_resources_doplet()
            QApplication.clipboard().setText(command)
            self._set_status("Terminal command copied", 4000)
        except Exception as exc:
            self._show_error("Copy terminal command failed", str(exc), traceback.format_exc())

    def _queue_resources_doplet_lifecycle(self, action: str) -> None:
        doplet_id = self._selected_resources_doplet_id()
        if not doplet_id:
            self._set_status("Choose a local Doplet first", 3000)
            return
        self._run_async_task(
            start_message=f"Starting {action} for Doplet {doplet_id}...",
            work=lambda: self._launch_queued_platform_task(
                lambda: self.service.queue_doplet_lifecycle(doplet_id, action, actor="desktop"),
                actor="desktop",
            ),
            on_success=lambda result: (self._watch_platform_task(int((result or {}).get("task_id", 0))), self._load_bootstrap()),
            error_title=f"{action.title()} Doplet failed",
            done_message=f"{action.title()} started",
        )

    def _delete_selected_resources_doplet(self) -> None:
        resource_id = self._selected_resources_doplet_id()
        if not resource_id:
            self._set_status("Choose a local Doplet first", 3000)
            return
        doplet = next((item for item in self._local_control_plane_doplets() if _coerce_int(item.get("id")) == _coerce_int(resource_id)), None)
        if not doplet:
            self._set_status("Selected Doplet is no longer available", 4000)
            return
        self._delete_doplet_with_confirmation(resource_id, doplet)

    def _resize_selected_resources_doplet(self) -> None:
        doplet_id = self._selected_resources_doplet_id()
        if not doplet_id:
            self._set_status("Choose a local Doplet first", 3000)
            return
        doplet = self._doplet_by_id(doplet_id, local_only=True)
        if not doplet:
            self._set_status("Selected Doplet is no longer available", 4000)
            return
        resize_payload = self._prompt_resize_spec(doplet)
        if not resize_payload:
            return
        self._run_async_task(
            start_message=f"Resizing VPS {doplet.get('name', '')}...",
            work=lambda: self._launch_queued_platform_task(
                lambda: self.service.queue_doplet_resize(doplet_id, resize_payload, actor="desktop"),
                actor="desktop",
            ),
            on_success=lambda result: (self._watch_platform_task(int((result or {}).get("task_id", 0))), self._load_bootstrap()),
            error_title="Resize VPS failed",
            done_message="Resize started",
        )

    def _reprovision_selected_resources_doplet(self) -> None:
        doplet_id = self._selected_resources_doplet_id()
        if not doplet_id:
            self._set_status("Choose a local Doplet first", 3000)
            return
        doplet = self._doplet_by_id(doplet_id, local_only=True)
        if not doplet:
            self._set_status("Selected Doplet is no longer available", 4000)
            return
        if QMessageBox.warning(
            self,
            "Reprovision VPS",
            (
                f"Reprovision VPS {doplet.get('name', 'Doplet')}?\n\n"
                "This will rebuild the VPS from the saved Doplet spec and can destroy runtime data that is not backed up."
            ),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        reprovision_payload = self._reprovision_payload_for_doplet(doplet)
        self._run_async_task(
            start_message=f"Reprovisioning VPS {doplet.get('name', '')}...",
            work=lambda: self._launch_queued_platform_task(
                lambda: self.service.queue_doplet_create(
                    int(self.service.upsert_doplet(reprovision_payload, actor="desktop")["id"]),
                    actor="desktop",
                ),
                actor="desktop",
            ),
            on_success=lambda result: (self._watch_platform_task(int((result or {}).get("task_id", 0))), self._load_bootstrap()),
            error_title="Reprovision VPS failed",
            done_message="Reprovision started",
        )

    def _capture_selected_or_local_platform_host_inventory(self) -> None:
        host_id = self._selected_platform_host_id()
        if not host_id:
            local_hosts = self._local_control_plane_hosts()
            host_id = int(local_hosts[0].get("id", 0)) if local_hosts else None
        if not host_id:
            self._set_status("No local host is configured yet", 3000)
            return
        self._run_async_task(
            start_message=f"Capturing inventory for host {host_id}...",
            work=lambda: self.service.capture_platform_host_inventory(host_id, actor="desktop"),
            on_success=lambda _result: self._load_bootstrap(),
            error_title="Capture inventory failed",
            done_message="Inventory captured",
        )

    def _reclaim_selected_or_local_platform_host_runtime(self) -> None:
        host_id = self._selected_platform_host_id()
        if not host_id:
            local_hosts = self._local_control_plane_hosts()
            host_id = int(local_hosts[0].get("id", 0)) if local_hosts else None
        if not host_id:
            self._set_status("No local host is configured yet", 3000)
            return
        self._run_async_task(
            start_message=f"Reclaiming WSL runtime for host {host_id}...",
            work=lambda: self.service.reclaim_platform_host_runtime(host_id, actor="desktop"),
            on_success=lambda _result: self._load_bootstrap(),
            error_title="Reclaim WSL memory failed",
            done_message="Requested WSL runtime reclaim",
        )

    def _queue_native_doplet_lifecycle(self, action: str) -> None:
        doplet_id = self._selected_native_doplet_id()
        if not doplet_id:
            self._set_status("Choose a Doplet first", 3000)
            return
        self._run_async_task(
            start_message=f"Starting {action} for Doplet {doplet_id}...",
            work=lambda: self._launch_queued_platform_task(
                lambda: self.service.queue_doplet_lifecycle(doplet_id, action, actor="desktop"),
                actor="desktop",
            ),
            on_success=lambda result: (self._watch_platform_task(int((result or {}).get("task_id", 0))), self._load_bootstrap()),
            error_title=f"{action.title()} Doplet failed",
            done_message=f"{action.title()} started",
        )

    def _resize_selected_native_doplet(self) -> None:
        doplet_id = self._selected_native_doplet_id()
        if not doplet_id:
            self._set_status("Choose a Doplet first", 3000)
            return
        doplet = self._doplet_by_id(doplet_id)
        if not doplet:
            self._set_status("Selected Doplet is no longer available", 4000)
            return
        resize_payload = self._prompt_resize_spec(doplet)
        if not resize_payload:
            return
        self._run_async_task(
            start_message=f"Resizing VPS {doplet.get('name', '')}...",
            work=lambda: self._launch_queued_platform_task(
                lambda: self.service.queue_doplet_resize(doplet_id, resize_payload, actor="desktop"),
                actor="desktop",
            ),
            on_success=lambda result: (self._watch_platform_task(int((result or {}).get("task_id", 0))), self._load_bootstrap()),
            error_title="Resize VPS failed",
            done_message="Resize started",
        )

    def _reprovision_selected_native_doplet(self) -> None:
        doplet_id = self._selected_native_doplet_id()
        if not doplet_id:
            self._set_status("Choose a Doplet first", 3000)
            return
        doplet = self._doplet_by_id(doplet_id)
        if not doplet:
            self._set_status("Selected Doplet is no longer available", 4000)
            return
        if QMessageBox.warning(
            self,
            "Reprovision VPS",
            (
                f"Reprovision VPS {doplet.get('name', 'Doplet')}?\n\n"
                "This will rebuild the VPS from the saved Doplet spec and can destroy runtime data that is not backed up."
            ),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        reprovision_payload = self._reprovision_payload_for_doplet(doplet)
        self._run_async_task(
            start_message=f"Reprovisioning VPS {doplet.get('name', '')}...",
            work=lambda: self._launch_queued_platform_task(
                lambda: self.service.queue_doplet_create(
                    int(self.service.upsert_doplet(reprovision_payload, actor="desktop")["id"]),
                    actor="desktop",
                ),
                actor="desktop",
            ),
            on_success=lambda result: (self._watch_platform_task(int((result or {}).get("task_id", 0))), self._load_bootstrap()),
            error_title="Reprovision VPS failed",
            done_message="Reprovision started",
        )

    def _delete_selected_native_doplet(self) -> None:
        doplet_id = self._selected_native_doplet_id()
        if not doplet_id:
            self._set_status("Choose a Doplet first", 3000)
            return
        doplet = next((item for item in self._control_plane_doplets() if _coerce_int(item.get("id")) == _coerce_int(doplet_id)), None)
        if not doplet:
            self._set_status("Selected Doplet is no longer available", 4000)
            return
        self._delete_doplet_with_confirmation(doplet_id, doplet)

    def _delete_doplet_with_confirmation(self, doplet_id: int, doplet: dict[str, Any]) -> None:
        backup_count = len([item for item in (self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("backups", []) if int(item.get("doplet_id", 0) or 0) == int(doplet_id)])
        snapshot_count = len([item for item in (self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("snapshots", []) if int(item.get("doplet_id", 0) or 0) == int(doplet_id)])

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("Delete VPS")
        dialog.setText(f"Delete VPS {doplet.get('name', 'Doplet')}?")
        dialog.setInformativeText(
            "This will destroy the VPS runtime and then remove it from VPSdash.\n\n"
            f"Known backups: {backup_count}\n"
            f"Known snapshots: {snapshot_count}\n\n"
            "Choose backup first if you are not completely sure."
        )
        backup_delete_button = dialog.addButton("Backup Then Delete", QMessageBox.AcceptRole)
        delete_now_button = dialog.addButton("Delete VPS Now", QMessageBox.DestructiveRole)
        cancel_button = dialog.addButton(QMessageBox.Cancel)
        dialog.setDefaultButton(backup_delete_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked == cancel_button or clicked is None:
            return

        def _delete_vps(include_backup: bool) -> dict[str, Any]:
            result: dict[str, Any] = {"doplet_id": doplet_id, "backup": None, "delete": None, "warnings": []}
            if include_backup:
                try:
                    backup_task = self.service.queue_doplet_backup(doplet_id, actor="desktop")
                    backup_run = self.service.run_platform_task(int(backup_task["id"]), actor="desktop", dry_run=False)
                    result["backup"] = backup_run
                    if str(backup_run.get("status") or "").lower() not in {"completed", "ok", "success", "succeeded"}:
                        result["warnings"].append("Backup did not complete successfully before delete.")
                except Exception as exc:
                    result["warnings"].append(f"Backup attempt failed before delete: {exc}")
            delete_result = self.service.force_delete_platform_doplet(doplet_id, actor="desktop")
            result["delete"] = delete_result
            result["warnings"].extend(list(delete_result.get("warnings") or []))
            return result

        include_backup = clicked == backup_delete_button
        self._run_async_task(
            start_message=f"{'Backing up and d' if include_backup else 'D'}eleting VPS {doplet.get('name', '')}...",
            work=lambda: _delete_vps(include_backup),
            on_success=lambda result: (self._load_bootstrap(), self._set_status("VPS deleted with warnings" if (result or {}).get("warnings") else "VPS deleted", 5000)),
            error_title="Delete VPS failed",
            done_message="VPS deleted",
        )

    def _capture_selected_platform_host_inventory(self) -> None:
        host_id = self._selected_platform_host_id()
        if not host_id:
            self._set_status("Choose a host first", 3000)
            return
        self._run_async_task(
            start_message=f"Capturing inventory for host {host_id}...",
            work=lambda: self.service.capture_platform_host_inventory(host_id, actor="desktop"),
            on_success=lambda _result: self._load_bootstrap(),
            error_title="Capture inventory failed",
            done_message="Inventory captured",
        )

    def _prepare_selected_platform_host(self) -> None:
        host_id = self._selected_platform_host_id()
        if not host_id:
            self._set_status("Choose a host first", 3000)
            return
        selected_host = next((item for item in self._control_plane_hosts() if int(item.get("id", 0)) == int(host_id)), None)
        if selected_host and str(selected_host.get("host_mode") or selected_host.get("mode") or "").strip().lower() == "windows-local":
            wsl_state = self._windows_local_wsl_state(selected_host)
            if not wsl_state.get("distro_ready"):
                self._set_status("WSL is not ready yet on this machine. Run Initial Setup first and finish any WSL install/reboot prompts.", 7000)
                return
        self._run_async_task(
            start_message=f"Preparing host {host_id}...",
            work=lambda: self._launch_queued_platform_task(
                lambda: self.service.queue_prepare_platform_host(host_id, actor="desktop"),
                actor="desktop",
            ),
            on_success=lambda result: (self._watch_platform_task(int((result or {}).get("task_id", 0))), self._load_bootstrap()),
            error_title="Prepare host failed",
            done_message="Host preparation started",
        )

    def _reclaim_selected_platform_host_runtime(self) -> None:
        host_id = self._selected_platform_host_id()
        if not host_id:
            self._set_status("Choose a host first", 3000)
            return
        self._run_async_task(
            start_message=f"Reclaiming WSL runtime for host {host_id}...",
            work=lambda: self.service.reclaim_platform_host_runtime(host_id, actor="desktop"),
            on_success=lambda _result: self._load_bootstrap(),
            error_title="Reclaim WSL memory failed",
            done_message="Requested WSL runtime reclaim",
        )

    def _selected_native_task_id(self) -> int | None:
        if not hasattr(self, "native_task_tree"):
            return None
        item = self.native_task_tree.currentItem()
        if not item:
            return None
        task_id = item.data(0, Qt.UserRole)
        return int(task_id) if task_id else None

    def _native_task_selection_changed(self) -> None:
        task_id = self._selected_native_task_id()
        control_plane = self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}
        task = next((item for item in control_plane.get("tasks", []) if int(item.get("id", 0)) == int(task_id or 0)), None)
        if not task:
            self.native_task_detail.setPlainText("Select a task to inspect it.")
            if hasattr(self, "execution_output"):
                self.execution_output["output"].setPlainText("Select a task to inspect its command output.")
            self.native_task_launch_button.setEnabled(False)
            self.native_task_cancel_button.setEnabled(False)
            self.native_task_retry_button.setEnabled(False)
            return
        payload = dict(task.get("result_payload") or {})
        log_output = str(task.get("log_output") or "").strip()
        self.native_task_detail.setPlainText(
            json.dumps(
                {
                    "task": task,
                    "result_payload": payload,
                    "log_output": log_output,
                },
                indent=2,
            )
        )
        if hasattr(self, "execution_output"):
            execution_text = log_output or json.dumps(payload, indent=2) or "No command output captured for this task yet."
            self.execution_output["output"].setPlainText(execution_text)
        status = str(task.get("status") or "")
        self.native_task_launch_button.setEnabled(status not in {"running", "cancel-requested", "cancelled"})
        self.native_task_cancel_button.setEnabled(status in {"planned", "queued", "running", "cancel-requested"})
        self.native_task_retry_button.setEnabled(status in {"failed", "cancelled"})

    def _launch_selected_platform_task(self) -> None:
        task_id = self._selected_native_task_id()
        if not task_id:
            self._set_status("Choose a control-plane task first", 3000)
            return
        self._run_async_task(
            start_message=f"Launching task {task_id}...",
            work=lambda: self.service.launch_platform_task(task_id, actor="desktop", dry_run=False),
            on_success=lambda result: (self._watch_platform_task(int(task_id)), self._load_bootstrap()),
            error_title="Launch task failed",
            done_message="Task launched",
        )

    def _cancel_selected_platform_task(self) -> None:
        task_id = self._selected_native_task_id()
        if not task_id:
            self._set_status("Choose a control-plane task first", 3000)
            return
        self._run_async_task(
            start_message=f"Cancelling task {task_id}...",
            work=lambda: self.service.cancel_platform_task(task_id, actor="desktop"),
            on_success=lambda _result: self._load_bootstrap(),
            error_title="Cancel task failed",
            done_message="Task cancel processed",
        )

    def _retry_selected_platform_task(self) -> None:
        task_id = self._selected_native_task_id()
        if not task_id:
            self._set_status("Choose a control-plane task first", 3000)
            return
        self._run_async_task(
            start_message=f"Retrying task {task_id}...",
            work=lambda: self._retry_and_launch_platform_task(task_id, actor="desktop"),
            on_success=lambda result: (self._watch_platform_task(int((result or {}).get("task_id", 0))), self._load_bootstrap()),
            error_title="Retry task failed",
            done_message="Retry started",
        )

    def _selected_default_from_ui(self) -> dict[str, Any] | None:
        default_id = self.default_select.currentData() if hasattr(self, "default_select") else None
        if not default_id:
            return None
        return next((item for item in self.defaults if item.get("id") == default_id), None)

    def _default_selection_changed(self, *_args: Any) -> None:
        self.selected_default = self._selected_default_from_ui()
        default_item = self.selected_default
        if not default_item:
            self.default_name.setText("")
            self.default_description.setText("")
            self.default_preview_output.setHtml("No default selected.")
            self.default_activity_output.setHtml("Load a default to see the fill activity log.")
            set_chip(self.default_kind_chip, "NO DEFAULT SELECTED", "neutral")
            set_chip(self.default_source_chip, "PROJECT + HOST PRESET", "neutral")
            self.default_update_button.setEnabled(False)
            return

        self.default_name.setText(default_item.get("name", ""))
        self.default_description.setText(default_item.get("description", ""))
        self.default_update_button.setEnabled(default_item.get("kind") == "custom")
        set_chip(
            self.default_kind_chip,
            "BUILT-IN DEFAULT" if default_item.get("kind") == "builtin" else "CUSTOM DEFAULT",
            "accent" if default_item.get("kind") == "builtin" else "success",
        )
        source = default_item.get("source_template_id") or default_item.get("project_defaults", {}).get("template_id") or "generic"
        set_chip(self.default_source_chip, f"TEMPLATE {str(source).upper()}"[:30], "neutral")
        self.default_preview_output.setHtml(self._default_preview_html(default_item))
        self.default_activity_output.setHtml(
            "Click <b>Load Default</b> to watch fields populate here. You can edit every field afterward."
        )

    def _default_preview_html(self, default_item: dict[str, Any]) -> str:
        entries = self._default_entries(default_item)
        if not entries:
            return "This default does not prefill anything yet."
        lines = "".join(f"<li><b>{_escape(label)}</b>: {_escape(value)}</li>" for label, value in entries)
        return f"<ul>{lines}</ul>"

    def _default_entries(self, default_item: dict[str, Any]) -> list[tuple[str, str]]:
        host_defaults = default_item.get("host_defaults", {})
        project_defaults = default_item.get("project_defaults", {})
        entries: list[tuple[str, str]] = []
        host_labels = [
            ("Host label", host_defaults.get("name")),
            ("Mode", self._mode_label(host_defaults.get("mode"))),
            ("Device role", self._device_role_label(host_defaults.get("device_role"))),
            ("Bootstrap auth", self._bootstrap_auth_label(host_defaults.get("bootstrap_auth"))),
            ("SSH user", host_defaults.get("ssh_user")),
            ("SSH host", host_defaults.get("ssh_host")),
            ("SSH port", host_defaults.get("ssh_port")),
            ("SSH key path", host_defaults.get("ssh_key_path")),
            ("WSL distro", host_defaults.get("wsl_distribution")),
        ]
        project_labels = [
            ("Project", project_defaults.get("name")),
            ("Template", project_defaults.get("template_id")),
            ("Repository URL", project_defaults.get("repo_url")),
            ("Branch", project_defaults.get("branch")),
            ("Deploy path", project_defaults.get("deploy_path")),
            ("Primary domain", project_defaults.get("primary_domain")),
            ("TLS email", project_defaults.get("letsencrypt_email")),
        ]
        for label, value in host_labels + project_labels:
            if value not in (None, ""):
                entries.append((label, str(value)))
        domains = project_defaults.get("domains", [])
        if domains:
            entries.append(("Additional domains", ", ".join(str(domain) for domain in domains)))
        env_items = project_defaults.get("env", [])
        if env_items:
            env_keys = ", ".join(item.get("key", "") for item in env_items if item.get("key"))
            if env_keys:
                entries.append(("Environment keys", env_keys))
        return entries

    def _collect_default_payload(self, existing_id: str | None = None) -> dict[str, Any]:
        project_payload = self._collect_project()
        project_payload["id"] = None
        host_payload = self._collect_host()
        host_payload["id"] = None
        return {
            "id": existing_id,
            "name": self.default_name.text().strip() or f"{project_payload.get('name', 'Project')} Default",
            "description": self.default_description.text().strip() or "Custom setup default saved from the current host and project form.",
            "host_defaults": host_payload,
            "project_defaults": project_payload,
            "source_template_id": project_payload.get("template_id"),
        }

    def _save_default_as_new(self) -> None:
        try:
            response = self.service.upsert_default(self._collect_default_payload())
            saved_default = response["default"]
            self.defaults = self.bootstrap_data.get("defaults", [])
            self.bootstrap_data = self.service.bootstrap()
            self.defaults = self.bootstrap_data["defaults"]
            self._populate_defaults()
            index = self.default_select.findData(saved_default["id"])
            if index >= 0:
                self.default_select.setCurrentIndex(index)
            self._set_status(f"Saved default {saved_default['name']}", 4000)
        except Exception as exc:
            self._show_error("Save default failed", str(exc), traceback.format_exc())

    def _update_selected_default(self) -> None:
        default_item = self._selected_default_from_ui()
        if not default_item:
            self._set_status("Choose a default first", 3000)
            return
        if default_item.get("kind") != "custom":
            self._set_status("Built-in defaults cannot be overwritten. Save as a new default instead.", 4000)
            return
        try:
            response = self.service.upsert_default(self._collect_default_payload(default_item.get("id")))
            saved_default = response["default"]
            self.bootstrap_data = self.service.bootstrap()
            self.defaults = self.bootstrap_data["defaults"]
            self._populate_defaults()
            index = self.default_select.findData(saved_default["id"])
            if index >= 0:
                self.default_select.setCurrentIndex(index)
            self._set_status(f"Updated default {saved_default['name']}", 4000)
        except Exception as exc:
            self._show_error("Update default failed", str(exc), traceback.format_exc())

    def _load_selected_default_into_form(self) -> None:
        default_item = self._selected_default_from_ui()
        if not default_item:
            self._set_status("Choose a default first", 3000)
            return
        self._start_prefill(default_item)

    def _start_prefill(self, default_item: dict[str, Any]) -> None:
        self._form_refresh_timer.stop()
        self._prefill_timer.stop()
        self.current_plan = None
        self.current_host = None
        self.current_project = None
        self._prefill_steps = self._build_prefill_steps(default_item)
        self.default_activity_output.setHtml("")
        self._live_updates_suspended = True
        self._set_status(f"Loading default {default_item.get('name', 'default')}...", 0)
        self._run_next_prefill_step()

    def _build_prefill_steps(self, default_item: dict[str, Any]) -> list[tuple[str, Any]]:
        host_defaults = dict(default_item.get("host_defaults", {}))
        project_defaults = dict(default_item.get("project_defaults", {}))
        steps: list[tuple[str, Any]] = []

        def add(label: str, fn: Any) -> None:
            steps.append((label, fn))

        add("Host label", lambda: self.host_name.setText(host_defaults.get("name", "")))
        add("Execution mode", lambda: self.host_mode.setCurrentText(host_defaults.get("mode", "remote-linux")))
        add("This device role", lambda: self.host_device_role.setCurrentIndex(max(0, self.host_device_role.findData(host_defaults.get("device_role", "computer-a-main")))))
        add("Bootstrap auth", lambda: self.host_bootstrap_auth.setCurrentIndex(max(0, self.host_bootstrap_auth.findData(host_defaults.get("bootstrap_auth", "password-bootstrap")))))
        add("SSH user", lambda: self.host_ssh_user.setText(host_defaults.get("ssh_user", "")))
        add("SSH host / IP", lambda: self.host_ssh_host.setText(host_defaults.get("ssh_host", "")))
        add("SSH port", lambda: self.host_ssh_port.setValue(int(host_defaults.get("ssh_port", 22) or 22)))
        add("SSH key path", lambda: self.host_ssh_key.setCurrentText(host_defaults.get("ssh_key_path", "")))
        add("WSL distro", lambda: self.host_wsl_distribution.setText(host_defaults.get("wsl_distribution", "Ubuntu")))
        add("Project template", lambda: self.template_select.setCurrentIndex(max(0, self.template_select.findData(project_defaults.get("template_id")))))
        add("Project name", lambda: self.project_name.setText(project_defaults.get("name", "")))
        add("Repository URL", lambda: self.project_repo_url.setText(project_defaults.get("repo_url", "")))
        add("Branch", lambda: self.project_branch.setText(project_defaults.get("branch", "main")))
        add("Deploy path", lambda: self.project_deploy_path.setText(project_defaults.get("deploy_path", "")))
        add("Primary domain", lambda: self.project_primary_domain.setText(project_defaults.get("primary_domain", "")))
        add("TLS email", lambda: self.project_letsencrypt_email.setText(project_defaults.get("letsencrypt_email", "admin@example.com")))
        add("Additional domains", lambda: self.project_domains.setPlainText("\n".join(project_defaults.get("domains", []))))
        add("Environment variables", lambda: self._fill_env_table(project_defaults.get("env", [])))
        return steps

    def _run_next_prefill_step(self) -> None:
        if not self._prefill_steps:
            self._live_updates_suspended = False
            self.current_host = self._collect_host()
            self.current_project = self._collect_project()
            self._host_mode_changed()
            self._refresh_dashboard()
            self._render_plan()
            name = self.selected_default.get("name", "default") if self.selected_default else "default"
            self._set_status(f"Loaded default {name}", 4000)
            return

        label, callback = self._prefill_steps.pop(0)
        callback()
        current_log = self.default_activity_output.toHtml().strip()
        line = f"<div>Filled <b>{_escape(label)}</b>.</div>"
        self.default_activity_output.setHtml((current_log + line) if current_log else line)
        self._prefill_timer.start(45)

    def _adopt_local_machine_for_server(self) -> None:
        machine = self.local_machine or {}
        preferred_host = next(iter(machine.get("ip_candidates", [])), machine.get("hostname", ""))
        platform_text = str(machine.get("platform") or "").lower()
        remote_mode = "windows-remote" if "windows" in platform_text else "remote-linux"
        self._clear_host_selection()
        self._live_updates_suspended = True
        try:
            self.host_mode.setCurrentText(remote_mode)
            self.host_device_role.setCurrentIndex(max(0, self.host_device_role.findData("computer-b-server")))
            self.host_bootstrap_auth.setCurrentIndex(max(0, self.host_bootstrap_auth.findData("password-bootstrap")))
            self.host_name.setText(machine.get("hostname", self.host_name.text()))
            self.host_ssh_user.setText(machine.get("username", self.host_ssh_user.text()))
            self.host_ssh_host.setText(preferred_host)
            if not self.host_ssh_port.value():
                self.host_ssh_port.setValue(22)
        finally:
            self._live_updates_suspended = False
        self._host_mode_changed()
        self._refresh_dashboard()
        self._set_status("Filled host fields from this machine for Computer B", 5000)

    def _clear_host_selection(self) -> None:
        self.current_host = None
        self.current_plan = None
        if hasattr(self, "saved_host_select"):
            self.saved_host_select.blockSignals(True)
            self.saved_host_select.setCurrentIndex(0)
            self.saved_host_select.blockSignals(False)

    def _configure_local_host(self, mode: str) -> None:
        machine = self.local_machine or {}
        self._clear_host_selection()
        self._live_updates_suspended = True
        try:
            self.host_mode.setCurrentText(mode)
            self.host_device_role.setCurrentIndex(max(0, self.host_device_role.findData("computer-b-server")))
            self.host_name.setText(machine.get("hostname", self.host_name.text()))
            self.host_ssh_user.setText("")
            self.host_ssh_host.setText("")
            self.host_ssh_key.setCurrentText("")
            if mode == "windows-local":
                self.host_wsl_distribution.setText(self.host_wsl_distribution.text().strip() or "Ubuntu")
        finally:
            self._live_updates_suspended = False
        self._host_mode_changed()
        self._refresh_dashboard()
        mode_text = "Windows + WSL" if mode == "windows-local" else "local Linux"
        self._set_status(f"Prepared this machine as a {mode_text} host draft", 5000)
        self._open_host_admin()

    def _configure_windows_local_host(self) -> None:
        self._configure_local_host("windows-local")

    def _configure_linux_local_host(self) -> None:
        self._configure_local_host("linux-local")

    def _project_source_setup_commands(self) -> tuple[str, str] | None:
        if getattr(sys, "frozen", False):
            return None
        project_root = _resource_root()
        requirements = project_root / "requirements.txt"
        if not requirements.exists():
            return None
        venv_dir = project_root / ".venv"
        if os.name == "nt":
            python_bin = venv_dir / "Scripts" / "python.exe"
        else:
            python_bin = venv_dir / "bin" / "python"
        create_command = f'"{sys.executable}" -m venv "{venv_dir}"'
        install_command = f'"{python_bin}" -m pip install -r "{requirements}"'
        return create_command, install_command

    def _project_source_setup_needs_install(self) -> dict[str, Any] | None:
        if getattr(sys, "frozen", False):
            return None
        project_root = _resource_root()
        requirements = project_root / "requirements.txt"
        if not requirements.exists():
            return None
        venv_dir = project_root / ".venv"
        if os.name == "nt":
            python_bin = venv_dir / "Scripts" / "python.exe"
        else:
            python_bin = venv_dir / "bin" / "python"
        stamp_path = venv_dir / ".vpsdash_requirements.sha256"
        requirements_hash = hashlib.sha256(requirements.read_bytes()).hexdigest()
        return {
            "venv_dir": venv_dir,
            "python_bin": python_bin,
            "stamp_path": stamp_path,
            "requirements_hash": requirements_hash,
            "needs_venv": not python_bin.exists(),
            "needs_requirements": (not stamp_path.exists()) or stamp_path.read_text(encoding="utf-8").strip() != requirements_hash,
        }

    def _perform_local_initial_setup(self, host_payload: dict[str, Any]) -> dict[str, Any]:
        return self._perform_local_initial_setup_with_progress(host_payload, None)

    def _windows_local_wsl_state(self, host_payload: dict[str, Any]) -> dict[str, Any]:
        distro = str(host_payload.get("wsl_distribution") or "Ubuntu").strip() or "Ubuntu"
        list_result = _run_windows_native_command(["wsl.exe", "-l", "-v"], timeout=60)
        stdout = str(list_result.get("stdout") or "")
        stderr = str(list_result.get("stderr") or "")
        combined = f"{stdout}\n{stderr}".strip()
        distro_lines = [
            line.replace("\x00", "").strip(" *\r\n\t")
            for line in stdout.splitlines()
            if line.strip()
        ]
        normalized = [line.lower() for line in distro_lines]
        distro_exists = any(line == distro.lower() or line.startswith(f"{distro.lower()} ") for line in normalized)
        ready_result = None
        distro_ready = False
        if distro_exists:
            ready_result = _run_windows_native_command(
                ["wsl.exe", "-d", distro, "--", "bash", "-lc", "echo __VPSDASH_WSL_READY__"],
                timeout=60,
            )
            distro_ready = bool(ready_result.get("ok")) and "__VPSDASH_WSL_READY__" in str(ready_result.get("stdout") or "")
        return {
            "distro": distro,
            "list_result": list_result,
            "list_output": combined,
            "distro_exists": distro_exists,
            "distro_ready": distro_ready,
            "ready_result": ready_result,
        }

    def _install_windows_local_wsl(self, host_payload: dict[str, Any]) -> dict[str, Any]:
        distro = str(host_payload.get("wsl_distribution") or "Ubuntu").strip() or "Ubuntu"
        list_result = _run_windows_native_command(["wsl.exe", "-l", "-q"], timeout=60)
        existing = [line.strip().replace("\x00", "") for line in str(list_result.get("stdout") or "").splitlines() if line.strip()]
        if any(item.lower() == distro.lower() for item in existing):
            return {"ok": True, "reboot_required": False, "stdout": "WSL distro already present.", "stderr": ""}
        result = _run_windows_native_command(["wsl.exe", "--install", "-d", distro], timeout=2400)
        text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".lower()
        reboot_required = any(token in text for token in ("restart", "reboot", "needs to be restarted", "changes will not take effect"))
        install_started = any(
            token in text
            for token in (
                "installing",
                "has been installed",
                "the operation completed successfully",
                "changes will not be effective until the system is rebooted",
                "wsl is already installed",
            )
        )
        return {**result, "ok": bool(result.get("ok")) or install_started or reboot_required, "reboot_required": reboot_required}

    def _summarize_status_message(self, message: str, limit: int = 120) -> str:
        compact = " ".join(str(message or "").split())
        if len(compact) > limit:
            return compact[: limit - 3].rstrip() + "..."
        return compact

    def _run_initial_setup_step_with_retries(
        self,
        title: str,
        operation: Any,
        *,
        emit: Any,
        attempts: int = 3,
        delay_seconds: float = 2.0,
        success_check: Any | None = None,
        error_detail: Any | None = None,
    ) -> Any:
        last_error = "Unknown error"
        for attempt in range(1, attempts + 1):
            try:
                result = operation()
                ok = bool(success_check(result) if success_check else True)
                if ok:
                    return result
                if callable(error_detail):
                    last_error = str(error_detail(result) or last_error)
                elif isinstance(result, dict):
                    last_error = str(result.get("stderr") or result.get("stdout") or last_error)
                else:
                    last_error = str(result)
            except Exception as exc:
                last_error = str(exc)
            if attempt < attempts:
                emit(0, f"{title} hit an error. Retrying ({attempt + 1}/{attempts})...")
                time.sleep(delay_seconds)
        raise RuntimeError(f"{title} failed after {attempts} attempts. {last_error}".strip())

    def _run_prepare_host_until_complete(self, host_id: int, emit: Any) -> dict[str, Any]:
        queued = self.service.queue_prepare_platform_host(host_id, actor="desktop")
        task_id = int(queued.get("id", 0))
        if task_id <= 0:
            raise RuntimeError("Prepare Host did not return a valid task id.")
        attempts = 3
        current_task_id = task_id
        last_result: dict[str, Any] | None = None
        for attempt in range(1, attempts + 1):
            emit(84, f"Preparing the local hypervisor stack ({attempt}/{attempts})...")
            last_result = self.service.run_platform_task(current_task_id, actor="desktop", dry_run=False)
            status = str((last_result or {}).get("status") or "").strip().lower()
            if status in {"succeeded", "complete", "completed"}:
                payload = dict(last_result or {})
                payload["task_id"] = current_task_id
                payload["completed"] = True
                return payload
            if attempt < attempts:
                emit(88, f"Prepare Host failed once. Retrying ({attempt + 1}/{attempts})...")
                retry_task = self.service.retry_platform_task(current_task_id, actor="desktop")
                current_task_id = int(retry_task.get("id", 0))
                if current_task_id <= 0:
                    raise RuntimeError("Prepare Host retry did not return a valid task id.")
                time.sleep(2.0)
        detail = ""
        if isinstance(last_result, dict):
            detail = str(
                ((last_result.get("result_payload") or {}).get("results") or last_result.get("log_output") or last_result.get("status") or "")
            ).strip()
        raise RuntimeError(f"Prepare Host failed after {attempts} attempts. {detail}".strip())

    def _perform_local_initial_setup_with_progress(self, host_payload: dict[str, Any], progress_cb: Any | None) -> dict[str, Any]:
        def emit(percent: int, message: str) -> None:
            if progress_cb is not None:
                progress_cb(percent, message)

        result: dict[str, Any] = {"source_env": None, "host": None, "inventory": None, "prepare": None, "setup": {}}
        host_mode = str(host_payload.get("host_mode") or host_payload.get("mode") or "").strip().lower()
        emit(5, "Checking local prerequisites...")
        source_state = self._project_source_setup_needs_install()
        source_commands = self._project_source_setup_commands()
        if source_commands and source_state and (source_state["needs_venv"] or source_state["needs_requirements"]):
            create_command, install_command = source_commands
            if source_state["needs_venv"]:
                emit(10, "Creating local Python environment...")
                create_result = self._run_initial_setup_step_with_retries(
                    "Create local Python environment",
                    lambda: run_local_command(create_command, timeout=900),
                    emit=emit,
                    success_check=lambda value: bool((value or {}).get("ok")),
                    error_detail=lambda value: (value or {}).get("stderr") or (value or {}).get("stdout") or "",
                )
            if source_state["needs_requirements"]:
                emit(18, "Installing Python requirements...")
                install_result = self._run_initial_setup_step_with_retries(
                    "Install Python requirements",
                    lambda: run_local_command(install_command, timeout=1800),
                    emit=emit,
                    success_check=lambda value: bool((value or {}).get("ok")),
                    error_detail=lambda value: (value or {}).get("stderr") or (value or {}).get("stdout") or "",
                )
                source_state["stamp_path"].parent.mkdir(parents=True, exist_ok=True)
                source_state["stamp_path"].write_text(source_state["requirements_hash"], encoding="utf-8")
            result["source_env"] = {
                "venv_created": bool(source_state["needs_venv"]),
                "requirements_installed": bool(source_state["needs_requirements"]),
            }
        elif source_state:
            result["source_env"] = {
                "venv_created": False,
                "requirements_installed": False,
                "skipped": True,
            }

        emit(28, "Saving local host profile...")
        host = self.service.upsert_platform_host(
            self._control_plane_host_payload(host_payload, status="queued" if host_mode in {"windows-local", "linux-local"} else None),
            actor="desktop",
        )
        host_id = int(host.get("id", 0))
        if host_id <= 0:
            raise RuntimeError("Initial setup could not save the local host profile.")

        if host_mode == "windows-local":
            emit(38, "Checking Windows and WSL prerequisites...")
            wsl_state = self._windows_local_wsl_state(host_payload)
            result["setup"]["wsl"] = {
                "distro": wsl_state["distro"],
                "distro_exists": wsl_state["distro_exists"],
                "distro_ready": wsl_state["distro_ready"],
                "list_output": wsl_state["list_output"],
                "list_ok": bool(((wsl_state or {}).get("list_result") or {}).get("ok")),
            }
            if not wsl_state["distro_exists"] or not wsl_state["distro_ready"]:
                emit(48, f"Installing or initializing WSL distro {wsl_state['distro']}...")
                install_result = self._run_initial_setup_step_with_retries(
                    f"Install or initialize WSL distro {wsl_state['distro']}",
                    lambda: self._install_windows_local_wsl(host_payload),
                    emit=emit,
                    attempts=2,
                    success_check=lambda value: bool((value or {}).get("ok")),
                    error_detail=lambda value: (value or {}).get("stderr") or (value or {}).get("stdout") or "",
                )
                result["setup"]["wsl_install"] = {
                    "ok": bool(install_result.get("ok")),
                    "reboot_required": bool(install_result.get("reboot_required")),
                    "stdout": str(install_result.get("stdout") or ""),
                    "stderr": str(install_result.get("stderr") or ""),
                }
                emit(58, "Re-checking WSL runtime after install...")
                follow_up = self._windows_local_wsl_state(host_payload)
                result["setup"]["wsl"].update(
                    {
                        "distro_exists": follow_up["distro_exists"],
                        "distro_ready": follow_up["distro_ready"],
                        "list_output": follow_up["list_output"],
                        "list_ok": bool(((follow_up or {}).get("list_result") or {}).get("ok")),
                    }
                )
                if not follow_up["distro_ready"]:
                    host = self.service.upsert_platform_host(
                        self._control_plane_host_payload(host, status="queued"),
                        actor="desktop",
                    )
                    result["host"] = host
                    result["prepare"] = {
                        "skipped": True,
                        "pending": True,
                        "reason": "wsl-not-ready",
                        "reboot_required": bool(install_result.get("reboot_required")),
                    }
                    emit(
                        100,
                        "WSL setup started. Reopen VPSdash after WSL finishes installing"
                        + (" and reboot Windows if prompted." if install_result.get("reboot_required") else "."),
                    )
                    return result

        emit(62, "Capturing system inventory...")
        inventory = self._run_initial_setup_step_with_retries(
            "Capture system inventory",
            lambda: self.service.capture_platform_host_inventory(host_id, actor="desktop"),
            emit=emit,
        )
        resources = dict((inventory.get("inventory") or {}).get("resources") or {})
        if bool(resources.get("virtualization_ready")):
            emit(100, "Local host is ready for Doplets.")
            host = self.service.upsert_platform_host(
                self._control_plane_host_payload(host, status="ready"),
                actor="desktop",
            )
            prepare = {"skipped": True, "status": "ready"}
        else:
            emit(78, "Preparing the local hypervisor stack...")
            host = self.service.upsert_platform_host(
                self._control_plane_host_payload(host, status="provisioning"),
                actor="desktop",
            )
            prepare = self._run_prepare_host_until_complete(host_id, emit)
            emit(100, "Initial setup finished preparing the local host.")
        result["host"] = host
        result["inventory"] = inventory
        result["prepare"] = prepare
        return result

    def _run_initial_setup(self) -> None:
        machine = self.local_machine or {}
        platform_text = str(machine.get("platform") or sys.platform).lower()
        if "win" in platform_text:
            self._configure_windows_local_host()
        else:
            self._configure_linux_local_host()
        host_payload = self._collect_host()
        self._set_initial_setup_progress(3, "Starting initial setup...")
        self._run_async_task(
            start_message="Running initial setup for this machine...",
            work=lambda progress: self._perform_local_initial_setup_with_progress(host_payload, progress),
            on_success=self._complete_initial_setup,
            error_title="Initial setup failed",
            done_message="Initial setup started",
            on_progress=self._set_initial_setup_progress,
        )

    def _complete_initial_setup(self, result: dict[str, Any]) -> None:
        host = dict((result or {}).get("host") or {})
        if host:
            self.current_host = host
        prepare = dict((result or {}).get("prepare") or {})
        task_id = int(prepare.get("task_id", 0) or 0)
        if task_id:
            self._watch_platform_task(task_id)
        self._load_bootstrap()
        self._open_host_admin()
        if prepare.get("pending"):
            self._set_initial_setup_progress(
                100,
                "Initial setup started WSL provisioning. Reboot Windows if prompted, then reopen VPSdash and run Initial Setup again.",
            )
            self._set_status("Initial setup started WSL installation. Reboot Windows if required, then rerun Initial Setup.", 9000)
        elif str(prepare.get("status") or "").strip().lower() in {"succeeded", "complete", "completed"} or prepare.get("completed"):
            self._set_initial_setup_progress(100, "Initial setup fully completed host preparation.")
            self._set_status("Initial setup completed. This machine is ready for local Doplets.", 7000)
        elif prepare.get("skipped"):
            self._set_initial_setup_progress(100, "Initial setup confirmed the local runtime is ready.")
            self._set_status("Initial setup checked this machine and skipped host preparation because the local runtime already looks ready.", 7000)
        else:
            self._set_initial_setup_progress(100, "Initial setup queued host preparation.")
            self._set_status("Initial setup started. VPSdash is preparing this machine for local Doplets.", 6000)

    def _save_host_and_open_doplet_admin(self) -> None:
        self._save_host()
        if self.current_host:
            self._open_doplet_builder()

    def _current_host_preview(self) -> dict[str, Any] | None:
        if self.current_host or any(
            [
                self.host_name.text().strip(),
                self.host_ssh_user.text().strip(),
                self.host_ssh_host.text().strip(),
                self.host_ssh_key.currentText().strip(),
            ]
        ):
            return self._collect_host()
        return None

    def _current_project_preview(self) -> dict[str, Any] | None:
        if self.current_project or any(
            [
                self.project_name.text().strip(),
                self.project_repo_url.text().strip(),
                self.project_deploy_path.text().strip(),
                self.project_primary_domain.text().strip(),
            ]
        ):
            return self._collect_project()
        return None

    def _refresh_dashboard(self) -> None:
        host = self._current_host_preview()
        project = self._current_project_preview()
        remote_host = self._collect_host()
        template_source = project or self._selected_template_preview()
        control_plane = self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}

        step_count = self._plan_step_count()
        warning_count = len(self.current_plan.get("warnings", [])) if self.current_plan else 0

        self.metric_templates["value"].setText(str(len(control_plane.get("hosts", []))))
        self.metric_hosts["value"].setText(str(len(control_plane.get("doplets", []))))
        self.metric_projects["value"].setText(str(len(control_plane.get("networks", []))))
        self.metric_steps["value"].setText(str(len(control_plane.get("tasks", []))))
        self.metric_steps["body"].setText("Running or recent control-plane work." if control_plane.get("tasks") else "No queued platform tasks yet.")

        mode_text = (host or {}).get("mode", "host pending").replace("-", " ").upper()
        activity_count = len(control_plane.get("tasks", []))
        set_chip(self.overview_mode_chip, mode_text, "accent" if host else "neutral")
        set_chip(self.overview_project_chip, "HARMINOPLET ADMIN", "accent")
        set_chip(self.overview_plan_chip, f"{activity_count} TASK{'S' if activity_count != 1 else ''}", "success" if activity_count else "neutral")

        set_chip(self.setup_host_chip, (host.get("name", "HOST UNNAMED")).upper()[:30] if host else "HOST UNNAMED", "accent" if host else "neutral")
        set_chip(self.setup_project_chip, (project.get("name", "PROJECT UNNAMED")).upper()[:30] if project else "PROJECT UNNAMED", "accent" if project else "neutral")
        set_chip(
            self.setup_template_chip,
            f"TEMPLATE {project.get('template_id', 'READY').upper()}"[:32] if project else "TEMPLATE READY",
            "accent" if project else "neutral",
        )

        set_chip(self.ops_host_chip, (host.get("name", "NO HOST")).upper()[:30] if host else "NO HOST", "accent" if host else "warn")
        set_chip(self.ops_project_chip, (project.get("name", "NO PROJECT")).upper()[:30] if project else "NO PROJECT", "accent" if project else "warn")
        set_chip(
            self.ops_plan_chip,
            "PLAN READY" if self.current_plan and not warning_count else ("PLAN HAS WARNINGS" if self.current_plan else "PLAN NOT READY"),
            "success" if self.current_plan and not warning_count else "warn",
        )

        self.setup_context_body.setText(self._setup_context_html(host, project))
        self.operations_context.setText(self._operations_context_html(host, project))
        self.overview_workspace_body.setText(self._workspace_html(host, project))
        self.overview_next_steps.setText(self._next_steps_html(host, project))
        self.overview_template_body.setText(self._template_html(template_source))
        self.overview_coverage_body.setText(self._coverage_html(template_source))
        if hasattr(self, "local_host_quickstart_body"):
            self.local_host_quickstart_body.setText(self._local_host_quickstart_html(host))
        if hasattr(self, "doplet_builder_body"):
            self.doplet_builder_body.setText(self._doplet_builder_html(host))
        self.remote_flow_output.setHtml(self._remote_flow_html(remote_host))
        self.remote_packet_output.setPlainText(self._remote_packet_text(remote_host))
        is_remote = remote_host.get("mode") in {"remote-linux", "windows-remote"}
        self.copy_ssh_button.setEnabled(is_remote)
        self.copy_packet_button.setEnabled(is_remote)

        domain_count = 1 + len(project.get("domains", [])) if project and project.get("primary_domain") else len((project or {}).get("domains", []))
        set_chip(self.workspace_domain_chip, f"{domain_count} DOMAIN{'S' if domain_count != 1 else ''}", "success" if domain_count else "warn")
        backup_count = len((project or {}).get("backup_paths", []))
        set_chip(self.workspace_backup_chip, f"{backup_count} BACKUP PATH{'S' if backup_count != 1 else ''}", "success" if backup_count else "neutral")
        health_count = len((project or {}).get("health_checks", []))
        set_chip(self.workspace_health_chip, f"{health_count} HEALTH CHECK{'S' if health_count != 1 else ''}", "success" if health_count else "neutral")

        branch = (template_source or {}).get("branch") or "unspecified"
        set_chip(self.template_branch_chip, f"BRANCH {branch.upper()}"[:28], "accent" if template_source else "neutral")
        build_count = len((template_source or {}).get("build_steps", []))
        set_chip(self.template_build_chip, f"{build_count} BUILD STEP{'S' if build_count != 1 else ''}", "accent" if build_count else "neutral")
        storage_count = len((template_source or {}).get("persistent_paths", []))
        set_chip(self.template_storage_chip, f"{storage_count} DATA PATH{'S' if storage_count != 1 else ''}", "accent" if storage_count else "neutral")

        self.project_template_hint.setText(self._template_hint_text(template_source))

        if self.current_plan:
            summary = self.current_plan["summary"]
            self.plan_summary.setText(
                f"{_escape(summary['project_name'])} on {_escape(summary['host_name'])}. "
                f"Host mode: {_escape(summary['host_mode'])}. "
                f"Role: {_escape(self._device_role_label(summary.get('device_role')))}. "
                f"Auth: {_escape(self._bootstrap_auth_label(summary.get('bootstrap_auth')))}. "
                f"Deploy path: {_escape(summary['deploy_path'])}. "
                f"Shell: {_escape(summary['shell'])}."
            )
            set_chip(self.plan_mode_chip, summary["host_mode"].replace("-", " ").upper(), "accent")
            set_chip(
                self.plan_warning_chip,
                f"{warning_count} WARNING{'S' if warning_count != 1 else ''}" if warning_count else "NO WARNINGS",
                "warn" if warning_count else "success",
            )
            set_chip(self.plan_step_chip, f"{step_count} STEP{'S' if step_count != 1 else ''}", "accent")
        else:
            self.plan_summary.setText(
                "Generate a plan from Setup to stage the host, repo, env, proxy, deploy, backup, and verification steps."
            )
            set_chip(self.plan_mode_chip, "MODE UNKNOWN", "neutral")
            set_chip(self.plan_warning_chip, "NO PLAN", "warn")
            set_chip(self.plan_step_chip, "0 STEPS", "neutral")

        if hasattr(self, "instances_tree"):
            self._instance_selection_changed()
        self._refresh_topbar()

    def _refresh_topbar(self) -> None:
        if not hasattr(self, "desktop_topbar_title"):
            return

        page_index = self.pages.currentIndex() if hasattr(self, "pages") else self.PAGE_OVERVIEW
        titles = {
            self.PAGE_OVERVIEW: (
                "Overview",
                "See host readiness, current Doplet posture, recent tasks, and the shortest path into the builder."
            ),
            self.PAGE_ACTIVITY: (
                "Activity",
                "Review diagnostics, execution logs, backups, snapshots, native task state, and instance operations."
            ),
            self.PAGE_RESOURCES: (
                "Resources",
                "Watch current-machine CPU, RAM, disk, and local VPS usage first, then inspect remote host capacity separately."
            ),
            self.PAGE_HARMINOPLETS: (
                "Doplet Workspace",
                "Use the native workspace for host setup, Windows + WSL hosting, Doplet drafts, remote access details, and deployment inputs."
            ),
        }
        title, body = titles.get(page_index, ("Control Plane", "Operate the platform from one native shell."))
        self.desktop_topbar_title.setText(title)
        self.desktop_topbar_body.setText(body)

        host = self._current_host_preview()
        set_chip(
            self.desktop_topbar_host_chip,
            (host.get("name", "HOST PENDING")).upper()[:30] if host else "HOST PENDING",
            "accent" if host else "neutral",
        )
        set_chip(
            self.desktop_topbar_page_chip,
            self.PAGE_LABELS[page_index].upper(),
            "accent",
        )

        if page_index == self.PAGE_HARMINOPLETS:
            status_text = "NATIVE WORKSPACE"
            tone = "accent"
        elif page_index == self.PAGE_RESOURCES:
            status_text = "LOCAL RESOURCE VIEW"
            tone = "accent"
        elif self.current_plan:
            status_text = "PLAN IN MEMORY"
            tone = "accent"
        else:
            status_text = "LOCAL ADMIN READY"
            tone = "success"
        set_chip(self.desktop_topbar_status_chip, status_text, tone)

    def _setup_context_html(self, host: dict[str, Any] | None, project: dict[str, Any] | None) -> str:
        host_line = _escape(host["name"]) if host else "No host selected yet."
        project_line = _escape(project["name"]) if project else "No project selected yet."
        repo_line = _escape(project.get("repo_url")) if project and project.get("repo_url") else "Repository not configured."
        return (
            f"<b>Host</b><br>{host_line}<br><br>"
            f"<b>Project</b><br>{project_line}<br><br>"
            f"<b>Repository</b><br>{repo_line}"
        )

    def _local_host_quickstart_html(self, host: dict[str, Any] | None) -> str:
        if not host:
            return (
                "<b>Windows self-host path</b><br>"
                "Click <b>Use This Windows PC</b>, confirm the WSL distro, save the host, then open Host Admin to capture inventory and prepare the machine.<br><br>"
                "<b>Linux self-host path</b><br>"
                "Click <b>Use This Linux PC</b>, save the host, then prepare it from Host Admin."
            )
        if host.get("mode") == "windows-local":
            return (
                f"<b>{_escape(host.get('name') or 'This machine')}</b> is configured as a Windows + WSL host.<br>"
                f"WSL distro: {_escape(host.get('wsl_distribution') or 'Ubuntu')}<br><br>"
                "Next: save the host if needed, open Host Admin, run inventory capture, then queue host preparation."
            )
        if host.get("mode") == "linux-local":
            return (
                f"<b>{_escape(host.get('name') or 'This machine')}</b> is configured as a local Linux host.<br><br>"
                "Next: save the host if needed, open Host Admin, run inventory capture, then queue host preparation."
            )
        return (
            f"Current host mode is <b>{_escape(self._mode_label(host.get('mode')))}</b>.<br>"
            "Use the buttons here when this machine itself should become the Doplet host."
        )

    def _doplet_builder_html(self, host: dict[str, Any] | None) -> str:
        host_hint = (
            f"Current host draft: <b>{_escape(host.get('name') or 'Unnamed host')}</b> ({_escape(self._mode_label(host.get('mode')))})<br><br>"
            if host
            else ""
        )
        return (
            f"{host_hint}"
            "<b>Configure here in Doplet Admin</b><br>"
            "Host selection, image, flavor, vCPU, RAM, disk, network, storage backend, GPU assignment, backup policy, snapshot, clone, and resize actions."
        )

    def _operations_context_html(self, host: dict[str, Any] | None, project: dict[str, Any] | None) -> str:
        if not host and not project:
            return "Select a host and project in Setup, then use Doplet Builder for droplet size and lifecycle settings. Operations is for diagnostics, snapshots, backups, tasks, and logs."
        host_text = _escape(host["name"]) if host else "No host selected"
        project_text = _escape(project["name"]) if project else "No project selected"
        instance_text = ""
        if self.current_instance:
            instance_text = f" Managed instance: <b>{_escape(self.current_instance.get('name', 'Unnamed instance'))}</b>."
        return (
            f"Current target: <b>{host_text}</b> running <b>{project_text}</b>.{instance_text} "
            "Use diagnostics before live deploys and snapshots when the machine behavior changes. "
            "Use <b>Open Doplet Builder</b> for CPU, RAM, disk, image, network, and GPU configuration."
        )

    def _workspace_html(self, host: dict[str, Any] | None, project: dict[str, Any] | None) -> str:
        host_block = "No host selected yet. Open Host Admin to create one."
        if host:
            if host.get("mode") in {"remote-linux", "windows-remote"}:
                reachability = f"{_escape(host.get('ssh_user') or 'user')}@{_escape(host.get('ssh_host') or 'host')}:{_escape(host.get('ssh_port') or 22)}"
                if host.get("mode") == "windows-remote":
                    reachability += f" via WSL distro {_escape(host.get('wsl_distribution') or 'Ubuntu')}"
            elif host.get("mode") == "windows-local":
                reachability = f"WSL distro {_escape(host.get('wsl_distribution') or 'Ubuntu')}"
            else:
                reachability = "Commands run on this Linux machine."
            host_block = (
                f"<b>{_escape(host.get('name') or 'Unnamed host')}</b><br>"
                f"Mode: {_escape(host.get('mode', 'remote-linux'))}<br>"
                f"Target: {reachability}"
            )
        admin_block = (
            "<b>Next place to work</b><br>"
            "Host Admin for machine setup, then Doplet Builder for CPU, RAM, disk, network, and backup configuration."
        )
        return f"{host_block}<br><br>{admin_block}"

    def _next_steps_html(self, host: dict[str, Any] | None, project: dict[str, Any] | None) -> str:
        steps: list[str] = []
        if not host:
            steps.append("Open Host Admin and save the machine you want to use as the Doplet host.")
        elif host.get("mode") in {"remote-linux", "windows-remote"} and not host.get("ssh_host"):
            steps.append("Add the SSH hostname or IP so VPSdash can reach that host.")
        elif host.get("mode") == "windows-local":
            steps.append("Confirm the WSL distro, capture inventory, then queue host preparation from Host Admin.")
        elif host.get("mode") == "windows-remote":
            steps.append("Confirm the remote Windows SSH target and the WSL distro that should host Doplets on that machine.")
        else:
            steps.append("Open Doplet Builder and choose image, vCPU, RAM, disk, network, storage, and backup settings.")

        if control_plane_tasks := (self.bootstrap_data.get("control_plane", {}) if hasattr(self, "bootstrap_data") else {}).get("tasks", []):
            steps.append(f"Open Activity to review {len(control_plane_tasks)} queued or recent platform task(s).")
        else:
            steps.append("After the host is ready, create a Doplet from the admin workspace.")

        steps.append("Use Activity for diagnostics, task status, snapshots, and backup results.")

        steps = steps[:3]
        return "<br>".join(f"<b>{index}.</b> {_escape(step)}" for index, step in enumerate(steps, start=1))

    def _template_html(self, project: dict[str, Any] | None) -> str:
        return (
            "<b>Host Admin</b><br>"
            "Create or edit hosts, choose Linux or Windows + WSL mode, capture inventory, and prepare the machine.<br><br>"
            "<b>Doplet Builder</b><br>"
            "Set image, flavor, vCPU, RAM, disk, network, storage backend, GPU assignment, backup policy, and lifecycle actions."
        )

    def _coverage_html(self, project: dict[str, Any] | None) -> str:
        mode_hint = (
            "Windows mode routes Linux-side setup through WSL."
            if self.host_mode.currentText() in {"windows-local", "windows-remote"}
            else "Remote mode expects SSH reachability and local mode runs directly on the machine."
        )
        return (
            "<b>Activity</b><br>Diagnostics, host telemetry, queued tasks, logs, backups, and snapshot history.<br><br>"
            "<b>Lifecycle</b><br>Create, start, stop, reboot, resize, snapshot, clone, restore, and backup Doplets.<br><br>"
            f"<b>Host note</b><br>{_escape(mode_hint)}"
        )

    def _template_hint_text(self, project: dict[str, Any] | None) -> str:
        if not project:
            return "Select a template to preload service layout, persistent paths, env schema, and health checks."
        description = project.get("description") or "No description provided."
        services = len(project.get("services", []))
        build_steps = len(project.get("build_steps", []))
        persistent = len(project.get("persistent_paths", []))
        return (
            f"{description} This template declares {services} service(s), {build_steps} build step(s), "
            f"and {persistent} persistent path(s)."
        )

    def _selected_template_preview(self) -> dict[str, Any] | None:
        template_id = self.template_select.currentData()
        if not template_id:
            return None
        return next((dict(item) for item in self.templates if item.get("id") == template_id), None)

    def _mode_label(self, mode: Any) -> str:
        mapping = {
            "remote-linux": "Remote Linux over SSH",
            "linux-local": "Local Linux on this machine",
            "windows-local": "Windows host with WSL",
            "windows-remote": "Remote Windows host with SSH + WSL",
        }
        return mapping.get(str(mode), str(mode or "Unspecified"))

    def _device_role_label(self, role: Any) -> str:
        mapping = {
            "computer-a-main": "Computer A - main control machine",
            "computer-b-server": "Computer B - server being prepared",
        }
        return mapping.get(str(role), str(role or "Unspecified"))

    def _bootstrap_auth_label(self, auth: Any) -> str:
        mapping = {
            "password-bootstrap": "Password bootstrap first",
            "ssh-key-ready": "SSH key already ready",
        }
        return mapping.get(str(auth), str(auth or "Unspecified"))

    def _remote_ssh_command_preview(self, host: dict[str, Any]) -> str:
        if host.get("mode") not in {"remote-linux", "windows-remote"}:
            return ""

        user = host.get("ssh_user") or "username"
        hostname = host.get("ssh_host") or "<hostname-or-ip>"
        port = int(host.get("ssh_port") or 22)
        parts = ["ssh"]
        if port != 22:
            parts.extend(["-p", str(port)])
        if host.get("bootstrap_auth") == "ssh-key-ready" and host.get("ssh_key_path"):
            parts.extend(["-i", str(host.get("ssh_key_path"))])
        parts.append(f"{user}@{hostname}")
        return " ".join(parts)

    def _remote_flow_html(self, host: dict[str, Any]) -> str:
        mode = host.get("mode")
        if mode == "linux-local":
            return (
                "<b>Local host flow</b><br>"
                "This profile runs directly on the current Linux machine, so there is no Computer A / Computer B handoff. "
                "Save the host, fill the project profile, then generate a plan."
            )
        if mode == "windows-local":
            return (
                "<b>Windows self-host flow</b><br>"
                "This profile uses WSL as the Linux runtime on the current Windows machine. "
                "Confirm the distro name, keep the machine awake, and use local execution instead of SSH."
            )
        if mode not in {"remote-linux", "windows-remote"}:
            return (
                "<b>Host flow</b><br>"
                "Save the host profile, fill the project profile, and generate a plan to stage the machine."
            )

        role = host.get("device_role", "computer-a-main")
        auth = host.get("bootstrap_auth", "password-bootstrap")
        host_name = _escape(host.get("name") or "Unnamed host")
        user = _escape(host.get("ssh_user") or "username")
        target = _escape(host.get("ssh_host") or "hostname-or-ip")
        auth_text = _escape(self._bootstrap_auth_label(auth))

        if role == "computer-b-server":
            steps = [
                "You are defining <b>Computer B</b>, the machine that will become the server.",
                "If this app is running on Computer B right now, click <b>Use This Machine For Computer B</b> so VPSdash can fill the hostname, local username, and reachable addresses for you.",
                f"Keep <b>{auth_text}</b> selected until Computer A has logged in once using the account password.",
                (
                    f"From Computer A, connect to <b>{user}@{target}</b>, verify the remote Windows machine, let VPSdash verify or install "
                    f"the <b>{_escape(host.get('wsl_distribution') or 'Ubuntu')}</b> WSL distro there, and then install or copy your SSH public key."
                    if mode == "windows-remote"
                    else f"From Computer A, connect to <b>{user}@{target}</b>, verify the server, then install or copy your SSH public key."
                ),
                "After key access works from Computer A, switch Bootstrap auth to <b>SSH key already ready</b>, choose the key path, and continue with diagnostics and deploy automation.",
            ]
        else:
            steps = [
                "You are defining <b>Computer A</b>, the control machine that reaches Computer B over SSH.",
                (
                    f"Enter the SSH target for <b>{host_name}</b> using the remote Windows username, address, port, and WSL distro below."
                    if mode == "windows-remote"
                    else f"Enter the SSH target for <b>{host_name}</b> using the server username, address, and port below."
                ),
                "If keys are not ready yet, leave Bootstrap auth on <b>Password bootstrap first</b> and use the connection packet to perform the first login manually.",
                "Once key-based access works, choose the key path on Computer A and switch Bootstrap auth to <b>SSH key already ready</b> before you run automated remote steps.",
            ]

        return "<br><br>".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))

    def _remote_packet_text(self, host: dict[str, Any]) -> str:
        mode = host.get("mode")
        if mode not in {"remote-linux", "windows-remote"}:
            return (
                "Connection packet is only generated for SSH-based remote host modes.\n\n"
                "Choose remote-linux or windows-remote when Computer A will reach Computer B over SSH."
            )

        machine = self.local_machine or {}
        ip_candidates = machine.get("ip_candidates", [])
        target = host.get("ssh_host") or (ip_candidates[0] if ip_candidates else machine.get("fqdn") or machine.get("hostname") or "")
        user = host.get("ssh_user") or machine.get("username") or "username"
        port = int(host.get("ssh_port") or 22)
        role = self._device_role_label(host.get("device_role"))
        auth = self._bootstrap_auth_label(host.get("bootstrap_auth"))
        command = self._remote_ssh_command_preview({**host, "ssh_host": target, "ssh_user": user, "ssh_port": port})

        lines = [
            "Computer A / Computer B Connection Packet",
            "",
            f"Host label: {host.get('name') or machine.get('hostname') or 'Unnamed host'}",
            f"Execution mode: {self._mode_label(mode)}",
            f"Role: {role}",
            f"Bootstrap auth: {auth}",
            f"SSH user: {user}",
            f"SSH target: {target or '<fill hostname or IP>'}",
            f"SSH port: {port}",
            f"SSH key path: {host.get('ssh_key_path') or '(not set)'}",
        ]
        if mode == "windows-remote":
            lines.append(f"WSL distro: {host.get('wsl_distribution') or 'Ubuntu'}")

        if machine:
            lines.extend(
                [
                    "",
                    "Current machine details:",
                    f"Hostname: {machine.get('hostname') or '(unknown)'}",
                    f"FQDN: {machine.get('fqdn') or '(unknown)'}",
                    f"Detected IPv4 addresses: {', '.join(ip_candidates) if ip_candidates else '(none detected)'}",
                ]
            )

        lines.extend(
            [
                "",
                "First connection from Computer A:",
                command or "ssh username@hostname-or-ip",
            ]
        )

        if host.get("bootstrap_auth") == "password-bootstrap":
            lines.extend(
                [
                    "",
                    "Bootstrap note:",
                    "Use the account password for the first SSH login, then install your SSH key and switch the profile to SSH key already ready.",
                ]
            )

        return "\n".join(lines)

    def _copy_remote_ssh_command(self) -> None:
        command = self._remote_ssh_command_preview(self._collect_host())
        if not command:
            self._set_status("SSH command is only available for SSH-based remote host modes", 3000)
            return
        QApplication.clipboard().setText(command)
        self._set_status("Copied SSH command", 3000)

    def _copy_remote_packet(self) -> None:
        packet = self._remote_packet_text(self._collect_host())
        QApplication.clipboard().setText(packet)
        self._set_status("Copied connection packet", 3000)

    def _plan_step_count(self) -> int:
        if not self.current_plan:
            return 0
        return sum(len(stage.get("steps", [])) for stage in self.current_plan.get("stages", []))

    def _template_changed(self, *_args: Any) -> None:
        if self._live_updates_suspended:
            return
        self.current_plan = None
        self._render_plan()
        self._refresh_dashboard()
        template_id = self.template_select.currentData()
        if template_id:
            template = next((item for item in self.templates if item["id"] == template_id), None)
            if template:
                self._set_status(f"Selected template {template['name']}. Click Apply Template Fields to preload its project values.", 4000)

    def _apply_selected_template(self) -> None:
        template_id = self.template_select.currentData()
        if not template_id:
            self._set_status("Choose a template first", 3000)
            return
        self._apply_template(str(template_id))

    def _apply_template(self, template_id: str) -> None:
        self._form_refresh_timer.stop()
        template = next((item for item in self.templates if item["id"] == template_id), None)
        if not template:
            return
        self.current_project = json.loads(json.dumps(template))
        self.current_project["template_id"] = template_id
        self.current_project["id"] = None
        self.current_plan = None
        self._fill_project_form(self.current_project)
        self._render_plan()
        self._refresh_dashboard()
        self._set_status(f"Loaded template {template['name']}", 3000)

    def _fill_host_form(self, host: dict[str, Any]) -> None:
        self._form_refresh_timer.stop()
        self._prefill_timer.stop()
        self._live_updates_suspended = True
        try:
            self.host_name.setText(host.get("name", ""))
            self.host_mode.setCurrentText(host.get("mode", "remote-linux"))
            self.host_device_role.setCurrentIndex(max(0, self.host_device_role.findData(host.get("device_role", "computer-a-main"))))
            self.host_bootstrap_auth.setCurrentIndex(max(0, self.host_bootstrap_auth.findData(host.get("bootstrap_auth", "password-bootstrap"))))
            self.host_ssh_user.setText(host.get("ssh_user", ""))
            self.host_ssh_host.setText(host.get("ssh_host", ""))
            self.host_ssh_port.setValue(int(host.get("ssh_port", 22)))
            self.host_ssh_key.setCurrentText(host.get("ssh_key_path", ""))
            self.host_wsl_distribution.setText(host.get("wsl_distribution", "Ubuntu"))
        finally:
            self._live_updates_suspended = False
        self._host_mode_changed()
        self._refresh_dashboard()

    def _fill_project_form(self, project: dict[str, Any]) -> None:
        self._form_refresh_timer.stop()
        self._prefill_timer.stop()
        self._live_updates_suspended = True
        try:
            index = self.template_select.findData(project.get("template_id"))
            self.template_select.blockSignals(True)
            if index >= 0:
                self.template_select.setCurrentIndex(index)
            self.template_select.blockSignals(False)
            self.project_name.setText(project.get("name", ""))
            self.project_repo_url.setText(project.get("repo_url", ""))
            self.project_branch.setText(project.get("branch", "main"))
            self.project_deploy_path.setText(project.get("deploy_path", ""))
            self.project_primary_domain.setText(project.get("primary_domain", ""))
            self.project_letsencrypt_email.setText(project.get("letsencrypt_email", "admin@example.com"))
            self.project_domains.setPlainText("\n".join(project.get("domains", [])))
            self._fill_env_table(project.get("env", []))
        finally:
            self._live_updates_suspended = False
        self._refresh_dashboard()

    def _fill_env_table(self, env_items: list[dict[str, Any]]) -> None:
        self._live_updates_suspended = True
        try:
            self.env_table.setRowCount(0)
            for item in env_items:
                self._add_env_row(item, refresh=False)
        finally:
            self._live_updates_suspended = False

    def _checkbox_item(self, checked: bool) -> QTableWidgetItem:
        item = QTableWidgetItem("")
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def _add_env_row(self, item: dict[str, Any] | None = None, *, refresh: bool = True) -> None:
        item = item or {"key": "", "label": "", "value": "", "secret": False, "required": False}
        row = self.env_table.rowCount()
        self.env_table.insertRow(row)
        self.env_table.setItem(row, 0, QTableWidgetItem(item.get("key", "")))
        self.env_table.setItem(row, 1, QTableWidgetItem(item.get("label", "")))
        self.env_table.setItem(row, 2, QTableWidgetItem(item.get("value", "")))
        self.env_table.setItem(row, 3, self._checkbox_item(bool(item.get("secret"))))
        self.env_table.setItem(row, 4, self._checkbox_item(bool(item.get("required"))))
        if refresh and not self._live_updates_suspended:
            self._handle_form_changed()

    def _remove_selected_env_rows(self) -> None:
        rows = sorted({index.row() for index in self.env_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.env_table.removeRow(row)
        if not self._live_updates_suspended:
            self._handle_form_changed()

    def _collect_host(self) -> dict[str, Any]:
        return {
            "id": self.current_host.get("id") if self.current_host else None,
            "name": self.host_name.text().strip() or "New host",
            "mode": self.host_mode.currentText(),
            "device_role": self.host_device_role.currentData(),
            "bootstrap_auth": self.host_bootstrap_auth.currentData(),
            "ssh_user": self.host_ssh_user.text().strip(),
            "ssh_host": self.host_ssh_host.text().strip(),
            "ssh_port": int(self.host_ssh_port.value()),
            "ssh_key_path": self.host_ssh_key.currentText().strip(),
            "wsl_distribution": self.host_wsl_distribution.text().strip() or "Ubuntu",
            "local_machine_fingerprint": str((self.local_machine or {}).get("machine_fingerprint") or "").strip(),
        }

    def _matching_control_plane_host(self, host_payload: dict[str, Any]) -> dict[str, Any] | None:
        fingerprint = str(host_payload.get("local_machine_fingerprint") or "").strip()
        mode = str(host_payload.get("host_mode") or host_payload.get("mode") or "").strip().lower()
        ssh_host = str(host_payload.get("ssh_host") or "").strip().lower()
        ssh_user = str(host_payload.get("ssh_user") or "").strip().lower()
        name = str(host_payload.get("name") or "").strip().lower()
        for host in self._control_plane_hosts():
            config = dict((host.get("config") or {}))
            if fingerprint and str(config.get("local_machine_fingerprint") or "").strip() == fingerprint:
                return host
            if (
                str(host.get("host_mode") or host.get("mode") or "").strip().lower() == mode
                and str(host.get("ssh_host") or "").strip().lower() == ssh_host
                and str(host.get("ssh_user") or "").strip().lower() == ssh_user
                and str(host.get("name") or "").strip().lower() == name
            ):
                return host
        return None

    def _control_plane_host_payload(self, host_payload: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
        payload = dict(host_payload or {})
        mode = str(payload.get("host_mode") or payload.get("mode") or "").strip()
        if mode:
            payload["host_mode"] = mode
        host_id = _coerce_int(payload.get("id"), 0)
        if host_id > 0:
            payload["id"] = host_id
        else:
            payload.pop("id", None)
        if status:
            payload["status"] = status
        return payload

    def _collect_project(self) -> dict[str, Any]:
        env_items: list[dict[str, Any]] = []
        for row in range(self.env_table.rowCount()):
            secret_item = self.env_table.item(row, 3)
            required_item = self.env_table.item(row, 4)
            env_items.append(
                {
                    "key": self._item_text(row, 0),
                    "label": self._item_text(row, 1),
                    "value": self._item_text(row, 2),
                    "secret": bool(secret_item and secret_item.checkState() == Qt.Checked),
                    "required": bool(required_item and required_item.checkState() == Qt.Checked),
                }
            )
        env_items = [item for item in env_items if item["key"]]

        return {
            **(self.current_project or {}),
            "id": self.current_project.get("id") if self.current_project else None,
            "template_id": self.template_select.currentData(),
            "name": self.project_name.text().strip() or "New project",
            "repo_url": self.project_repo_url.text().strip(),
            "branch": self.project_branch.text().strip() or "main",
            "deploy_path": self.project_deploy_path.text().strip() or "~/apps/my-app",
            "primary_domain": self.project_primary_domain.text().strip(),
            "letsencrypt_email": self.project_letsencrypt_email.text().strip() or "admin@example.com",
            "domains": [line.strip() for line in self.project_domains.toPlainText().splitlines() if line.strip()],
            "env": env_items,
        }

    def _item_text(self, row: int, column: int) -> str:
        item = self.env_table.item(row, column)
        return item.text() if item else ""

    def _load_selected_host(self, *_args: Any) -> None:
        host_id = self.saved_host_select.currentData()
        if not host_id:
            self.current_host = None
            self.current_plan = None
            self._render_plan()
            self._refresh_dashboard()
            return
        host = next((item for item in self.hosts if item["id"] == host_id), None)
        if host:
            self.current_host = host
            self.current_plan = None
            self._fill_host_form(host)
            self._render_plan()
            self._set_status(f"Loaded host {host['name']}", 3000)

    def _load_selected_project(self, *_args: Any) -> None:
        project_id = self.saved_project_select.currentData()
        if not project_id:
            self.current_project = None
            self.current_plan = None
            self._render_plan()
            self._refresh_dashboard()
            return
        project = next((item for item in self.projects if item["id"] == project_id), None)
        if project:
            self.current_project = project
            self.current_plan = None
            self._fill_project_form(project)
            self._render_plan()
            self._set_status(f"Loaded project {project['name']}", 3000)

    def _selected_instance_from_tree(self) -> dict[str, Any] | None:
        if not hasattr(self, "instances_tree"):
            return None
        item = self.instances_tree.currentItem()
        if not item:
            return None
        instance_id = item.data(0, Qt.UserRole)
        return next((entry for entry in self.instances if entry.get("id") == instance_id), None)

    def _instance_selection_changed(self) -> None:
        self.current_instance = self._selected_instance_from_tree()
        instance = self.current_instance
        if not hasattr(self, "instance_detail_output"):
            return
        self.update_instance_button.setEnabled(instance is not None)
        self.load_instance_button.setEnabled(instance is not None)
        self.backup_instance_button.setEnabled(instance is not None)
        self.delete_instance_button.setEnabled(instance is not None)
        if not instance:
            self.instance_detail_output.setHtml(
                "No managed instance selected yet.<br><br>Save the current host and project as an instance to unlock backups and lifecycle management."
            )
            return
        self.instance_detail_output.setHtml(self._instance_detail_html(instance))

    def _instance_detail_html(self, instance: dict[str, Any]) -> str:
        host = instance.get("host", {})
        project = instance.get("project", {})
        backups = instance.get("backups", [])
        backup_lines = "<br>".join(
            f"<b>{_escape(entry.get('created_at', ''))}</b>  {_escape(entry.get('status', 'unknown').upper())}  {_escape(entry.get('artifact_path', '(no path recorded)'))}"
            for entry in backups[:6]
        ) or "No backups recorded yet."
        return (
            f"<b>Instance</b><br>{_escape(instance.get('name', 'Unnamed instance'))}<br><br>"
            f"<b>Host</b><br>{_escape(host.get('name', 'Host'))}  ({_escape(host.get('mode', 'remote-linux'))})<br><br>"
            f"<b>Project</b><br>{_escape(project.get('name', 'Project'))}<br>"
            f"Deploy path: {_escape(project.get('deploy_path', ''))}<br>"
            f"Primary domain: {_escape(project.get('primary_domain', 'Not set'))}<br><br>"
            f"<b>Backups</b><br>{backup_lines}<br><br>"
            f"<b>Updated</b><br>{_escape(instance.get('updated_at', ''))}"
        )

    def _persist_current_profiles(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        host_response = self.service.upsert_host(self._collect_host())
        self.current_host = host_response["host"]
        project_response = self.service.upsert_project(self._collect_project())
        self.current_project = project_response["project"]
        state = project_response["state"]
        self.hosts = state["hosts"]
        self.projects = state["projects"]
        self._populate_hosts()
        self._populate_projects()
        return self.current_host, self.current_project, state

    def _build_instance_payload(self, *, existing_id: str | None = None, existing_instance: dict[str, Any] | None = None) -> dict[str, Any]:
        host, project, _state = self._persist_current_profiles()
        return {
            "id": existing_id,
            "name": (existing_instance or {}).get("name") or f"{project.get('name', 'Project')} on {host.get('name', 'Host')}",
            "description": (existing_instance or {}).get("description") or "Managed deployment instance.",
            "host_id": host.get("id"),
            "project_id": project.get("id"),
            "host": host,
            "project": project,
            "backups": list((existing_instance or {}).get("backups", [])),
            "created_at": (existing_instance or {}).get("created_at"),
        }

    def _save_instance(self) -> None:
        self._form_refresh_timer.stop()
        try:
            response = self.service.upsert_instance(self._build_instance_payload())
            self.instances = response["state"]["instances"]
            self.current_instance = response["instance"]
            self._populate_instances()
            self._refresh_dashboard()
            self._set_status(f"Saved instance {self.current_instance['name']}", 4000)
        except Exception as exc:
            self._show_error("Save instance failed", str(exc), traceback.format_exc())

    def _update_selected_instance(self) -> None:
        instance = self._selected_instance_from_tree()
        if not instance:
            self._set_status("Choose an instance first", 3000)
            return
        self._form_refresh_timer.stop()
        try:
            response = self.service.upsert_instance(
                self._build_instance_payload(existing_id=instance.get("id"), existing_instance=instance)
            )
            self.instances = response["state"]["instances"]
            self.current_instance = response["instance"]
            self._populate_instances()
            self._refresh_dashboard()
            self._set_status(f"Updated instance {self.current_instance['name']}", 4000)
        except Exception as exc:
            self._show_error("Update instance failed", str(exc), traceback.format_exc())

    def _load_selected_instance(self) -> None:
        instance = self._selected_instance_from_tree()
        if not instance:
            self._set_status("Choose an instance first", 3000)
            return
        self.current_instance = instance
        self.current_host = dict(instance.get("host", {}))
        self.current_project = dict(instance.get("project", {}))
        self.current_plan = None
        self._fill_host_form(self.current_host)
        self._fill_project_form(self.current_project)
        self._refresh_dashboard()
        self._render_plan()
        self._set_status(f"Loaded instance {instance['name']} into the hidden draft workspace", 4000)

    def _create_backup_for_selected_instance(self) -> None:
        instance = self._selected_instance_from_tree()
        if not instance:
            self._set_status("Choose an instance first", 3000)
            return
        self._run_async_task(
            start_message=f"Creating backup for {instance.get('name', 'instance')}...",
            work=lambda: self.service.create_instance_backup(str(instance.get("id"))),
            on_success=self._complete_instance_backup,
            error_title="Backup failed",
            done_message="Instance backup complete",
        )

    def _complete_instance_backup(self, result: dict[str, Any]) -> None:
        self.instances = result["state"]["instances"]
        self.current_instance = result["instance"]
        self._populate_instances()
        self.instance_detail_output.setHtml(self._instance_detail_html(self.current_instance))
        self.execution_output["output"].setPlainText(json.dumps({"backup": result["backup"], "results": result["results"]}, indent=2))
        self._switch_page(self.PAGE_ACTIVITY)

    def _delete_selected_instance(self) -> None:
        instance = self._selected_instance_from_tree()
        if not instance:
            self._set_status("Choose an instance first", 3000)
            return
        reply = QMessageBox.question(
            self,
            "Delete Instance",
            "This removes the managed instance record and its backup history from VPSdash. Saved host and project profiles stay in place. Continue?",
        )
        if reply != QMessageBox.Yes:
            return
        try:
            response = self.service.delete_instance(str(instance.get("id")))
            self.instances = response["state"]["instances"]
            if self.current_instance and self.current_instance.get("id") == instance.get("id"):
                self.current_instance = None
            self._populate_instances()
            self._refresh_dashboard()
            self._set_status(f"Deleted instance {instance.get('name', 'instance')}", 4000)
        except Exception as exc:
            self._show_error("Delete instance failed", str(exc), traceback.format_exc())

    def _save_host(self) -> None:
        self._form_refresh_timer.stop()
        try:
            host_payload = self._collect_host()
            legacy_response = self.service.upsert_host(host_payload)
            existing_platform_host = self._matching_control_plane_host(host_payload)
            existing_status = str((existing_platform_host or {}).get("status") or "").strip().lower()
            host_mode = str(host_payload.get("mode") or "").strip().lower()
            requested_status = existing_status if existing_status in {"ready", "provisioning", "queued"} else None
            if not requested_status and host_mode in {"windows-local", "linux-local"}:
                requested_status = "queued"
            platform_host = self.service.upsert_platform_host(
                self._control_plane_host_payload(host_payload, status=requested_status),
                actor="desktop",
            )
            self.current_host = legacy_response["host"]
            self.hosts = legacy_response["state"]["hosts"]
            self._populate_hosts()
            self._load_bootstrap()
            self._refresh_dashboard()
            self._set_status(f"Saved host {platform_host.get('name') or self.current_host['name']}", 4000)
        except Exception as exc:
            self._show_error("Save host failed", str(exc), traceback.format_exc())

    def _save_project(self) -> None:
        self._form_refresh_timer.stop()
        try:
            response = self.service.upsert_project(self._collect_project())
            self.current_project = response["project"]
            self.projects = response["state"]["projects"]
            self._populate_projects()
            self._refresh_dashboard()
            self._set_status(f"Saved project {self.current_project['name']}", 4000)
        except Exception as exc:
            self._show_error("Save project failed", str(exc), traceback.format_exc())

    def _save_all(self) -> None:
        self._save_host()
        self._save_project()

    def _generate_plan(self) -> None:
        self._form_refresh_timer.stop()
        try:
            payload = self.service.generate_plan(self._collect_host(), self._collect_project())
            self.current_host = payload["host"]
            self.current_project = payload["project"]
            self.current_plan = payload["plan"]
            self._render_plan()
            self._refresh_dashboard()
            warning_count = len(self.current_plan.get("warnings", []))
            if warning_count:
                self._set_status(f"Plan generated with {warning_count} warning(s)", 5000)
            else:
                self._set_status("Legacy plan generated in background", 4000)
        except Exception as exc:
            self._show_error("Plan generation failed", str(exc), traceback.format_exc())

    def _render_plan(self) -> None:
        self.plan_tree.clear()
        if not self.current_plan:
            self.plan_target_body.setText(
                "No plan generated yet. Define the host and project in Setup, then generate a plan."
            )
            self.warning_box.setHtml("<b>No plan yet.</b><br>Generate a plan to see warnings and execution stages.")
            return

        summary = self.current_plan["summary"]
        self.plan_target_body.setText(
            f"<b>Host</b><br>{_escape(summary['host_name'])}<br><br>"
            f"<b>Project</b><br>{_escape(summary['project_name'])}<br><br>"
            f"<b>Deploy path</b><br>{_escape(summary['deploy_path'])}<br><br>"
            f"<b>Shell</b><br>{_escape(summary['shell'])}"
        )

        warnings = self.current_plan.get("warnings") or []
        if warnings:
            items = "".join(f"<li>{_escape(warning)}</li>" for warning in warnings)
            self.warning_box.setHtml(f"<ul>{items}</ul>")
        else:
            self.warning_box.setHtml("<b>No blocking warnings.</b><br>The generated plan is ready for a dry run.")

        for stage in self.current_plan["stages"]:
            stage_item = QTreeWidgetItem([stage["title"], f"{len(stage['steps'])} step(s)", ""])
            for step in stage["steps"]:
                child = QTreeWidgetItem([step["title"], step.get("detail", ""), step.get("command", "")])
                stage_item.addChild(child)
            self.plan_tree.addTopLevelItem(stage_item)
            stage_item.setExpanded(True)

    def _complete_diagnostics(self, result: dict[str, Any]) -> None:
        self.diagnostics_output["output"].setPlainText(json.dumps(result, indent=2))
        self._switch_page(self.PAGE_ACTIVITY)

    def _complete_monitor_snapshot(self, result: dict[str, Any]) -> None:
        self.monitor_output["output"].setPlainText(json.dumps(result, indent=2))
        self._switch_page(self.PAGE_ACTIVITY)

    def _complete_execution(self, result: dict[str, Any]) -> None:
        self.execution_output["output"].setPlainText(json.dumps(result, indent=2))
        self._switch_page(self.PAGE_ACTIVITY)

    def _run_diagnostics(self) -> None:
        self._form_refresh_timer.stop()
        host = self._collect_host()
        project = self._collect_project()
        self._run_async_task(
            start_message="Running diagnostics...",
            work=lambda: self.service.diagnostics(host, project),
            on_success=self._complete_diagnostics,
            error_title="Diagnostics failed",
            done_message="Diagnostics complete",
        )

    def _run_monitor_snapshot(self) -> None:
        self._form_refresh_timer.stop()
        host = self._collect_host()
        project = self._collect_project()
        self._run_async_task(
            start_message="Capturing snapshot...",
            work=lambda: self.service.monitor_snapshot(host, project),
            on_success=self._complete_monitor_snapshot,
            error_title="Snapshot failed",
            done_message="Snapshot captured",
        )

    def _execute_plan(self, dry_run: bool) -> None:
        self._form_refresh_timer.stop()
        if not self.current_plan:
            self._generate_plan()
        if not self.current_plan:
            return
        steps = [step for stage in self.current_plan["stages"] for step in stage["steps"]]
        if not dry_run:
            reply = QMessageBox.question(
                self,
                "Execute Live Plan",
                "This will run the generated commands against the selected host. Continue?",
            )
            if reply != QMessageBox.Yes:
                return
        host = self._collect_host()
        self._run_async_task(
            start_message="Dry running plan..." if dry_run else "Executing live plan...",
            work=lambda: self.service.execute(host, steps, dry_run=dry_run),
            on_success=self._complete_execution,
            error_title="Execution failed",
            done_message="Dry run complete" if dry_run else "Live execution complete",
        )

    def _host_mode_changed(self, *_args: Any) -> None:
        mode = self.host_mode.currentText()
        is_remote = mode in {"remote-linux", "windows-remote"}
        is_windows = mode in {"windows-local", "windows-remote"}
        device_role = self.host_device_role.currentData()
        bootstrap_auth = self.host_bootstrap_auth.currentData()
        self.host_ssh_user.setEnabled(is_remote)
        self.host_ssh_host.setEnabled(is_remote)
        self.host_ssh_port.setEnabled(is_remote)
        self.host_ssh_key.setEnabled(is_remote)
        self.host_wsl_distribution.setEnabled(is_windows)

        if mode in {"remote-linux", "windows-remote"}:
            role_hint = (
                "This profile is describing Computer B, the machine that will become the server."
                if device_role == "computer-b-server"
                else "This profile is describing Computer A, the main control machine."
            )
            auth_hint = (
                "Start with password SSH, then switch to key-based SSH after the first successful login from Computer A."
                if bootstrap_auth == "password-bootstrap"
                else "Key-based SSH is expected to be ready before remote automation starts."
            )
            if mode == "windows-remote":
                self.host_mode_hint.setText(
                    f"Remote Windows uses SSH to reach the machine, then runs Linux-side hosting inside WSL. {role_hint} {auth_hint}"
                )
            else:
                self.host_mode_hint.setText(f"Remote Linux uses SSH. {role_hint} {auth_hint}")
        elif mode == "linux-local":
            self.host_mode_hint.setText(
                "Local Linux runs commands directly on this machine. SSH details are intentionally disabled."
            )
        else:
            self.host_mode_hint.setText(
                "Windows local mode uses WSL as the Linux host layer. Services only stay available while the Windows machine stays awake."
            )

    def _handle_form_changed(self, *_args: Any) -> None:
        if self._live_updates_suspended:
            return
        self._form_refresh_timer.start()

    def _web_admin_url(self, anchor: str | None = None, *, embedded: bool = False) -> str:
        base_url = f"{self.web_admin_server.start()}/dashboard"
        if embedded:
            base_url = f"{base_url}?embedded=1"
        return f"{base_url}{anchor}" if anchor else base_url

    def _apply_control_plane_height(self, raw_height: Any) -> None:
        if self.control_plane_view is None:
            return
        try:
            height = int(float(raw_height))
        except (TypeError, ValueError):
            return
        target_height = max(1200, min(height + 40, 12000))
        if self.control_plane_view.minimumHeight() == target_height and self.control_plane_view.maximumHeight() == target_height:
            return
        self.control_plane_view.setMinimumHeight(target_height)
        self.control_plane_view.setMaximumHeight(target_height)
        self.control_plane_view.updateGeometry()
        if hasattr(self, "control_plane_scroll"):
            self.control_plane_scroll.widget().adjustSize()
            self.control_plane_scroll.ensureWidgetVisible(self.control_plane_view, 0, 0)

    def _refresh_control_plane_height(self) -> None:
        if self.control_plane_view is None:
            return
        page = self.control_plane_view.page()
        if page is None:
            return
        script = (
            "Math.max("
            "document.body ? document.body.scrollHeight : 0,"
            "document.documentElement ? document.documentElement.scrollHeight : 0,"
            "document.body ? document.body.offsetHeight : 0,"
            "document.documentElement ? document.documentElement.offsetHeight : 0,"
            "(document.querySelector('.shell') ? document.querySelector('.shell').scrollHeight : 0),"
            "(document.querySelector('.workspace-shell') ? document.querySelector('.workspace-shell').scrollHeight : 0),"
            "(document.querySelector('.app-shell') ? document.querySelector('.app-shell').scrollHeight : 0)"
            ")"
        )
        page.runJavaScript(script, self._apply_control_plane_height)

    def _ensure_control_plane_view(self) -> Any | None:
        if self.control_plane_view is not None:
            return self.control_plane_view
        view_class = _get_webengine_view_class()
        if view_class is None or self.control_plane_panel_layout is None:
            return None
        self.control_plane_view = view_class()
        self.control_plane_view.setMinimumHeight(1200)
        self.control_plane_view.setMaximumHeight(1200)
        self.control_plane_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        page = self.control_plane_view.page()
        if page is not None:
            if hasattr(page, "contentsSizeChanged"):
                page.contentsSizeChanged.connect(lambda size: self._apply_control_plane_height(size.height()))
            self.control_plane_view.loadFinished.connect(lambda _ok: QTimer.singleShot(150, self._refresh_control_plane_height))
            self.control_plane_view.loadFinished.connect(lambda _ok: QTimer.singleShot(700, self._refresh_control_plane_height))
            self.control_plane_view.loadFinished.connect(lambda _ok: QTimer.singleShot(1500, self._refresh_control_plane_height))
            self.control_plane_view.loadFinished.connect(lambda _ok: self._control_plane_height_timer.start())
        if self.control_plane_placeholder is not None:
            self.control_plane_panel_layout.removeWidget(self.control_plane_placeholder)
            self.control_plane_placeholder.deleteLater()
            self.control_plane_placeholder = None
        self.control_plane_panel_layout.addWidget(self.control_plane_view, 1)
        return self.control_plane_view

    def _navigate_embedded_admin(
        self,
        *,
        anchor: str | None = None,
        force_reload: bool = False,
        open_browser_fallback: bool = False,
    ) -> str:
        url = self._web_admin_url(anchor, embedded=True)
        view = self._ensure_control_plane_view()
        if view is not None:
            if force_reload or view.url().toString() != url:
                view.setUrl(QUrl(url))
                if force_reload:
                    view.reload()
        elif open_browser_fallback:
            webbrowser.open(url)
        elif self.control_plane_placeholder is not None:
            self.control_plane_placeholder.setHtml(
                "<h3>Embedded Doplet admin is unavailable in this runtime.</h3>"
                f"<p>Open the control plane in your browser instead: <a href=\"{_escape(url)}\">{_escape(url)}</a></p>"
            )
        self.control_plane_status.setText(f"Doplet admin running at {url}")
        return url

    def _open_host_admin(self) -> None:
        self._show_native_doplets_page(getattr(self, "host_profile_card", None))
        self._set_status("Opened native Host profile", 4000)

    def _open_doplet_builder(self) -> None:
        self._show_native_doplets_page(getattr(self, "project_profile_card", None))
        self._set_status("Opened native Doplet builder inputs", 4000)

    def _open_doplet_admin(self) -> None:
        self._switch_page(self.PAGE_ACTIVITY)
        if hasattr(self, "instances_tree"):
            QTimer.singleShot(0, lambda: self.operations_scroll.ensureWidgetVisible(self.instances_tree, 0, 48))
        self._set_status("Opened native Doplet management", 4000)

    def _open_web_admin(self) -> None:
        try:
            url = self._web_admin_url()
            webbrowser.open(url)
            self._set_status(f"Opened Doplet admin at {url}", 5000)
        except Exception as exc:
            self._show_error("Web admin failed", str(exc), traceback.format_exc())

    def _show_error(self, title: str, message: str, details: str | None = None) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        console_entry = f"[{timestamp}] {title}\n{message}"
        if details:
            console_entry = f"{console_entry}\n\n{details.strip()}"
        self._error_events.insert(0, console_entry)
        self._error_events = self._error_events[:30]
        self._refresh_error_console()
        self._set_status(f"{title}. See Activity > Error console.", 6000)
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Critical)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        if details:
            dialog.setDetailedText(details)
        dialog.exec()
        self._set_status(f"{title}: {message}", 6000)

    def _load_embedded_control_plane(self, force_reload: bool = False) -> None:
        try:
            url = self._navigate_embedded_admin(force_reload=force_reload)
            self._set_status(f"Doplet admin ready at {url}", 4000)
        except Exception as exc:
            self._show_error("Doplet admin failed", str(exc), traceback.format_exc())

    def closeEvent(self, event: QEvent) -> None:
        self._control_plane_height_timer.stop()
        self.web_admin_server.stop()
        super().closeEvent(event)

    def force_foreground(self) -> None:
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.showMaximized()
        if os.name == "nt":
            try:
                hwnd = int(self.winId())
                user32 = ctypes.windll.user32
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass


def run_desktop_app() -> None:
    _write_startup_log("Starting VPSdash desktop app")
    app = QApplication(sys.argv)
    icon_path = _app_icon_path()
    if icon_path.exists():
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)
    font = QFont(app.font())
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(APP_QSS)
    service = VpsDashService(_state_root(), resource_root=_resource_root())
    window = VpsDashWindow(service)
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    _write_startup_log("QApplication and main window created")

    def _handle_uncaught_exception(exc_type: Any, exc_value: BaseException, exc_traceback: Any) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        _write_startup_log(f"Unhandled exception: {exc_value}\n{details}")
        window._show_error("Unexpected application error", str(exc_value), details)

    sys.excepthook = _handle_uncaught_exception
    window.show()
    QTimer.singleShot(0, window.force_foreground)
    sys.exit(app.exec())



