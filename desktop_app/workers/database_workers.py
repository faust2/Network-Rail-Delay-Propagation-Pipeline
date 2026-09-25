from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from railway_delay.database_service import (
    build_timetable_database,
    download_timetable,
    replace_movement_table,
)


class DatabaseWorker(QObject):
    progress = Signal(int, int, str)
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, operation: str, arguments: dict[str, Any]) -> None:
        super().__init__()
        self._operation = operation
        self._arguments = arguments

    @Slot()
    def run(self) -> None:
        try:
            if self._operation == "download":
                path = download_timetable(
                    username=self._arguments["username"],
                    password=self._arguments["password"],
                    output_path=Path(self._arguments["output_path"]),
                    progress=self.progress.emit,
                )
                self._arguments["password"] = ""
                self.succeeded.emit(f"Timetable downloaded to {path}")
            elif self._operation == "build":
                result = build_timetable_database(
                    input_path=Path(self._arguments["input_path"]),
                    database_path=Path(self._arguments["database_path"]),
                    progress=self.progress.emit,
                )
                self.succeeded.emit(
                    f"Database built: {result['schedules']:,} schedules and "
                    f"{result['locations']:,} locations"
                )
            elif self._operation == "movements":
                result = replace_movement_table(
                    input_path=Path(self._arguments["input_path"]),
                    database_path=Path(self._arguments["database_path"]),
                    progress=self.progress.emit,
                )
                self.succeeded.emit(
                    f"Loaded {result['inserted']:,} movement records "
                    f"({result['bad_lines']:,} skipped)"
                )
            else:
                raise ValueError(f"Unknown database operation: {self._operation}")
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if "password" in self._arguments:
                self._arguments["password"] = ""
            self.finished.emit()
