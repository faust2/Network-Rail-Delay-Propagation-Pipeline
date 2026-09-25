from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop_app.workers.database_workers import DatabaseWorker
from railway_delay.database_service import inspect_database


class DatabasePage(QWidget):
    status_changed = Signal(str)

    def __init__(
        self,
        credential_provider: Callable[[], tuple[str, str]],
        data_directory_provider: Callable[[], Path],
    ) -> None:
        super().__init__()
        self.setObjectName("page")
        self._credential_provider = credential_provider
        self._data_directory_provider = data_directory_provider
        self._thread: QThread | None = None
        self._worker: DatabaseWorker | None = None

        self.database_path_label = QLabel()
        self.database_size_value = QLabel("—")
        self.schedule_value = QLabel("0")
        self.location_value = QLabel("0")
        self.movement_value = QLabel("0")
        self.progress_bar = QProgressBar()
        self.operation_label = QLabel("Ready")
        self.log = QPlainTextEdit()
        self.download_button = QPushButton("Download timetable")
        self.build_button = QPushButton("Build timetable database")
        self.movements_button = QPushButton("Load movement file")
        self.refresh_button = QPushButton("Refresh summary")

        self._build_interface()
        self.refresh_summary()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _build_interface(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(38, 34, 38, 38)

        heading = QHBoxLayout()
        heading_group = QVBoxLayout()
        title = QLabel("Database workspace")
        title.setObjectName("pageTitle")
        description = QLabel(
            "Download timetable data, build railway.db and load captured "
            "movement records."
        )
        description.setObjectName("pageDescription")
        heading_group.addWidget(title)
        heading_group.addWidget(description)
        heading.addLayout(heading_group)
        heading.addStretch()
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.clicked.connect(self.refresh_summary)
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)
        layout.addSpacing(16)

        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(11)
        summary_grid.addWidget(
            self._summary_card("Database size", self.database_size_value), 0, 0
        )
        summary_grid.addWidget(
            self._summary_card("Schedules", self.schedule_value), 0, 1
        )
        summary_grid.addWidget(
            self._summary_card("Schedule locations", self.location_value), 0, 2
        )
        summary_grid.addWidget(
            self._summary_card("Movements", self.movement_value), 0, 3
        )
        layout.addLayout(summary_grid)

        self.database_path_label.setObjectName("databasePath")
        self.database_path_label.setWordWrap(True)
        layout.addWidget(self.database_path_label)
        layout.addSpacing(10)

        operations_card = QFrame()
        operations_card.setObjectName("raisedCard")
        operations_layout = QVBoxLayout(operations_card)
        operations_layout.setContentsMargins(20, 18, 20, 18)

        operations_title = QLabel("Data preparation")
        operations_title.setObjectName("cardTitle")
        operations_layout.addWidget(operations_title)

        button_row = QHBoxLayout()
        self.download_button.setObjectName("primaryButton")
        self.build_button.setObjectName("secondaryButton")
        self.movements_button.setObjectName("secondaryButton")
        self.download_button.clicked.connect(self.download_timetable)
        self.build_button.clicked.connect(self.build_database)
        self.movements_button.clicked.connect(self.load_movements)
        button_row.addWidget(self.download_button)
        button_row.addWidget(self.build_button)
        button_row.addWidget(self.movements_button)
        button_row.addStretch()
        operations_layout.addLayout(button_row)

        warning = QLabel(
            "Building the timetable replaces the timetable tables. Loading a "
            "movement file replaces the existing movement table. Confirmation "
            "is required before either operation."
        )
        warning.setObjectName("databaseWarning")
        warning.setWordWrap(True)
        operations_layout.addWidget(warning)

        self.progress_bar.setObjectName("operationProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        operations_layout.addWidget(self.progress_bar)

        self.operation_label.setObjectName("operationLabel")
        operations_layout.addWidget(self.operation_label)

        self.log.setObjectName("databaseConsole")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setPlaceholderText("Database operations will be recorded here")
        operations_layout.addWidget(self.log, 1)

        layout.addWidget(operations_card, 1)

    @staticmethod
    def _summary_card(label_text: str, value_label: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("metricCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 11, 14, 11)
        label = QLabel(label_text)
        label.setObjectName("metricLabel")
        value_label.setObjectName("databaseMetricValue")
        card_layout.addWidget(label)
        card_layout.addWidget(value_label)
        return card

    def refresh_summary(self) -> None:
        database_path = self._database_path()
        summary = inspect_database(database_path)
        self.database_path_label.setText(f"Database: {database_path}")
        size = int(summary["size_bytes"])
        self.database_size_value.setText(self._format_bytes(size))
        self.schedule_value.setText(f"{int(summary['schedules']):,}")
        self.location_value.setText(f"{int(summary['locations']):,}")
        self.movement_value.setText(f"{int(summary['movements']):,}")

    def download_timetable(self) -> None:
        username, password = self._credential_provider()
        if not username or not password:
            self._append_log("Enter credentials on the Connection page first")
            return
        output_path = self._raw_directory() / "schedule_full.json.gz"
        self._start_operation(
            "download",
            {
                "username": username,
                "password": password,
                "output_path": str(output_path),
            },
            "Downloading full CIF timetable…",
        )

    def build_database(self) -> None:
        timetable_path = self._raw_directory() / "schedule_full.json.gz"
        if not timetable_path.exists():
            self._append_log("Download the timetable before building the database")
            return
        answer = QMessageBox.warning(
            self,
            "Replace timetable tables?",
            "This rebuilds the tiploc, schedules and schedule_locations tables. "
            "Existing movement records are retained. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_operation(
            "build",
            {
                "input_path": str(timetable_path),
                "database_path": str(self._database_path()),
            },
            "Building timetable database…",
        )

    def load_movements(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Choose captured movement file",
            str(self._raw_directory()),
            "JSON Lines (*.jsonl);;All files (*)",
        )
        if not selected:
            return
        answer = QMessageBox.warning(
            self,
            "Replace movement table?",
            "This replaces all rows currently in train_movements with the "
            "selected capture file. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_operation(
            "movements",
            {
                "input_path": selected,
                "database_path": str(self._database_path()),
            },
            "Loading movement records…",
        )

    def shutdown(self, timeout_ms: int = 5000) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(timeout_ms)

    def _start_operation(
        self,
        operation: str,
        arguments: dict[str, str],
        initial_message: str,
    ) -> None:
        if self.is_running:
            self._append_log("Wait for the current operation to finish")
            return

        self._set_controls_enabled(False)
        self.operation_label.setText(initial_message)
        self.progress_bar.setRange(0, 0)
        self._append_log(initial_message)
        self.status_changed.emit(initial_message)

        self._thread = QThread(self)
        self._worker = DatabaseWorker(operation, arguments)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._operation_progress)
        self._worker.succeeded.connect(self._operation_succeeded)
        self._worker.failed.connect(self._operation_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._operation_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _operation_progress(self, current: int, total: int, message: str) -> None:
        if total > 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(min(100, int(current / total * 100)))
        else:
            self.progress_bar.setRange(0, 0)
        self.operation_label.setText(message)
        self._append_log(message)

    def _operation_succeeded(self, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.operation_label.setText(message)
        self._append_log(message)
        self.status_changed.emit(message)

    def _operation_failed(self, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.operation_label.setText(f"Error: {message}")
        self._append_log(f"ERROR — {message}")
        self.status_changed.emit(message)

    def _operation_finished(self) -> None:
        self._worker = None
        self._thread = None
        self._set_controls_enabled(True)
        self.refresh_summary()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.download_button.setEnabled(enabled)
        self.build_button.setEnabled(enabled)
        self.movements_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)

    def _database_path(self) -> Path:
        return self._data_directory_provider() / "railway.db"

    def _raw_directory(self) -> Path:
        return self._data_directory_provider() / "raw"

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"{timestamp}  {message}")

    @staticmethod
    def _format_bytes(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"
