from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import stomp
from PySide6.QtCore import QObject, Signal, Slot


HOST = "publicdatafeeds.networkrail.co.uk"
PORT = 61618
TOPIC = "/topic/TRAIN_MVT_ALL_TOC"


class _MovementListener(stomp.ConnectionListener):
    def __init__(self, worker: "MovementWorker") -> None:
        self._worker = worker

    def on_error(self, frame: Any) -> None:
        body = getattr(frame, "body", "Unknown STOMP error")
        self._worker.log_message.emit(f"STOMP error: {body}")

    def on_disconnected(self) -> None:
        if not self._worker.stop_requested:
            self._worker.log_message.emit("The movement feed disconnected unexpectedly")

    def on_message(self, frame: Any) -> None:
        self._worker.handle_message(getattr(frame, "body", ""))


class MovementWorker(QObject):
    connected = Signal(str)
    counters_changed = Signal(int, int)
    log_message = Signal(str)
    failed = Signal(str)
    finished = Signal(str)

    def __init__(
        self,
        username: str,
        password: str,
        data_directory: Path,
    ) -> None:
        super().__init__()
        self._username = username
        self._password = password
        self._data_directory = data_directory
        self._stop_event = threading.Event()
        self._connection: stomp.Connection12 | None = None
        self._output_path: Path | None = None
        self._message_count = 0
        self._batch_count = 0

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def request_stop(self) -> None:
        """Thread-safe stop request, callable directly by the GUI thread."""
        self._stop_event.set()

    @Slot()
    def run(self) -> None:
        try:
            raw_directory = self._data_directory / "raw"
            raw_directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._output_path = raw_directory / f"train_movements_{timestamp}.jsonl"

            self.log_message.emit(f"Connecting to {HOST}:{PORT}")
            self._connection = stomp.Connection12(
                host_and_ports=[(HOST, PORT)],
                heartbeats=(10000, 10000),
                keepalive=True,
            )
            self._connection.set_listener("railconnect", _MovementListener(self))
            self._connection.connect(
                login=self._username,
                passcode=self._password,
                wait=True,
            )
            self._password = ""

            self._connection.subscribe(
                destination=TOPIC,
                id="railconnect-train-movements",
                ack="auto",
            )
            self.connected.emit(str(self._output_path))
            self.log_message.emit(f"Subscribed to {TOPIC}")

            while not self._stop_event.wait(0.25):
                if self._connection is not None and not self._connection.is_connected():
                    raise ConnectionError("The movement feed connection was lost")

        except Exception as error:
            if not self.stop_requested:
                self.failed.emit(str(error))
        finally:
            self._password = ""
            self._disconnect()
            output = str(self._output_path) if self._output_path else ""
            self.finished.emit(output)

    def handle_message(self, body: str) -> None:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            self.log_message.emit(f"Skipped invalid JSON batch: {error}")
            return

        messages = payload if isinstance(payload, list) else [payload]
        received_at = datetime.now(timezone.utc).isoformat()

        if self._output_path is None:
            return

        try:
            with self._output_path.open("a", encoding="utf-8") as output_file:
                for message in messages:
                    row = {
                        "received_at_utc": received_at,
                        "message": message,
                    }
                    output_file.write(json.dumps(row) + "\n")
        except OSError as error:
            self.failed.emit(f"Could not write movement data: {error}")
            self._stop_event.set()
            return

        self._batch_count += 1
        self._message_count += len(messages)
        self.counters_changed.emit(self._message_count, self._batch_count)

        if self._batch_count == 1 or self._batch_count % 20 == 0:
            self.log_message.emit(
                f"Captured {self._message_count:,} messages "
                f"in {self._batch_count:,} batches"
            )

    def _disconnect(self) -> None:
        if self._connection is None:
            return
        try:
            if self._connection.is_connected():
                self._connection.disconnect()
        except Exception as error:
            print(f"Movement disconnect warning: {error}", file=sys.stderr)
