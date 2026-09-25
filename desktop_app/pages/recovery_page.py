from __future__ import annotations
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)
from desktop_app.workers.recovery_worker import RecoveryWorker


class RecoveryPage(QWidget):
    status_changed = Signal(str)
    def __init__(self, data_directory_provider: Callable[[], Path]) -> None:
        super().__init__(); self.setObjectName("page"); self.provider = data_directory_provider
        self._thread = None; self._worker = None
        self.budget_input, self.effect_input = QDoubleSpinBox(), QDoubleSpinBox()
        self.max_input, self.limit_input = QSpinBox(), QSpinBox()
        self.run_button, self.load_button = QPushButton("Optimise recovery plan"), QPushButton("Load existing plan")
        self.values = {key: QLabel("0") for key in ("candidates", "selected", "used_budget", "benefit")}
        self.solution_table, self.train_table, self.location_table = QTableWidget(), QTableWidget(), QTableWidget()
        self.figure = Figure(figsize=(8, 4)); self.canvas = FigureCanvasQTAgg(self.figure); self.log = QPlainTextEdit()
        self._build()

    @property
    def is_running(self): return self._thread is not None and self._thread.isRunning()

    def _build(self):
        outer = QVBoxLayout(self); outer.setContentsMargins(30, 25, 30, 28); outer.setSpacing(10)
        head = QHBoxLayout(); titles = QVBoxLayout(); title = QLabel("Recovery optimisation"); title.setObjectName("pageTitle")
        desc = QLabel("Select train and location interventions under a constrained heuristic budget."); desc.setObjectName("pageDescription")
        titles.addWidget(title); titles.addWidget(desc); head.addLayout(titles); head.addStretch()
        warning = QLabel("PROTOTYPE DECISION SUPPORT"); warning.setObjectName("causalCaveat"); head.addWidget(warning); outer.addLayout(head)
        card = QFrame(); card.setObjectName("raisedCard"); box = QVBoxLayout(card); box.setContentsMargins(15,11,15,11)
        grid = QGridLayout(); settings = [("Total budget",self.budget_input,1,100,8," units"),
            ("Maximum interventions",self.max_input,1,50,5,""),("Assumed effectiveness",self.effect_input,.05,1,.5,""),
            ("Candidates per type",self.limit_input,5,500,100,"")]
        for col,(text,w,low,high,value,suffix) in enumerate(settings):
            w.setRange(low,high); w.setValue(value); w.setSuffix(suffix)
            if isinstance(w,QDoubleSpinBox): w.setSingleStep(.05); w.setDecimals(2)
            label=QLabel(text); label.setObjectName("parameterLabel"); grid.addWidget(label,0,col); grid.addWidget(w,1,col); grid.setColumnStretch(col,1)
        box.addLayout(grid); actions=QHBoxLayout(); actions.addStretch()
        self.run_button.setObjectName("primaryButton"); self.load_button.setObjectName("secondaryButton")
        self.run_button.setMinimumWidth(220); self.load_button.setMinimumWidth(190)
        self.run_button.clicked.connect(self.run); self.load_button.clicked.connect(lambda:self._start(False,None))
        actions.addWidget(self.run_button); actions.addWidget(self.load_button); box.addLayout(actions); outer.addWidget(card)
        metrics=QGridLayout(); labels=[("Candidates","candidates"),("Selected","selected"),("Budget used","used_budget"),("Estimated avoided impact","benefit")]
        for col,(text,key) in enumerate(labels): metrics.addWidget(self._metric(text,self.values[key]),0,col)
        outer.addLayout(metrics); tabs=QTabWidget(); tabs.setObjectName("propagationTabs")
        tabs.addTab(self._chart_tab(),"Selected plan"); tabs.addTab(self._table_tab(self.solution_table),"All candidates")
        tabs.addTab(self._table_tab(self.train_table),"Train priorities"); tabs.addTab(self._table_tab(self.location_table),"Location priorities")
        tabs.addTab(self._log_tab(),"Optimisation log"); outer.addWidget(tabs,1); self._draw_empty()

    @staticmethod
    def _metric(text,value):
        card=QFrame(); card.setObjectName("metricCard"); box=QVBoxLayout(card); box.setContentsMargins(12,7,12,7)
        label=QLabel(text); label.setObjectName("metricLabel"); value.setObjectName("databaseMetricValue"); box.addWidget(label); box.addWidget(value); return card
    def _chart_tab(self):
        tab=QWidget(); tab.setObjectName("propagationTab")
        box=QVBoxLayout(tab); box.setContentsMargins(8,6,8,6)
        self.canvas.setMinimumHeight(170)
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        box.addWidget(self.canvas,1)
        return tab
    @staticmethod
    def _table_tab(table):
        tab=QWidget(); tab.setObjectName("propagationTab"); box=QVBoxLayout(tab); box.setContentsMargins(8,8,8,8)
        table.setAlternatingRowColors(True); table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); table.verticalHeader().setVisible(False); box.addWidget(table); return tab
    def _log_tab(self):
        tab=QWidget(); tab.setObjectName("propagationTab"); box=QVBoxLayout(tab); box.setContentsMargins(8,8,8,8)
        self.log.setObjectName("databaseConsole"); self.log.setReadOnly(True); box.addWidget(self.log); return tab
    def run(self):
        answer=QMessageBox.question(self,"Optimise recovery plan?","This replaces previous recovery result tables. Continue?",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Yes)
        if answer==QMessageBox.StandardButton.Yes: self._start(True,{"total_budget":self.budget_input.value(),
            "max_interventions":self.max_input.value(),"assumed_effectiveness":self.effect_input.value(),"candidate_limit":self.limit_input.value()})
    def _start(self, run_pipeline, parameters):
        if self.is_running:return
        self._enable(False); data=self.provider(); message="Starting exact recovery optimisation" if run_pipeline else "Loading existing recovery plan"
        self._append(message); self.status_changed.emit(message); self._thread=QThread(self)
        self._worker=RecoveryWorker(data/"railway.db",data.parent/"outputs"/"tables",run_pipeline,parameters); self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run); self._worker.progress.connect(self._append); self._worker.results_ready.connect(self._show)
        self._worker.failed.connect(self._error); self._worker.finished.connect(self._thread.quit); self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._finished); self._thread.finished.connect(self._thread.deleteLater); self._thread.start()
    def _show(self,results):
        for key,value in results["metrics"].items(): self.values[key].setText(f"{value:,.2f}" if isinstance(value,float) else f"{value:,}")
        self._populate(self.solution_table,results["solution"],[("Selected","selected_by_bip"),("Type","intervention_type"),("Target","target"),("Benefit","benefit"),("Cost","intervention_cost"),("Affected","affected_count")])
        self._populate(self.train_table,results["trains"],[("Rank","rank"),("Train","source_train_id"),("Selected","selected_for_intervention"),("Affected","n_affected_trains"),("Locations","n_locations"),("Avoided impact","estimated_avoided_downstream_impact")])
        self._populate(self.location_table,results["locations"],[("Rank","rank"),("Location","location_name"),("Selected","selected_for_intervention"),("Sources","n_source_trains"),("Affected","n_affected_trains"),("Avoided impact","estimated_avoided_downstream_impact")])
        self._draw(results["solution"]); self._append("Recovery plan loaded"); self.status_changed.emit("Recovery optimisation results ready")
    @staticmethod
    def _populate(table,frame,columns):
        table.setColumnCount(len(columns)); table.setHorizontalHeaderLabels([x[0] for x in columns]); table.setRowCount(len(frame))
        for i,(_,row) in enumerate(frame.iterrows()):
            for j,(_,key) in enumerate(columns):
                value=row.get(key,""); text="—" if pd.isna(value) else f"{value:.2f}" if isinstance(value,float) else str(value); table.setItem(i,j,QTableWidgetItem(text))
        header=table.horizontalHeader(); header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
    def _draw_empty(self):
        self.figure.clear(); self.figure.patch.set_facecolor("#17223B"); ax=self.figure.add_subplot(111); ax.set_facecolor("#17223B")
        ax.text(.5,.5,"Run optimisation or load an existing plan",ha="center",va="center",color="#8EA8CE",transform=ax.transAxes); ax.axis("off"); self.canvas.draw_idle()
    def _draw(self,solution):
        selected=solution[solution.selected_by_bip==1].sort_values("benefit"); self.figure.clear(); self.figure.patch.set_facecolor("#17223B"); ax=self.figure.add_subplot(111); ax.set_facecolor("#17223B")
        if selected.empty: ax.text(.5,.5,"No feasible interventions selected",ha="center",va="center",color="#AFC8EF",transform=ax.transAxes); ax.axis("off")
        else:
            colors=["#78A7FF" if x=="train" else "#69E3D5" for x in selected.intervention_type]
            bars=ax.barh(selected.target.astype(str),selected.benefit,color=colors)
            maximum=max(float(selected.benefit.max()),1.0)
            ax.set_xlim(0,maximum*1.16)
            ax.bar_label(
                bars,
                labels=[f"{value:.2f}" for value in selected.benefit],
                padding=5,color="#E8F1FF",fontsize=8,
            )
            ax.set_xlabel("Estimated avoided downstream impact",color="#B9CBE8"); ax.tick_params(colors="#B9CBE8",labelsize=8); ax.grid(axis="x",color="#395070",alpha=.45)
            for spine in ax.spines.values():spine.set_color("#557098")
        self.figure.subplots_adjust(left=.20,right=.985,bottom=.22,top=.94)
        self.canvas.draw_idle()
    def _error(self,message):self._append(f"ERROR — {message}");self.status_changed.emit(message)
    def _finished(self):self._worker=None;self._thread=None;self._enable(True)
    def _enable(self,enabled):
        for w in (self.budget_input,self.max_input,self.effect_input,self.limit_input,self.run_button,self.load_button):w.setEnabled(enabled)
    def _append(self,message):self.log.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
