from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel

from ips_uu.gui.app import MainWindow


def test_about_page_shows_support_donation_panel() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.thread_pool.waitForDone(10000)
        window.nav.setCurrentRow(window.nav_items.index("About"))
        labels = [label.text() for label in window.findChildren(QLabel)]
        support_labels = [label for label in window.findChildren(QLabel) if "patreon.com/cw/fuzzlove" in label.text()]
        assert any("Support Continued Development" in label.text() for label in support_labels)
        assert any("Optional support only. The app remains usable without a donation." in label.text() for label in support_labels)
        assert all(label.openExternalLinks() for label in support_labels)
        assert any(label.objectName() == "DonationQR" for label in window.findChildren(QLabel))
    finally:
        window.close()
