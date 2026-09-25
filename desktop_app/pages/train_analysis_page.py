from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop_app.workers.train_analysis_worker import TrainAnalysisWorker


class TrainAnalysisPage(QWidget):
    status_changed = Signal(str)

    def __init__(self, data_directory_provider: Callable[[], Path]) -> None:
        super().__init__()
        self.setObjectName("page")
        self._data_directory_provider = data_directory_provider
        self._thread: QThread | None = None
        self._worker: TrainAnalysisWorker | None = None

        self.train_selector = QComboBox()
        self.dropdown_button = QPushButton("▼")
        self.search_button = QPushButton("Analyse train")
        self.refresh_button = QPushButton("Refresh candidates")
        self.case_label = QLabel("NO TRAIN SELECTED")
        self.events_value = QLabel("0")
        self.start_value = QLabel("—")
        self.end_value = QLabel("—")
        self.net_value = QLabel("—")
        self.figure = Figure(figsize=(8, 3.4))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(210)
        self.table = QTableWidget()

        self._build_interface()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _build_interface(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 30)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        title_group = QVBoxLayout()
        title = QLabel("Train analysis")
        title.setObjectName("pageTitle")
        description = QLabel(
            "Inspect an individual train's ordered movement events and delay evolution."
        )
        description.setObjectName("pageDescription")
        title_group.addWidget(title)
        title_group.addWidget(description)
        heading.addLayout(title_group)
        heading.addStretch()
        self.case_label.setObjectName("caseLabel")
        heading.addWidget(self.case_label)
        layout.addLayout(heading)

        controls = QFrame()
        controls.setObjectName("raisedCard")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(16, 12, 16, 12)
        selector_label = QLabel("Train ID")
        selector_label.setObjectName("formLabel")
        self.train_selector.setEditable(True)
        self.train_selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.train_selector.setMinimumWidth(250)
        self.train_selector.lineEdit().setPlaceholderText(
            "Choose a candidate or enter an exact train ID"
        )
        self.search_button.setObjectName("primaryButton")
        self.dropdown_button.setObjectName("dropdownButton")
        self.dropdown_button.setFixedWidth(46)
        self.dropdown_button.setToolTip("Show candidate trains")
        self.refresh_button.setObjectName("secondaryButton")
        self.search_button.clicked.connect(self.analyse_selected_train)
        self.dropdown_button.clicked.connect(self.train_selector.showPopup)
        self.refresh_button.clicked.connect(self.refresh_candidates)
        self.train_selector.lineEdit().returnPressed.connect(
            self.analyse_selected_train
        )
        controls_layout.addWidget(selector_label)
        controls_layout.addWidget(self.train_selector, 1)
        controls_layout.addWidget(self.dropdown_button)
        controls_layout.addWidget(self.search_button)
        controls_layout.addWidget(self.refresh_button)
        layout.addWidget(controls)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(10)
        metrics.addWidget(self._metric_card("Events", self.events_value), 0, 0)
        metrics.addWidget(
            self._metric_card("Starting variation", self.start_value), 0, 1
        )
        metrics.addWidget(
            self._metric_card("Ending variation", self.end_value), 0, 2
        )
        metrics.addWidget(self._metric_card("Net change", self.net_value), 0, 3)
        layout.addLayout(metrics)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("analysisSplitter")

        chart_card = QFrame()
        chart_card.setObjectName("analysisChartCard")
        chart_card.setMinimumHeight(255)
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(14, 11, 14, 10)
        chart_title = QLabel("Timetable variation through the journey")
        chart_title.setObjectName("cardTitle")
        chart_layout.addWidget(chart_title)
        chart_layout.addWidget(self.canvas, 1)

        table_card = QFrame()
        table_card.setObjectName("analysisTableCard")
        table_card.setMinimumHeight(205)
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(14, 11, 14, 10)
        table_title = QLabel("Ordered movement events")
        table_title.setObjectName("cardTitle")
        table_layout.addWidget(table_title)
        self._configure_table()
        table_layout.addWidget(self.table, 1)

        splitter.addWidget(chart_card)
        splitter.addWidget(table_card)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 300])
        layout.addWidget(splitter, 1)

        self._draw_empty_chart()

    @staticmethod
    def _metric_card(label_text: str, value_label: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("metricCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 9, 14, 9)
        label = QLabel(label_text)
        label.setObjectName("metricLabel")
        value_label.setObjectName("databaseMetricValue")
        card_layout.addWidget(label)
        card_layout.addWidget(value_label)
        return card

    def _configure_table(self) -> None:
        headers = [
            "Actual time",
            "Location",
            "Event",
            "Variation",
            "Change",
            "Status",
            "Platform",
        ]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def refresh_candidates(self) -> None:
        self._start_worker(train_id=None)

    def analyse_selected_train(self) -> None:
        train_id = self.train_selector.currentText().strip()
        if not train_id:
            self.status_changed.emit("Choose or enter a train ID")
            return
        self._start_worker(train_id=train_id)

    def _start_worker(self, train_id: str | None) -> None:
        if self.is_running:
            return
        self._set_controls_enabled(False)
        message = (
            "Loading candidate trains…"
            if train_id is None
            else f"Loading train {train_id}…"
        )
        self.status_changed.emit(message)

        self._thread = QThread(self)
        self._worker = TrainAnalysisWorker(self._database_path(), train_id)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.candidates_loaded.connect(self._show_candidates)
        self._worker.train_loaded.connect(self._show_train)
        self._worker.failed.connect(self._show_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._worker_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _show_candidates(self, candidates: pd.DataFrame) -> None:
        previous = self.train_selector.currentText()
        self.train_selector.clear()
        for row in candidates.itertuples(index=False):
            self.train_selector.addItem(
                str(row.train_id),
                {"events": int(row.n_events)},
            )
        if previous:
            self.train_selector.setEditText(previous)
        elif self.train_selector.count() > 0:
            self.train_selector.setCurrentIndex(0)
        self.status_changed.emit(
            f"Loaded {len(candidates):,} candidate trains ranked by event count"
        )

    def _show_train(self, events: pd.DataFrame, summary: dict[str, object]) -> None:
        if events.empty:
            self._show_error("No movement events found for that train ID")
            return
        self.events_value.setText(f"{int(summary['n_events']):,}")
        self.start_value.setText(self._minutes(summary["start_variation"]))
        self.end_value.setText(self._minutes(summary["end_variation"]))
        net = float(summary["net_change"])
        self.net_value.setText(f"{net:+.0f} min")
        self.case_label.setText(str(summary["case_type"]))
        self._populate_table(events)
        self._draw_chart(events)
        self.status_changed.emit(
            f"Loaded {len(events):,} events for {self.train_selector.currentText()}"
        )

    def _populate_table(self, events: pd.DataFrame) -> None:
        self.table.setRowCount(len(events))
        for index, row in events.iterrows():
            actual_time = (
                row["actual_time_utc"].strftime("%Y-%m-%d %H:%M:%S")
                if pd.notna(row["actual_time_utc"])
                else ""
            )
            variation = (
                f"{row['timetable_variation']:.0f} min"
                if pd.notna(row["timetable_variation"])
                else "—"
            )
            change = (
                f"{row['variation_change']:+.0f} min"
                if pd.notna(row["variation_change"])
                else "—"
            )
            values = [
                actual_time,
                str(row["location_name"] or ""),
                str(row["event_type"] or ""),
                variation,
                change,
                str(row["variation_status"] or ""),
                str(row["platform"] or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {3, 4}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight)
                self.table.setItem(index, column, item)

    def _draw_chart(self, events: pd.DataFrame) -> None:
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        self._style_axis(axis)
        valid = events.dropna(subset=["timetable_variation"])
        if valid.empty:
            axis.text(
                0.5,
                0.5,
                "No timetable-variation values available",
                ha="center",
                va="center",
                color="#AFC8EF",
                transform=axis.transAxes,
            )
        else:
            axis.plot(
                valid["actual_time_utc"],
                valid["timetable_variation"],
                color="#69E3D5",
                marker="o",
                markersize=5,
                linewidth=2,
                markerfacecolor="#D9F9F5",
            )
            axis.axhline(0, color="#7890B7", linewidth=1, alpha=0.7)
            axis.set_ylabel("Variation (min)", color="#AFC8EF", labelpad=5)
            axis.tick_params(axis="x", rotation=20)
        self.figure.subplots_adjust(left=0.09, right=0.985, bottom=0.29, top=0.94)
        self.canvas.draw_idle()

    def _draw_empty_chart(self) -> None:
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        self._style_axis(axis)
        axis.text(
            0.5,
            0.5,
            "Refresh candidates and select a train",
            ha="center",
            va="center",
            color="#8EA8CE",
            transform=axis.transAxes,
        )
        axis.set_xticks([])
        axis.set_yticks([])
        self.figure.subplots_adjust(left=0.06, right=0.985, bottom=0.10, top=0.94)
        self.canvas.draw_idle()

    def _style_axis(self, axis: object) -> None:
        self.figure.patch.set_facecolor("#17223B")
        axis.set_facecolor("#17223B")
        axis.grid(True, color="#3C5278", alpha=0.45, linewidth=0.7)
        axis.tick_params(colors="#AFC8EF", labelsize=8)
        for spine in axis.spines.values():
            spine.set_color("#55709A")

    def _show_error(self, message: str) -> None:
        self.status_changed.emit(message)

    def _worker_finished(self) -> None:
        self._worker = None
        self._thread = None
        self._set_controls_enabled(True)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.search_button.setEnabled(enabled)
        self.dropdown_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.train_selector.setEnabled(enabled)

    def _database_path(self) -> Path:
        return self._data_directory_provider() / "railway.db"

    @staticmethod
    def _minutes(value: object) -> str:
        return f"{float(value):.0f} min"
