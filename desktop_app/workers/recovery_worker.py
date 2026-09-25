from pathlib import Path
from PySide6.QtCore import QObject, Signal, Slot
from railway_delay.recovery_service import load_recovery_results, run_recovery


class RecoveryWorker(QObject):
    progress = Signal(str); results_ready = Signal(object); failed = Signal(str); finished = Signal()

    def __init__(self, database_path: Path, output_directory: Path, run_pipeline: bool,
                 parameters: dict | None = None) -> None:
        super().__init__(); self.database_path = database_path; self.output_directory = output_directory
        self.run_pipeline = run_pipeline; self.parameters = parameters or {}

    @Slot()
    def run(self) -> None:
        try:
            if self.run_pipeline:
                run_recovery(self.database_path, self.output_directory,
                             progress=self.progress.emit, **self.parameters)
            self.results_ready.emit(load_recovery_results(self.database_path))
        except Exception as error: self.failed.emit(str(error))
        finally: self.finished.emit()
