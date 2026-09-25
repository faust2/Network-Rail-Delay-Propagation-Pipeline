from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from railway_delay.propagation_service import (
    load_propagation_results,
    run_propagation_pipeline,
)


class PropagationWorker(QObject):
    progress = Signal(str)
    results_ready = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        database_path: Path,
        run_pipeline: bool,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._database_path = database_path
        self._run_pipeline = run_pipeline
        self._parameters = parameters or {}

    @Slot()
    def run(self) -> None:
        try:
            if self._run_pipeline:
                run_propagation_pipeline(
                    database_path=self._database_path,
                    progress=self.progress.emit,
                    **self._parameters,
                )
            self.results_ready.emit(load_propagation_results(self._database_path))
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()
