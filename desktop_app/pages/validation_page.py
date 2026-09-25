from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from desktop_app.workers.validation_worker import ValidationWorker


class ValidationPage(QWidget):
    status_changed = Signal(str)

    def __init__(self, data_directory_provider: Callable[[], Path]) -> None:
        super().__init__()
        self.setObjectName("page")
        self._data_directory_provider = data_directory_provider
        self._thread: QThread | None = None
        self._worker: ValidationWorker | None = None
        self.case_input, self.gap_input, self.window_input = QSpinBox(), QSpinBox(), QSpinBox()
        self.run_button = QPushButton("Run validation tests")
        self.load_button = QPushButton("Load existing results")
        self.values = {key: QLabel("0") for key in
                       ("evaluated", "supports", "partial", "not_support", "no_control")}
        self.control_table, self.sensitivity_table, self.negative_table = (
            QTableWidget(), QTableWidget(), QTableWidget()
        )
        self.figure = Figure(figsize=(8, 4))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.log = QPlainTextEdit()
        self._build_interface()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _build_interface(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 28)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Validation laboratory")
        title.setObjectName("pageTitle")
        description = QLabel("Test whether inferred propagation cases remain credible under controls and alternative thresholds.")
        description.setObjectName("pageDescription")
        titles.addWidget(title); titles.addWidget(description)
        heading.addLayout(titles); heading.addStretch()
        caveat = QLabel("ROBUSTNESS EVIDENCE — NOT CAUSAL PROOF")
        caveat.setObjectName("causalCaveat")
        heading.addWidget(caveat)
        layout.addLayout(heading)

        card = QFrame(); card.setObjectName("raisedCard")
        controls = QVBoxLayout(card); controls.setContentsMargins(15, 11, 15, 11)
        fields = QGridLayout(); fields.setHorizontalSpacing(14)
        settings = [("Maximum cases", self.case_input, 1, 500, 50, ""),
                    ("Maximum interaction gap", self.gap_input, 1, 60, 10, " min"),
                    ("Control time window", self.window_input, 5, 180, 30, " min")]
        for column, (text, widget, low, high, value, suffix) in enumerate(settings):
            widget.setRange(low, high); widget.setValue(value); widget.setSuffix(suffix)
            label = QLabel(text); label.setObjectName("parameterLabel")
            fields.addWidget(label, 0, column); fields.addWidget(widget, 1, column)
            fields.setColumnStretch(column, 1)
        controls.addLayout(fields)
        actions = QHBoxLayout(); actions.addStretch()
        self.run_button.setObjectName("primaryButton"); self.load_button.setObjectName("secondaryButton")
        self.run_button.setMinimumWidth(210); self.load_button.setMinimumWidth(205)
        self.run_button.clicked.connect(self.run_validation); self.load_button.clicked.connect(self.load_results)
        actions.addWidget(self.run_button); actions.addWidget(self.load_button)
        controls.addLayout(actions); layout.addWidget(card)

        metrics = QGridLayout(); metrics.setHorizontalSpacing(8)
        labels = [("Cases evaluated", "evaluated"), ("Supports case", "supports"),
                  ("Partial support", "partial"), ("Does not support", "not_support"),
                  ("No control", "no_control")]
        for column, (text, key) in enumerate(labels):
            metrics.addWidget(self._metric_card(text, self.values[key]), 0, column)
        layout.addLayout(metrics)

        tabs = QTabWidget(); tabs.setObjectName("propagationTabs")
        tabs.addTab(self._overview_tab(), "Overview")
        tabs.addTab(self._table_tab(self.control_table), "Matched controls")
        tabs.addTab(self._table_tab(self.sensitivity_table), "Sensitivity grid")
        tabs.addTab(self._table_tab(self.negative_table), "Negative tests")
        tabs.addTab(self._log_tab(), "Validation log")
        layout.addWidget(tabs, 1)
        self._draw_empty()

    @staticmethod
    def _metric_card(text: str, value: QLabel) -> QFrame:
        card = QFrame(); card.setObjectName("metricCard")
        box = QVBoxLayout(card); box.setContentsMargins(12, 7, 12, 7)
        label = QLabel(text); label.setObjectName("metricLabel")
        value.setObjectName("databaseMetricValue")
        box.addWidget(label); box.addWidget(value)
        return card

    def _overview_tab(self) -> QWidget:
        tab = QWidget(); tab.setObjectName("propagationTab")
        box = QVBoxLayout(tab); box.setContentsMargins(8, 6, 8, 6)
        # The overview must be allowed to shrink on common laptop-height screens.
        # A 300 px minimum forced the lower axes outside the visible tab pane.
        self.canvas.setMinimumHeight(170)
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        box.addWidget(self.canvas, 1)
        return tab

    @staticmethod
    def _table_tab(table: QTableWidget) -> QWidget:
        tab = QWidget(); tab.setObjectName("propagationTab")
        box = QVBoxLayout(tab); box.setContentsMargins(8, 8, 8, 8)
        table.setAlternatingRowColors(True); table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False); box.addWidget(table)
        return tab

    def _log_tab(self) -> QWidget:
        tab = QWidget(); tab.setObjectName("propagationTab")
        box = QVBoxLayout(tab); box.setContentsMargins(8, 8, 8, 8)
        self.log.setObjectName("databaseConsole"); self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500); box.addWidget(self.log)
        return tab

    def run_validation(self) -> None:
        answer = QMessageBox.question(self, "Run validation tests?",
            "This evaluates propagation cases and replaces previous validation result tables. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            self._start(True, {"max_cases": self.case_input.value(),
                               "max_gap_minutes": float(self.gap_input.value()),
                               "control_window_minutes": float(self.window_input.value())})

    def load_results(self) -> None:
        self._start(False, None)

    def _start(self, run_pipeline: bool, parameters: dict | None) -> None:
        if self.is_running: return
        self._set_enabled(False)
        message = "Starting validation laboratory" if run_pipeline else "Loading existing validation results"
        self._append_log(message); self.status_changed.emit(message)
        data_dir = self._data_directory_provider()
        self._thread = QThread(self)
        self._worker = ValidationWorker(data_dir / "railway.db", data_dir.parent / "outputs" / "tables",
                                        run_pipeline, parameters)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._append_log)
        self._worker.results_ready.connect(self._show_results)
        self._worker.failed.connect(self._show_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _show_results(self, results: dict[str, object]) -> None:
        for key, value in results["metrics"].items(): self.values[key].setText(f"{int(value):,}")
        controls, sensitivity, negative = results["controls"], results["sensitivity"], results["negative"]
        self._populate(self.control_table, controls, [
            ("Source", "source_train_id"), ("Affected", "affected_train_id"),
            ("Location", "location_name"), ("Gap", "time_gap_minutes"),
            ("Post change", "affected_post_change"), ("Controls", "n_controls"),
            ("Control rate", "control_post_worsening_rate"), ("Result", "control_result")])
        self._populate(self.sensitivity_table, sensitivity, [
            ("Gap", "max_gap_minutes"), ("Delay", "delay_threshold"), ("Pairs", "n_pairs"),
            ("Post worsened", "pct_post_worsened"), ("Stronger than pre", "pct_post_stronger_than_pre"),
            ("Locations", "n_unique_locations"), ("Top locations", "top_locations")])
        self._populate(self.negative_table, negative, [
            ("Test", "mode"), ("Pairs", "n_pairs"), ("Post worsened", "pct_post_worsened"),
            ("Stronger than pre", "pct_post_stronger_than_pre"),
            ("Mean post change", "mean_post_change"), ("Locations", "n_unique_locations")])
        self._draw_overview(results["metrics"], negative)
        self._append_log("Validation results loaded into the laboratory")
        self.status_changed.emit("Validation results ready")

    @staticmethod
    def _populate(table: QTableWidget, frame: pd.DataFrame, columns: list[tuple[str, str]]) -> None:
        table.setColumnCount(len(columns)); table.setHorizontalHeaderLabels([x[0] for x in columns])
        table.setRowCount(len(frame))
        for row_index, (_, row) in enumerate(frame.iterrows()):
            for column_index, (_, key) in enumerate(columns):
                value = row.get(key, "")
                text = "—" if pd.isna(value) else f"{value:.2f}" if isinstance(value, float) else str(value)
                table.setItem(row_index, column_index, QTableWidgetItem(text))
        header = table.horizontalHeader(); header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        if columns: header.setSectionResizeMode(len(columns) - 1, QHeaderView.ResizeMode.Stretch)

    def _style_figure(self) -> None:
        self.figure.patch.set_facecolor("#17223B")

    def _draw_empty(self) -> None:
        self.figure.clear(); self._style_figure(); axis = self.figure.add_subplot(111)
        axis.set_facecolor("#17223B"); axis.text(.5, .5, "Run validation or load existing results",
            ha="center", va="center", color="#8EA8CE", transform=axis.transAxes)
        axis.axis("off"); self.canvas.draw_idle()

    def _draw_overview(self, metrics: dict, negative: pd.DataFrame) -> None:
        self.figure.clear(); self._style_figure()
        left, right = self.figure.subplots(1, 2)
        result_labels = ["Supports", "Partial", "Does not\nsupport", "No control"]
        values = [metrics["supports"], metrics["partial"], metrics["not_support"], metrics["no_control"]]
        left_bars = left.bar(
            result_labels, values,
            color=["#69E3D5", "#78A7FF", "#FF7DBA", "#A9B5C8"],
        )
        left.set_title("Matched-control outcomes", color="#DDEAFF", fontsize=10)
        right_values = negative["pct_post_worsened"] * 100
        right_bars = right.bar(
            negative["mode"].str.replace("_", "\n"), right_values,
            color=["#69E3D5", "#FF7DBA", "#E6BE6A"],
        )
        right.set_title("Post-worsening rate by test", color="#DDEAFF", fontsize=10)
        right.set_ylabel("Percent", color="#B9CBE8")
        left.set_ylim(bottom=0, top=max(values + [1]) * 1.18)
        right.set_ylim(bottom=0, top=max(list(right_values) + [1]) * 1.18)
        left.bar_label(left_bars, padding=3, color="#E8F1FF", fontsize=8)
        right.bar_label(
            right_bars,
            labels=[f"{value:.1f}%" for value in right_values],
            padding=3, color="#E8F1FF", fontsize=8,
        )
        for axis in (left, right):
            axis.set_facecolor("#17223B"); axis.tick_params(colors="#B9CBE8", labelsize=8)
            axis.grid(axis="y", color="#395070", alpha=.45)
            for spine in axis.spines.values():
                spine.set_color("#557098")
        self.figure.subplots_adjust(
            left=.065, right=.985, bottom=.23, top=.82, wspace=.27
        )
        self.canvas.draw_idle()

    def _show_error(self, message: str) -> None:
        self._append_log(f"ERROR — {message}"); self.status_changed.emit(message)

    def _finished(self) -> None:
        self._worker = None; self._thread = None; self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (self.case_input, self.gap_input, self.window_input, self.run_button, self.load_button):
            widget.setEnabled(enabled)

    def _append_log(self, message: str) -> None:
        self.log.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
