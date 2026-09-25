from __future__ import annotations

import requests
from PySide6.QtCore import QObject, Signal, Slot


AUTHENTICATION_URL = (
    "https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate"
)


class ConnectionWorker(QObject):
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, username: str, password: str) -> None:
        super().__init__()
        self._username = username
        self._password = password

    @Slot()
    def run(self) -> None:
        try:
            response = requests.get(
                AUTHENTICATION_URL,
                params={"type": "CIF_ALL_FULL_DAILY", "day": "toc-full"},
                auth=(self._username, self._password),
                allow_redirects=False,
                stream=True,
                timeout=20,
            )

            if response.status_code in {200, 301, 302, 303, 307, 308}:
                self.succeeded.emit("Credentials accepted")
            elif response.status_code in {401, 403}:
                self.failed.emit("Authentication failed")
            else:
                self.failed.emit(
                    f"Network Rail returned HTTP {response.status_code}"
                )
            response.close()
        except requests.Timeout:
            self.failed.emit("Connection test timed out")
        except requests.RequestException as error:
            self.failed.emit(f"Connection error: {error}")
        finally:
            self._password = ""
            self.finished.emit()
