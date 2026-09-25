from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from desktop_app.pages.connection_page import ConnectionPage
from desktop_app.pages.database_page import DatabasePage
from desktop_app.pages.live_feed_page import LiveFeedPage
from desktop_app.pages.train_analysis_page import TrainAnalysisPage
from desktop_app.pages.propagation_page import PropagationPage
from desktop_app.pages.validation_page import ValidationPage
from desktop_app.pages.recovery_page import RecoveryPage
from desktop_app.pages.reports_page import ReportsPage
from desktop_app.pages.placeholder_page import PlaceholderPage


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RailConnect 2000")
        self.resize(1180, 760)
        self.setMinimumSize(920, 620)

        self.pages = QStackedWidget()
        self.navigation = QListWidget()
        self.connection_page = ConnectionPage()
        self.live_feed_page = LiveFeedPage(
            credential_provider=self.connection_page.session_credentials,
            data_directory_provider=self.connection_page.data_directory,
        )
        self.database_page = DatabasePage(
            credential_provider=self.connection_page.session_credentials,
            data_directory_provider=self.connection_page.data_directory,
        )
        self.train_analysis_page = TrainAnalysisPage(
            data_directory_provider=self.connection_page.data_directory
        )
        self.propagation_page = PropagationPage(
            data_directory_provider=self.connection_page.data_directory
        )
        self.validation_page = ValidationPage(
            data_directory_provider=self.connection_page.data_directory
        )
        self.recovery_page = RecoveryPage(
            data_directory_provider=self.connection_page.data_directory
        )
        self.reports_page = ReportsPage(
            data_directory_provider=self.connection_page.data_directory
        )

        self._build_interface()

    def _build_interface(self) -> None:
        root = QWidget()
        root.setObjectName("applicationRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_header())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self.pages, 1)
        root_layout.addWidget(body, 1)

        self.setCentralWidget(root)
        self._add_pages()

        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)

        self.statusBar().showMessage("Ready — credentials are not stored on disk")
        self.connection_page.status_changed.connect(self.statusBar().showMessage)
        self.live_feed_page.status_changed.connect(self.statusBar().showMessage)
        self.database_page.status_changed.connect(self.statusBar().showMessage)
        self.train_analysis_page.status_changed.connect(self.statusBar().showMessage)
        self.propagation_page.status_changed.connect(self.statusBar().showMessage)
        self.validation_page.status_changed.connect(self.statusBar().showMessage)
        self.recovery_page.status_changed.connect(self.statusBar().showMessage)
        self.reports_page.status_changed.connect(self.statusBar().showMessage)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("applicationHeader")
        header.setFixedHeight(70)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 0, 24, 0)

        brand = QLabel("RailConnect 2000")
        brand.setObjectName("applicationBrand")

        subtitle = QLabel("Network Rail Delay Propagation System")
        subtitle.setObjectName("applicationSubtitle")

        title_group = QVBoxLayout()
        title_group.setSpacing(1)
        title_group.addWidget(brand)
        title_group.addWidget(subtitle)

        status_dot = QLabel("●")
        status_dot.setObjectName("offlineStatusDot")

        status_text = QLabel("NOT CONNECTED")
        status_text.setObjectName("headerStatusText")

        layout.addLayout(title_group)
        layout.addStretch()
        layout.addWidget(status_dot)
        layout.addWidget(status_text)

        self.connection_page.connection_state_changed.connect(status_text.setText)
        self.connection_page.connection_state_changed.connect(
            lambda state: status_dot.setStyleSheet(
                "color: #23796A; font-size: 17px;"
                if state == "OPEN DATA CONNECTED"
                else "color: #7A8598; font-size: 17px;"
            )
        )

        return header

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(215)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 22, 14, 18)
        layout.setSpacing(10)

        label = QLabel("MY RAILWAY")
        label.setObjectName("sidebarLabel")
        layout.addWidget(label)

        self.navigation.setObjectName("navigationList")
        self.navigation.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.navigation.setSpacing(5)
        layout.addWidget(self.navigation, 1)

        privacy = QLabel("LOCAL SESSION\nSQLite workspace")
        privacy.setObjectName("sidebarFooter")
        layout.addWidget(privacy)

        return sidebar

    def _add_pages(self) -> None:
        page_definitions = [
            ("Connection", self.connection_page),
            (
                "Live Feed",
                self.live_feed_page,
            ),
            (
                "Database",
                self.database_page,
            ),
            (
                "Train Analysis",
                self.train_analysis_page,
            ),
            (
                "Propagation",
                self.propagation_page,
            ),
            (
                "Validation",
                self.validation_page,
            ),
            (
                "Recovery",
                self.recovery_page,
            ),
            (
                "Reports",
                self.reports_page,
            ),
        ]

        for title, page in page_definitions:
            self.navigation.addItem(QListWidgetItem(title))
            self.pages.addWidget(page)

    def closeEvent(self, event: QCloseEvent) -> None:
        if (
            self.database_page.is_running
            or self.train_analysis_page.is_running
            or self.propagation_page.is_running
            or self.validation_page.is_running
            or self.recovery_page.is_running
            or self.reports_page.is_running
        ):
            QMessageBox.information(
                self,
                "Operation in progress",
                "Wait for the current database or analysis operation to finish before "
                "closing RailConnect 2000.",
            )
            event.ignore()
            return
        self.live_feed_page.shutdown()
        event.accept()
