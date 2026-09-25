from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlaceholderPage(QWidget):
    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.setObjectName("page")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(38, 34, 38, 38)

        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")

        description_label = QLabel(description)
        description_label.setObjectName("pageDescription")
        description_label.setWordWrap(True)

        empty_panel = QLabel("COMING SOON")
        empty_panel.setObjectName("emptyPanel")
        empty_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addSpacing(20)
        layout.addWidget(empty_panel, 1)
