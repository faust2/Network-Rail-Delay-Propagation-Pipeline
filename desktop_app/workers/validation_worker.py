from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from railway_delay.validation_service import load_validation_results, run_validation


class ValidationWorker(QObject):
    progress = Signal(str)
    results_ready = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, database_path: Path, output_directory: Path,
                 run_pipeline: bool, parameters: dict[str, float | int] | None = None) -> None:
        super().__init__()
        self._database_path = database_path
        self._output_directory = output_directory
        self._run_pipeline = run_pipeline
        self._parameters = parameters or {}

    @Slot()
    def run(self) -> None:
        try:
            if self._run_pipeline:
                run_validation(self._database_path, self._output_directory,
                               progress=self.progress.emit, **self._parameters)
            self.results_ready.emit(load_validation_results(self._database_path))
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()
