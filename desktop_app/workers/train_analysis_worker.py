from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from railway_delay.train_analysis_service import (
    get_train_events,
    list_candidate_trains,
    summarise_train,
)


class TrainAnalysisWorker(QObject):
    candidates_loaded = Signal(object)
    train_loaded = Signal(object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, database_path: Path, train_id: str | None = None) -> None:
        super().__init__()
        self._database_path = database_path
        self._train_id = train_id

    @Slot()
    def run(self) -> None:
        try:
            if self._train_id is None:
                self.candidates_loaded.emit(
                    list_candidate_trains(self._database_path)
                )
            else:
                events = get_train_events(self._database_path, self._train_id)
                self.train_loaded.emit(events, summarise_train(events))
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()
