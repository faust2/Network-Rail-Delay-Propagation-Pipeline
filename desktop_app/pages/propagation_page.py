from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import networkx as nx
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop_app.workers.propagation_worker import PropagationWorker


class InteractiveNetworkCanvas(FigureCanvasQTAgg):
    """Network canvas with cursor-centred zoom and left-button panning."""

    def __init__(self, figure: Figure) -> None:
        super().__init__(figure)
        self._axis = None
        self._home_limits = None
        self._pan_start = None
        self.mpl_connect("scroll_event", self._on_scroll)
        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("button_release_event", self._on_release)
        self.mpl_connect("motion_notify_event", self._on_motion)

    def set_axis(self, axis: object) -> None:
        self._axis = axis
        self._home_limits = (axis.get_xlim(), axis.get_ylim())

    def zoom(self, factor: float, centre: tuple[float, float] | None = None) -> None:
        if self._axis is None:
            return
        x_limits, y_limits = self._axis.get_xlim(), self._axis.get_ylim()
        x = centre[0] if centre else sum(x_limits) / 2
        y = centre[1] if centre else sum(y_limits) / 2
        self._axis.set_xlim(x - (x - x_limits[0]) * factor,
                            x + (x_limits[1] - x) * factor)
        self._axis.set_ylim(y - (y - y_limits[0]) * factor,
                            y + (y_limits[1] - y) * factor)
        self.draw_idle()

    def reset_view(self) -> None:
        if self._axis is not None and self._home_limits is not None:
            self._axis.set_xlim(*self._home_limits[0])
            self._axis.set_ylim(*self._home_limits[1])
            self.draw_idle()

    def _on_scroll(self, event: object) -> None:
        if event.inaxes is self._axis and event.xdata is not None and event.ydata is not None:
            self.zoom(0.78 if event.button == "up" else 1.28, (event.xdata, event.ydata))

    def _on_press(self, event: object) -> None:
        if event.button == 1 and event.inaxes is self._axis and event.xdata is not None:
            self._pan_start = (event.xdata, event.ydata,
                               self._axis.get_xlim(), self._axis.get_ylim())

    def _on_release(self, _event: object) -> None:
        self._pan_start = None

    def _on_motion(self, event: object) -> None:
        if self._pan_start is None or event.inaxes is not self._axis or event.xdata is None:
            return
        start_x, start_y, x_limits, y_limits = self._pan_start
        dx, dy = event.xdata - start_x, event.ydata - start_y
        self._axis.set_xlim(x_limits[0] - dx, x_limits[1] - dx)
        self._axis.set_ylim(y_limits[0] - dy, y_limits[1] - dy)
        self.draw_idle()


class PropagationPage(QWidget):
    status_changed = Signal(str)

    def __init__(self, data_directory_provider: Callable[[], Path]) -> None:
        super().__init__()
        self.setObjectName("page")
        self._data_directory_provider = data_directory_provider
        self._thread: QThread | None = None
        self._worker: PropagationWorker | None = None

        self.gap_input = QSpinBox()
        self.source_input = QDoubleSpinBox()
        self.affected_input = QDoubleSpinBox()
        self.score_input = QDoubleSpinBox()
        self.direction_input = QCheckBox("Require same direction")
        self.run_button = QPushButton("Run propagation analysis")
        self.load_button = QPushButton("Load existing results")
        self.log = QPlainTextEdit()
        self.pairs_value = QLabel("0")
        self.high_conf_value = QLabel("0")
        self.edges_value = QLabel("0")
        self.nodes_value = QLabel("0")
        self.figure = Figure(figsize=(8, 5))
        self.canvas = InteractiveNetworkCanvas(self.figure)
        self.expand_button = QPushButton("Expand network")
        self._latest_edges = pd.DataFrame()
        self._latest_nodes = pd.DataFrame()
        self._expanded_dialog: QDialog | None = None
        self.edge_table = QTableWidget()
        self.location_table = QTableWidget()

        self._build_interface()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _build_interface(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 28)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        heading_group = QVBoxLayout()
        title = QLabel("Propagation analysis")
        title.setObjectName("pageTitle")
        description = QLabel(
            "Infer candidate train-to-train delay transmission from close-time, "
            "same-location movement sequences."
        )
        description.setObjectName("pageDescription")
        heading_group.addWidget(title)
        heading_group.addWidget(description)
        heading.addLayout(heading_group)
        heading.addStretch()
        caveat = QLabel("INFERRED — NOT PROVEN CAUSATION")
        caveat.setObjectName("causalCaveat")
        heading.addWidget(caveat)
        layout.addLayout(heading)

        controls = QFrame()
        controls.setObjectName("raisedCard")
        control_layout = QVBoxLayout(controls)
        control_layout.setContentsMargins(15, 11, 15, 11)
        control_layout.setSpacing(10)

        parameter_layout = QGridLayout()
        parameter_layout.setHorizontalSpacing(14)

        self.gap_input.setRange(1, 60)
        self.gap_input.setValue(10)
        self.gap_input.setSuffix(" min")
        self.source_input.setRange(0, 120)
        self.source_input.setValue(5)
        self.source_input.setSuffix(" min")
        self.affected_input.setRange(0, 120)
        self.affected_input.setValue(5)
        self.affected_input.setSuffix(" min")
        self.score_input.setRange(0, 100)
        self.score_input.setValue(8)
        self.score_input.setSingleStep(0.5)
        self.direction_input.setChecked(True)

        fields = [
            ("Maximum gap", self.gap_input),
            ("Source delay", self.source_input),
            ("Affected delay", self.affected_input),
            ("Causal score", self.score_input),
        ]
        for column, (label_text, widget) in enumerate(fields):
            label = QLabel(label_text)
            label.setObjectName("parameterLabel")
            parameter_layout.addWidget(label, 0, column)
            parameter_layout.addWidget(widget, 1, column)
            parameter_layout.setColumnStretch(column, 1)

        control_layout.addLayout(parameter_layout)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(12)
        action_layout.addWidget(self.direction_input)
        action_layout.addStretch()
        self.run_button.setObjectName("primaryButton")
        self.load_button.setObjectName("secondaryButton")
        self.run_button.setMinimumWidth(225)
        self.load_button.setMinimumWidth(200)
        self.run_button.clicked.connect(self.run_analysis)
        self.load_button.clicked.connect(self.load_results)
        action_layout.addWidget(self.run_button)
        action_layout.addWidget(self.load_button)
        control_layout.addLayout(action_layout)
        layout.addWidget(controls)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(10)
        metrics.addWidget(self._metric_card("Evaluated pairs", self.pairs_value), 0, 0)
        metrics.addWidget(
            self._metric_card("High-confidence pairs", self.high_conf_value), 0, 1
        )
        metrics.addWidget(self._metric_card("Directed edges", self.edges_value), 0, 2)
        metrics.addWidget(self._metric_card("Network trains", self.nodes_value), 0, 3)
        layout.addLayout(metrics)

        tabs = QTabWidget()
        tabs.setObjectName("propagationTabs")
        tabs.addTab(self._network_tab(), "Network")
        tabs.addTab(self._table_tab(self.edge_table, "edges"), "Strongest edges")
        tabs.addTab(
            self._table_tab(self.location_table, "locations"), "Important locations"
        )
        tabs.addTab(self._log_tab(), "Pipeline log")
        layout.addWidget(tabs, 1)
        self._draw_empty_network()

    @staticmethod
    def _metric_card(label_text: str, value_label: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("metricCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(13, 8, 13, 8)
        label = QLabel(label_text)
        label.setObjectName("metricLabel")
        value_label.setObjectName("databaseMetricValue")
        card_layout.addWidget(label)
        card_layout.addWidget(value_label)
        return card

    def _network_tab(self) -> QWidget:
        tab = QWidget()
        tab.setObjectName("propagationTab")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        tools = QHBoxLayout()
        hint = QLabel("Mouse wheel: zoom  ·  Hold left mouse button: move network")
        hint.setObjectName("networkHint")
        zoom_in = QPushButton("Zoom in +")
        zoom_out = QPushButton("Zoom out −")
        reset = QPushButton("Reset view")
        for button in (zoom_in, zoom_out, reset):
            button.setObjectName("networkToolButton")
            button.setMinimumWidth(100)
        self.expand_button.setObjectName("primaryButton")
        self.expand_button.setMinimumWidth(180)
        self.expand_button.setEnabled(False)
        zoom_in.clicked.connect(lambda: self.canvas.zoom(0.78))
        zoom_out.clicked.connect(lambda: self.canvas.zoom(1.28))
        reset.clicked.connect(self.canvas.reset_view)
        self.expand_button.clicked.connect(self._open_expanded_network)
        tools.addWidget(hint)
        tools.addStretch()
        tools.addWidget(zoom_in)
        tools.addWidget(zoom_out)
        tools.addWidget(reset)
        tools.addWidget(self.expand_button)
        layout.addLayout(tools)
        self.canvas.setMinimumHeight(315)
        layout.addWidget(self.canvas)
        return tab

    def _table_tab(self, table: QTableWidget, kind: str) -> QWidget:
        tab = QWidget()
        tab.setObjectName("propagationTab")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setProperty("tableKind", kind)
        layout.addWidget(table)
        return tab

    def _log_tab(self) -> QWidget:
        tab = QWidget()
        tab.setObjectName("propagationTab")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        self.log.setObjectName("databaseConsole")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        layout.addWidget(self.log)
        return tab

    def run_analysis(self) -> None:
        if self.is_running:
            return
        answer = QMessageBox.question(
            self,
            "Run propagation analysis?",
            "This rebuilds the derived enrichment and causal-propagation tables. "
            "The source timetable and movement tables are not changed. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        parameters = {
            "max_gap_minutes": float(self.gap_input.value()),
            "source_delay_threshold": float(self.source_input.value()),
            "affected_delay_threshold": float(self.affected_input.value()),
            "causal_score_threshold": float(self.score_input.value()),
            "require_same_direction": self.direction_input.isChecked(),
        }
        self._start_worker(True, parameters)

    def load_results(self) -> None:
        self._start_worker(False, None)

    def _start_worker(
        self, run_pipeline: bool, parameters: dict[str, object] | None
    ) -> None:
        if self.is_running:
            return
        self._set_controls_enabled(False)
        message = (
            "Starting propagation pipeline"
            if run_pipeline
            else "Loading existing propagation results"
        )
        self._append_log(message)
        self.status_changed.emit(message)

        self._thread = QThread(self)
        self._worker = PropagationWorker(
            self._database_path(), run_pipeline, parameters
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._append_log)
        self._worker.results_ready.connect(self._show_results)
        self._worker.failed.connect(self._show_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._worker_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _show_results(self, results: dict[str, object]) -> None:
        metrics = results["metrics"]
        self.pairs_value.setText(f"{int(metrics['pairs']):,}")
        self.high_conf_value.setText(
            f"{int(metrics['high_confidence_pairs']):,}"
        )
        self.edges_value.setText(f"{int(metrics['edges']):,}")
        self.nodes_value.setText(f"{int(metrics['nodes']):,}")
        self._populate_edge_table(results["edges"])
        self._populate_location_table(results["locations"])
        self._latest_edges = results["edges"].copy()
        self._latest_nodes = results["nodes"].copy()
        self.expand_button.setEnabled(not self._latest_edges.empty)
        self._draw_network(results["edges"], results["nodes"])
        self._append_log("Propagation results loaded into the explorer")
        self.status_changed.emit("Propagation analysis results ready")

    def _populate_edge_table(self, edges: pd.DataFrame) -> None:
        columns = [
            ("Source", "source_train_id"),
            ("Affected", "affected_train_id"),
            ("Location", "sample_location"),
            ("Events", "n_causal_events"),
            ("Mean gap", "mean_time_gap"),
            ("Post-worsening", "mean_post_worsening"),
            ("Weight", "edge_weight"),
        ]
        self._populate_table(self.edge_table, edges.head(200), columns)

    def _populate_location_table(self, locations: pd.DataFrame) -> None:
        columns = [
            ("Location", "location_name"),
            ("STANOX", "loc_stanox"),
            ("Pairs", "n_causal_pairs"),
            ("Sources", "n_sources"),
            ("Affected", "n_affected"),
            ("Mean worsening", "mean_post_worsening"),
            ("Score", "location_causal_score"),
        ]
        self._populate_table(self.location_table, locations.head(200), columns)

    @staticmethod
    def _populate_table(
        table: QTableWidget,
        frame: pd.DataFrame,
        columns: list[tuple[str, str]],
    ) -> None:
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels([title for title, _ in columns])
        table.setRowCount(len(frame))
        for row_index, (_, row) in enumerate(frame.iterrows()):
            for column_index, (_, key) in enumerate(columns):
                value = row.get(key, "")
                if isinstance(value, float):
                    text = f"{value:.2f}"
                else:
                    text = str(value)
                table.setItem(row_index, column_index, QTableWidgetItem(text))
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        if columns:
            header.setSectionResizeMode(2 if len(columns) > 2 else 0, QHeaderView.ResizeMode.Stretch)

    def _draw_network(self, edges: pd.DataFrame, nodes: pd.DataFrame) -> None:
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        self._style_network_axis(axis)
        if edges.empty:
            axis.text(
                0.5, 0.5, "No high-confidence edges met the current thresholds",
                ha="center", va="center", color="#AFC8EF", transform=axis.transAxes,
            )
            axis.axis("off")
            self.canvas.draw_idle()
            return

        selected = self._connected_top_edges(edges, 15)
        graph = nx.DiGraph()
        node_details = nodes.set_index("train_id").to_dict("index") if not nodes.empty else {}
        for _, edge in selected.iterrows():
            source = str(edge["source_train_id"])
            affected = str(edge["affected_train_id"])
            graph.add_node(source, **node_details.get(source, {}))
            graph.add_node(affected, **node_details.get(affected, {}))
            graph.add_edge(source, affected, weight=float(edge["edge_weight"]))

        positions = nx.spring_layout(graph, seed=42, k=1.35)
        roles = nx.get_node_attributes(graph, "node_role")
        role_colours = {
            "SOURCE_ONLY": "#FF7DBA",
            "INTERMEDIATE": "#78A7FF",
            "SINK_ONLY": "#69E3D5",
        }
        colours = [role_colours.get(roles.get(node, ""), "#B9C7DD") for node in graph]
        totals = [float(graph.nodes[node].get("total_weight", 1)) for node in graph]
        sizes = [max(1250, 1050 + 55 * max(weight, 0) ** 0.5) for weight in totals]
        weights = [float(graph[u][v]["weight"]) for u, v in graph.edges]
        max_weight = max(weights) if weights else 1
        widths = [1.2 + 4 * weight / max_weight for weight in weights]

        nx.draw_networkx_nodes(
            graph, positions, node_color=colours, node_size=sizes,
            edgecolors="#E6F0FF", linewidths=1.0, ax=axis,
        )
        nx.draw_networkx_edges(
            graph, positions, width=widths, edge_color="#77A7E8",
            arrows=True, arrowsize=17, alpha=0.82,
            connectionstyle="arc3,rad=0.08", ax=axis,
        )
        nx.draw_networkx_labels(
            graph, positions, font_size=6.2, font_family="monospace",
            font_color="#F4F8FF", horizontalalignment="center",
            verticalalignment="center", ax=axis,
        )
        axis.set_title(
            f"Connected high-weight subnetwork · {graph.number_of_nodes()} trains / "
            f"{graph.number_of_edges()} edges",
            color="#DDEAFF", fontsize=10, pad=8,
        )
        axis.axis("off")
        axis.margins(0.18)
        self.figure.subplots_adjust(left=0.02, right=0.98, bottom=0.07, top=0.90)
        self.canvas.set_axis(axis)
        self.canvas.draw_idle()

    def _open_expanded_network(self) -> None:
        if self._latest_edges.empty:
            return
        if self._expanded_dialog is not None and self._expanded_dialog.isVisible():
            self._expanded_dialog.raise_()
            self._expanded_dialog.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("RailConnect 2000 — Expanded propagation network")
        dialog.setMinimumSize(1000, 700)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        tools = QHBoxLayout()
        selected_edges = self._connected_top_edges(self._latest_edges, 15)
        selected_trains = set(selected_edges["source_train_id"].astype(str)) | set(
            selected_edges["affected_train_id"].astype(str)
        )
        title = QLabel(
            f"Expanded high-weight subnetwork · {len(selected_trains):,} trains / "
            f"{len(selected_edges):,} directed edges"
        )
        title.setObjectName("expandedNetworkTitle")
        hint = QLabel("Scroll to zoom · Hold and drag to traverse")
        hint.setObjectName("networkHint")
        tools.addWidget(title)
        tools.addStretch()
        tools.addWidget(hint)

        figure = Figure(figsize=(14, 8))
        canvas = InteractiveNetworkCanvas(figure)
        for text, factor in (("Zoom in +", 0.78), ("Zoom out −", 1.28)):
            button = QPushButton(text)
            button.setObjectName("networkToolButton")
            button.clicked.connect(
                lambda _checked=False, value=factor: canvas.zoom(value)
            )
            tools.addWidget(button)
        reset = QPushButton("Reset view")
        reset.setObjectName("networkToolButton")
        reset.clicked.connect(canvas.reset_view)
        tools.addWidget(reset)
        layout.addLayout(tools)
        layout.addWidget(self._expanded_network_legend())
        layout.addWidget(canvas, 1)
        loading_axis = figure.add_subplot(111)
        figure.patch.set_facecolor("#17223B")
        loading_axis.set_facecolor("#17223B")
        loading_axis.text(
            0.5, 0.5, "Preparing expanded network…",
            ha="center", va="center", color="#AFC8EF",
            fontsize=14, transform=loading_axis.transAxes,
        )
        loading_axis.axis("off")
        canvas.draw_idle()
        self._expanded_dialog = dialog
        dialog.finished.connect(
            lambda: setattr(self, "_expanded_dialog", None)
        )
        dialog.showMaximized()
        QTimer.singleShot(
            50,
            lambda: self._render_expanded_safely(dialog, figure, canvas),
        )

    @staticmethod
    def _expanded_network_legend() -> QFrame:
        legend = QFrame()
        legend.setObjectName("networkLegend")
        row = QHBoxLayout(legend)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(18)

        heading = QLabel("KEY")
        heading.setObjectName("networkLegendHeading")
        row.addWidget(heading)

        entries = (
            ("●", "#FF7DBA", "Source-only train"),
            ("●", "#78A7FF", "Intermediate train"),
            ("●", "#69E3D5", "Affected / sink-only train"),
            ("→", "#77A7E8", "Source to affected"),
            ("━━▶", "#77A7E8", "Thicker arrow = greater edge weight"),
        )
        for symbol, colour, description in entries:
            item = QWidget()
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setSpacing(5)
            marker = QLabel(symbol)
            marker.setStyleSheet(
                f"color: {colour}; font-size: 16px; font-weight: 700;"
            )
            text = QLabel(description)
            text.setObjectName("networkLegendText")
            item_layout.addWidget(marker)
            item_layout.addWidget(text)
            row.addWidget(item)
        row.addStretch()
        return legend

    def _render_expanded_safely(
        self,
        dialog: QDialog,
        figure: Figure,
        canvas: InteractiveNetworkCanvas,
    ) -> None:
        if not dialog.isVisible():
            return
        try:
            self._draw_expanded_network(figure, canvas)
        except Exception as error:
            self._append_log(f"ERROR — expanded network: {error}")
            QMessageBox.critical(
                dialog,
                "Could not display expanded network",
                "The expanded network could not be rendered.\n\n"
                f"Technical detail: {error}",
            )

    def _draw_expanded_network(
        self, figure: Figure, canvas: InteractiveNetworkCanvas
    ) -> None:
        figure.clear()
        figure.patch.set_facecolor("#17223B")
        axis = figure.add_subplot(111)
        axis.set_facecolor("#17223B")
        selected_edges = self._connected_top_edges(self._latest_edges, 15)
        graph = self._graph_from_frames(selected_edges, self._latest_nodes)
        positions = nx.spring_layout(graph, seed=42, k=1.35)
        roles = nx.get_node_attributes(graph, "node_role")
        role_colours = {
            "SOURCE_ONLY": "#FF7DBA",
            "INTERMEDIATE": "#78A7FF",
            "SINK_ONLY": "#69E3D5",
        }
        colours = [
            role_colours.get(roles.get(node, ""), "#B9C7DD") for node in graph
        ]
        weights = [float(graph[u][v]["weight"]) for u, v in graph.edges]
        maximum = max(weights) if weights else 1
        totals = [float(graph.nodes[node].get("total_weight", 1)) for node in graph]
        sizes = [max(1500, 1250 + 60 * max(weight, 0) ** 0.5) for weight in totals]
        nx.draw_networkx_nodes(
            graph, positions, node_color=colours, node_size=sizes,
            edgecolors="#E6F0FF", linewidths=1.0, ax=axis,
        )
        nx.draw_networkx_edges(
            graph, positions,
            width=[1.2 + 4 * value / maximum for value in weights],
            edge_color="#77A7E8", arrows=True, arrowsize=17, alpha=0.82,
            connectionstyle="arc3,rad=0.08", ax=axis,
        )
        nx.draw_networkx_labels(
            graph, positions, font_size=6.5, font_family="monospace",
            font_color="#F4F8FF", horizontalalignment="center",
            verticalalignment="center", ax=axis,
        )
        axis.set_title(
            "Connected high-weight propagation subnetwork",
            color="#DDEAFF", fontsize=12, pad=10,
        )
        axis.axis("off")
        axis.margins(0.16)
        figure.subplots_adjust(left=0.015, right=0.985, bottom=0.025, top=0.94)
        canvas.set_axis(axis)
        canvas.draw_idle()

    @staticmethod
    def _graph_from_frames(
        edges: pd.DataFrame, nodes: pd.DataFrame
    ) -> nx.DiGraph:
        graph = nx.DiGraph()
        details = (
            nodes.set_index("train_id").to_dict("index") if not nodes.empty else {}
        )
        for _, edge in edges.iterrows():
            source = str(edge["source_train_id"])
            affected = str(edge["affected_train_id"])
            graph.add_node(source, **details.get(source, {}))
            graph.add_node(affected, **details.get(affected, {}))
            graph.add_edge(source, affected, weight=float(edge["edge_weight"]))
        return graph

    @staticmethod
    def _connected_top_edges(edges: pd.DataFrame, target: int) -> pd.DataFrame:
        ordered = edges.sort_values("edge_weight", ascending=False).reset_index(drop=True)
        if ordered.empty:
            return ordered
        selected = [0]
        nodes = {ordered.loc[0, "source_train_id"], ordered.loc[0, "affected_train_id"]}
        remaining = set(range(1, len(ordered)))
        while len(selected) < target and remaining:
            touching = [
                index for index in remaining
                if ordered.loc[index, "source_train_id"] in nodes
                or ordered.loc[index, "affected_train_id"] in nodes
            ]
            if not touching:
                break
            best = max(touching, key=lambda index: ordered.loc[index, "edge_weight"])
            selected.append(best)
            nodes.add(ordered.loc[best, "source_train_id"])
            nodes.add(ordered.loc[best, "affected_train_id"])
            remaining.remove(best)
        return ordered.loc[selected].copy()

    def _draw_empty_network(self) -> None:
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        self._style_network_axis(axis)
        axis.text(
            0.5, 0.5, "Run the pipeline or load existing results",
            ha="center", va="center", color="#8EA8CE", transform=axis.transAxes,
        )
        axis.axis("off")
        self.canvas.set_axis(axis)
        self.canvas.draw_idle()

    def _style_network_axis(self, axis: object) -> None:
        self.figure.patch.set_facecolor("#17223B")
        axis.set_facecolor("#17223B")

    def _show_error(self, message: str) -> None:
        self._append_log(f"ERROR — {message}")
        self.status_changed.emit(message)

    def _worker_finished(self) -> None:
        self._worker = None
        self._thread = None
        self._set_controls_enabled(True)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.gap_input, self.source_input, self.affected_input, self.score_input,
            self.direction_input, self.run_button, self.load_button,
        ):
            widget.setEnabled(enabled)

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"{timestamp}  {message}")

    def _database_path(self) -> Path:
        return self._data_directory_provider() / "railway.db"
