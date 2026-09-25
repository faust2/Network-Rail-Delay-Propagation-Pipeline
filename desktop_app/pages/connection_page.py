from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop_app.workers.connection_worker import ConnectionWorker


class ConnectionPage(QWidget):
    status_changed = Signal(str)
    connection_state_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")

        self._thread: QThread | None = None
        self._worker: ConnectionWorker | None = None

        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.data_directory_input = QLineEdit()
        self.show_password_button = QPushButton("Show")
        self.test_button = QPushButton("Test connection")
        self.result_label = QLabel("Not tested")

        self._build_interface()

    def _build_interface(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(38, 34, 38, 38)

        title = QLabel("Open Data connection")
        title.setObjectName("pageTitle")

        description = QLabel(
            "Enter your Network Rail Open Data credentials and choose where "
            "RailConnect should keep downloaded and captured data."
        )
        description.setObjectName("pageDescription")
        description.setWordWrap(True)

        card = QFrame()
        card.setObjectName("raisedCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(26, 24, 26, 24)
        card_layout.setSpacing(18)

        card_title = QLabel("Account and workspace")
        card_title.setObjectName("cardTitle")
        card_layout.addWidget(card_title)

        form = QFormLayout()
        form.setHorizontalSpacing(22)
        form.setVerticalSpacing(15)

        self.username_input.setPlaceholderText("Network Rail Open Data email")
        self.username_input.setClearButtonEnabled(True)

        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)

        password_row = QWidget()
        password_layout = QHBoxLayout(password_row)
        password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.setSpacing(8)
        self.show_password_button.setObjectName("secondaryButton")
        self.show_password_button.setCheckable(True)
        self.show_password_button.toggled.connect(self._toggle_password)
        password_layout.addWidget(self.password_input, 1)
        password_layout.addWidget(self.show_password_button)

        default_data_directory = Path.cwd() / "data"
        self.data_directory_input.setText(str(default_data_directory))

        directory_row = QWidget()
        directory_layout = QHBoxLayout(directory_row)
        directory_layout.setContentsMargins(0, 0, 0, 0)
        directory_layout.setSpacing(8)
        browse_button = QPushButton("Browse")
        browse_button.setObjectName("secondaryButton")
        browse_button.clicked.connect(self._choose_directory)
        directory_layout.addWidget(self.data_directory_input, 1)
        directory_layout.addWidget(browse_button)

        form.addRow(self._form_label("Username"), self.username_input)
        form.addRow(self._form_label("Password"), password_row)
        form.addRow(self._form_label("Data directory"), directory_row)
        card_layout.addLayout(form)

        privacy_note = QLabel(
            "Credentials are used only for this running session. "
            "This version does not save them to disk."
        )
        privacy_note.setObjectName("privacyNote")
        privacy_note.setWordWrap(True)
        card_layout.addWidget(privacy_note)

        actions = QHBoxLayout()
        self.test_button.setObjectName("primaryButton")
        self.test_button.clicked.connect(self._test_connection)
        self.result_label.setObjectName("connectionResult")
        actions.addWidget(self.test_button)
        actions.addWidget(self.result_label)
        actions.addStretch()
        card_layout.addLayout(actions)

        page_layout.addWidget(title)
        page_layout.addWidget(description)
        page_layout.addSpacing(20)
        page_layout.addWidget(card)
        page_layout.addStretch()

    @staticmethod
    def _form_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("formLabel")
        return label

    def session_credentials(self) -> tuple[str, str]:
        """Return the credentials currently entered for in-memory session use."""
        return self.username_input.text().strip(), self.password_input.text()

    def data_directory(self) -> Path:
        return Path(self.data_directory_input.text()).expanduser()

    def _toggle_password(self, checked: bool) -> None:
        self.password_input.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.show_password_button.setText("Hide" if checked else "Show")

    def _choose_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose RailConnect data directory",
            self.data_directory_input.text(),
        )
        if selected:
            self.data_directory_input.setText(selected)

    def _test_connection(self) -> None:
        username = self.username_input.text().strip()
        password = self.password_input.text()

        if not username or not password:
            self._show_failure("Enter both your username and password.")
            return

        self.test_button.setEnabled(False)
        self.result_label.setText("Testing…")
        self.status_changed.emit("Testing Network Rail Open Data credentials…")

        self._thread = QThread(self)
        self._worker = ConnectionWorker(username, password)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._show_success)
        self._worker.failed.connect(self._show_failure)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._connection_test_finished)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def _show_success(self, message: str) -> None:
        self.result_label.setText(message)
        self.result_label.setProperty("state", "success")
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)
        self.connection_state_changed.emit("OPEN DATA CONNECTED")
        self.status_changed.emit(message)

    def _show_failure(self, message: str) -> None:
        self.result_label.setText(message)
        self.result_label.setProperty("state", "failure")
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)
        self.connection_state_changed.emit("NOT CONNECTED")
        self.status_changed.emit(message)

    def _connection_test_finished(self) -> None:
        self.test_button.setEnabled(True)
        self._worker = None
        self._thread = None
