from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from desktop_app.main_window import MainWindow


def load_stylesheet(application: QApplication) -> None:
    stylesheet_path = Path(__file__).parent / "resources" / "transit_os_2000.qss"
    application.setStyleSheet(stylesheet_path.read_text(encoding="utf-8"))


def main() -> None:
    application = QApplication(sys.argv)
    application.setApplicationName("RailConnect 2000")
    application.setOrganizationName("RailConnect")
    load_stylesheet(application)

    window = MainWindow()
    window.show()

    sys.exit(application.exec())


if __name__ == "__main__":
    main()
