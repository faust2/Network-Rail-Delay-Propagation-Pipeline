from pathlib import Path
from PySide6.QtCore import QObject, Signal, Slot
from railway_delay.report_service import generate_report, inspect_report_sources


class ReportWorker(QObject):
    progress = Signal(str); inventory_ready = Signal(object); report_ready = Signal(str)
    failed = Signal(str); finished = Signal()

    def __init__(self, database_path: Path, reports_root: Path, generate: bool) -> None:
        super().__init__(); self.database_path = database_path; self.reports_root = reports_root; self.generate = generate

    @Slot()
    def run(self) -> None:
        try:
            if self.generate:
                path = generate_report(self.database_path, self.reports_root, self.progress.emit)
                self.report_ready.emit(str(path))
            self.inventory_ready.emit(inspect_report_sources(self.database_path))
        except Exception as error: self.failed.emit(str(error))
        finally: self.finished.emit()
