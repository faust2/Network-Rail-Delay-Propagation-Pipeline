from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop_app.workers.movement_worker import MovementWorker


class LiveFeedPage(QWidget):
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
        self._worker: MovementWorker | None = None
        self._started_at: datetime | None = None

        self.message_value = QLabel("0")
        self.batch_value = QLabel("0")
        self.elapsed_value = QLabel("00:00:00")
        self.feed_state = QLabel("STOPPED")
        self.output_path = QLabel("No capture file created")
        self.log = QPlainTextEdit()
        self.start_button = QPushButton("Start capture")
        self.stop_button = QPushButton("Stop capture")

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_elapsed_time)

        self._build_interface()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _build_interface(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(38, 34, 38, 38)

        heading_row = QHBoxLayout()
        heading_group = QVBoxLayout()

        title = QLabel("Live movement capture")
        title.setObjectName("pageTitle")
        description = QLabel(
            "Capture TRUST movement messages directly from the "
            "TRAIN_MVT_ALL_TOC feed."
        )
        description.setObjectName("pageDescription")

        heading_group.addWidget(title)
        heading_group.addWidget(description)
        heading_row.addLayout(heading_group)
        heading_row.addStretch()

        self.feed_state.setObjectName("feedState")
        self.feed_state.setProperty("state", "stopped")
        heading_row.addWidget(self.feed_state)
        layout.addLayout(heading_row)
        layout.addSpacing(18)

        metric_layout = QGridLayout()
        metric_layout.setHorizontalSpacing(13)
        metric_layout.addWidget(
            self._metric_card("Messages captured", self.message_value), 0, 0
        )
        metric_layout.addWidget(
            self._metric_card("Batches received", self.batch_value), 0, 1
        )
        metric_layout.addWidget(
            self._metric_card("Elapsed time", self.elapsed_value), 0, 2
        )
        layout.addLayout(metric_layout)
        layout.addSpacing(13)

        console_card = QFrame()
        console_card.setObjectName("raisedCard")
        console_layout = QVBoxLayout(console_card)
        console_layout.setContentsMargins(20, 18, 20, 18)

        console_heading = QHBoxLayout()
        console_title = QLabel("Feed console")
        console_title.setObjectName("cardTitle")
        topic = QLabel("/topic/TRAIN_MVT_ALL_TOC")
        topic.setObjectName("feedTopic")
        console_heading.addWidget(console_title)
        console_heading.addStretch()
        console_heading.addWidget(topic)
        console_layout.addLayout(console_heading)

        self.log.setObjectName("feedConsole")
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Capture events will appear here")
        console_layout.addWidget(self.log, 1)

        self.output_path.setObjectName("outputPath")
        self.output_path.setWordWrap(True)
        console_layout.addWidget(self.output_path)
        layout.addWidget(console_card, 1)

        controls = QHBoxLayout()
        self.start_button.setObjectName("primaryButton")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_capture)
        self.stop_button.clicked.connect(self.stop_capture)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addStretch()
        layout.addLayout(controls)

    @staticmethod
    def _metric_card(label_text: str, value_label: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("metricCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(17, 13, 17, 13)

        label = QLabel(label_text)
        label.setObjectName("metricLabel")
        value_label.setObjectName("metricValue")

        card_layout.addWidget(label)
        card_layout.addWidget(value_label)
        return card

    def start_capture(self) -> None:
        if self.is_running:
            return

        username, password = self._credential_provider()
        if not username or not password:
            self._append_log("Enter credentials on the Connection page first")
            self.status_changed.emit("Live capture requires session credentials")
            return

        self.message_value.setText("0")
        self.batch_value.setText("0")
        self.elapsed_value.setText("00:00:00")
        self.output_path.setText("Preparing capture file…")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._set_feed_state("CONNECTING", "connecting")
        self._append_log("Starting movement-feed worker")

        self._thread = QThread(self)
        self._worker = MovementWorker(
            username=username,
            password=password,
            data_directory=self._data_directory_provider(),
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.connected.connect(self._capture_connected)
        self._worker.counters_changed.connect(self._update_counters)
        self._worker.log_message.connect(self._append_log)
        self._worker.failed.connect(self._capture_failed)
        self._worker.finished.connect(self._capture_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def stop_capture(self) -> None:
        if self._worker is None:
            return
        self.stop_button.setEnabled(False)
        self._set_feed_state("STOPPING", "connecting")
        self._append_log("Stop requested; closing the feed cleanly")
        self._worker.request_stop()

    def shutdown(self, timeout_ms: int = 5000) -> None:
        if self._worker is not None:
            self._worker.request_stop()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(timeout_ms)

    def _capture_connected(self, output_path: str) -> None:
        self.output_path.setText(f"Output: {output_path}")
        self._started_at = datetime.now()
        self._timer.start()
        self._set_feed_state("CAPTURING", "running")
        self.status_changed.emit("Live TRAIN_MVT_ALL_TOC capture is running")

    def _capture_failed(self, message: str) -> None:
        self._append_log(f"ERROR — {message}")
        self._set_feed_state("ERROR", "error")
        self.status_changed.emit(message)

    def _capture_finished(self, output_path: str) -> None:
        self._timer.stop()
        if self.feed_state.text() != "ERROR":
            self._set_feed_state("STOPPED", "stopped")
        if output_path:
            self.output_path.setText(f"Saved: {output_path}")
        self._append_log("Capture worker finished")
        self.status_changed.emit("Movement capture stopped")

    def _thread_finished(self) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._worker = None
        self._thread = None

    def _update_counters(self, messages: int, batches: int) -> None:
        self.message_value.setText(f"{messages:,}")
        self.batch_value.setText(f"{batches:,}")

    def _update_elapsed_time(self) -> None:
        if self._started_at is None:
            return
        elapsed_seconds = int((datetime.now() - self._started_at).total_seconds())
        hours, remainder = divmod(elapsed_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.elapsed_value.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"{timestamp}  {message}")

    def _set_feed_state(self, text: str, state: str) -> None:
        self.feed_state.setText(text)
        self.feed_state.setProperty("state", state)
        self.feed_state.style().unpolish(self.feed_state)
        self.feed_state.style().polish(self.feed_state)
