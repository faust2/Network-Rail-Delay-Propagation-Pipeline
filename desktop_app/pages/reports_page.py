from __future__ import annotations
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QStandardPaths, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFrame,QGridLayout,QHBoxLayout,QLabel,QMessageBox,QPlainTextEdit,QPushButton,
    QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget)
from desktop_app.workers.report_worker import ReportWorker
from railway_delay.report_service import export_report_archive


class ReportsPage(QWidget):
    status_changed=Signal(str)
    def __init__(self,data_directory_provider:Callable[[],Path])->None:
        super().__init__();self.setObjectName("page");self.provider=data_directory_provider;self._thread=None;self._worker=None;self.last_report=None
        self.refresh_button=QPushButton("Refresh report inventory");self.generate_button=QPushButton("Generate HTML report");self.open_button=QPushButton("Open latest report");self.download_button=QPushButton("Export to Downloads")
        self.values={key:QLabel("0") for key in ("movement_events","trains","edges","validated","selected")}
        self.inventory=QTableWidget();self.log=QPlainTextEdit();self._build()
    @property
    def is_running(self):return self._thread is not None and self._thread.isRunning()
    def _build(self):
        outer=QVBoxLayout(self);outer.setContentsMargins(30,25,30,28);outer.setSpacing(12)
        title=QLabel("Reports and exports");title.setObjectName("pageTitle");desc=QLabel("Create a portable analysis report and CSV evidence pack from the current workspace.");desc.setObjectName("pageDescription")
        outer.addWidget(title);outer.addWidget(desc)
        actions=QFrame();actions.setObjectName("raisedCard");box=QVBoxLayout(actions);box.setContentsMargins(15,12,15,12);box.setSpacing(10)
        note=QLabel("Reports are generated locally. Credentials are never included.");note.setObjectName("privacyNote");box.addWidget(note)
        action_row=QGridLayout();action_row.setHorizontalSpacing(10);action_row.setVerticalSpacing(8)
        self.generate_button.setObjectName("primaryButton");self.refresh_button.setObjectName("secondaryButton");self.open_button.setObjectName("secondaryButton");self.download_button.setObjectName("secondaryButton")
        for button,width in ((self.refresh_button,205),(self.generate_button,205),(self.open_button,180),(self.download_button,190)):button.setMinimumWidth(width)
        self.refresh_button.clicked.connect(lambda:self._start(False));self.generate_button.clicked.connect(lambda:self._start(True));self.open_button.clicked.connect(self._open);self.download_button.clicked.connect(self._export_to_downloads)
        self.open_button.setEnabled(False);self.download_button.setEnabled(False)
        action_row.addWidget(self.refresh_button,0,0);action_row.addWidget(self.generate_button,0,1);action_row.addWidget(self.open_button,1,0);action_row.addWidget(self.download_button,1,1);action_row.setColumnStretch(0,1);action_row.setColumnStretch(1,1);box.addLayout(action_row);outer.addWidget(actions)
        metrics=QGridLayout();labels=[("Movement events","movement_events"),("Trains","trains"),("Propagation edges","edges"),("Validated cases","validated"),("Selected actions","selected")]
        for col,(text,key) in enumerate(labels):metrics.addWidget(self._metric(text,self.values[key]),0,col)
        outer.addLayout(metrics)
        content=QHBoxLayout();inventory_card=QFrame();inventory_card.setObjectName("analysisTableCard");left=QVBoxLayout(inventory_card);heading=QLabel("Report contents");heading.setObjectName("sectionTitle");left.addWidget(heading)
        self.inventory.setColumnCount(2);self.inventory.setHorizontalHeaderLabels(["Analysis section","Status"]);self.inventory.verticalHeader().setVisible(False);self.inventory.horizontalHeader().setStretchLastSection(True);left.addWidget(self.inventory)
        log_card=QFrame();log_card.setObjectName("analysisTableCard");right=QVBoxLayout(log_card);log_title=QLabel("Export log");log_title.setObjectName("sectionTitle");right.addWidget(log_title)
        self.log.setObjectName("databaseConsole");self.log.setReadOnly(True);right.addWidget(self.log);content.addWidget(inventory_card,3);content.addWidget(log_card,2);outer.addLayout(content,1)
        self._start(False)
    @staticmethod
    def _metric(text,value):
        card=QFrame();card.setObjectName("metricCard");box=QVBoxLayout(card);box.setContentsMargins(12,7,12,7);label=QLabel(text);label.setObjectName("metricLabel");value.setObjectName("databaseMetricValue");box.addWidget(label);box.addWidget(value);return card
    def _start(self,generate):
        if self.is_running:return
        self._enable(False);data=self.provider();message="Generating report package" if generate else "Inspecting report sources";self._append(message);self.status_changed.emit(message)
        self._thread=QThread(self);self._worker=ReportWorker(data/"railway.db",data.parent/"outputs"/"reports",generate);self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run);self._worker.progress.connect(self._append);self._worker.inventory_ready.connect(self._inventory)
        self._worker.report_ready.connect(self._ready);self._worker.failed.connect(self._error);self._worker.finished.connect(self._thread.quit);self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._finished);self._thread.finished.connect(self._thread.deleteLater);self._thread.start()
    def _inventory(self,result):
        metrics=result.get("metrics",{});
        for key,label in self.values.items():label.setText(f"{int(metrics.get(key,0)):,}")
        rows=[(name,"READY") for name in result["available"]]+[(name,"NOT YET AVAILABLE") for name in result["missing"]]
        self.inventory.setRowCount(len(rows))
        for i,(name,status) in enumerate(rows):self.inventory.setItem(i,0,QTableWidgetItem(name));self.inventory.setItem(i,1,QTableWidgetItem(status))
        self.inventory.resizeColumnToContents(0);self.status_changed.emit("Report inventory ready")
    def _ready(self,path):
        self.last_report=Path(path);self.open_button.setEnabled(True);self.download_button.setEnabled(True);self._append(f"Report ready: {path}");self.status_changed.emit("HTML report and CSV evidence pack generated")
    def _open(self):
        if self.last_report and self.last_report.exists():QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_report)))
    def _export_to_downloads(self):
        if not self.last_report:return
        try:
            downloads_text=QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
            downloads=Path(downloads_text) if downloads_text else Path.home()/"Downloads"
            archive=export_report_archive(self.last_report,downloads)
            self._append(f"Exported report package: {archive}")
            self.status_changed.emit("Report package exported to Downloads")
            QMessageBox.information(self,"Report exported",f"The HTML report and CSV tables were saved as:\n\n{archive}")
        except Exception as error:
            self._error(f"Could not export report: {error}")
            QMessageBox.critical(self,"Export failed",str(error))
    def _error(self,message):self._append(f"ERROR — {message}");self.status_changed.emit(message)
    def _finished(self):self._worker=None;self._thread=None;self._enable(True)
    def _enable(self,enabled):self.refresh_button.setEnabled(enabled);self.generate_button.setEnabled(enabled);self.open_button.setEnabled(enabled and self.last_report is not None);self.download_button.setEnabled(enabled and self.last_report is not None)
    def _append(self,message):self.log.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
