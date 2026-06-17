from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from ips_uu.gui.app import MainWindow
from ips_uu.services.purple_restore_service import PURPLE_SIMULATION_BANNER


def test_purple_restore_gui_constructs_and_shows_warning() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.thread_pool.waitForDone(10000)
        assert "Purple Restore" in window.nav_items
        window.nav.setCurrentRow(window.nav_items.index("Purple Restore"))
        labels = [label.text() for label in window.findChildren(QLabel)]
        buttons = [button.text() for button in window.findChildren(QPushButton)]
        assert PURPLE_SIMULATION_BANNER in labels
        assert "Prepare Purple Restore" in buttons
        assert "Request Mock Tatsu Ticket" in buttons
        assert "Simulate Restore" in buttons
    finally:
        window.close()

