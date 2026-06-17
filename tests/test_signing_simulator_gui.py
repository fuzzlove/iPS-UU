from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from ips_uu.gui.app import MainWindow
from ips_uu.services.mock_tss_service import SIMULATION_BANNER


def test_signing_simulator_gui_shows_simulation_banner() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.thread_pool.waitForDone(10000)
        assert "Signing Simulator" in window.nav_items
        window.nav.setCurrentRow(window.nav_items.index("Signing Simulator"))
        labels = [label.text() for label in window.findChildren(QLabel)]
        buttons = [button.text() for button in window.findChildren(QPushButton)]
        assert SIMULATION_BANNER in labels
        assert window.signing_simulation_toggle.text() == "Enable local signing simulation mode"
        assert "Simulate Restore/Flash" in buttons
    finally:
        window.close()
