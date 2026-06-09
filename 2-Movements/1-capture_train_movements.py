from __future__ import annotations

import gzip
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import stomp

HOST = "publicdatafeeds.networkrail.co.uk"
PORT = 61618
TOPIC = "/topic/TRAIN_MVT_ALL_TOC"

RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)


class TrainMovementListener(stomp.ConnectionListener):
    def __init__(self, output_path: Path):
        self.output_path = output_path
        self.message_count = 0
        self.batch_count = 0

    def on_error(self, frame):
        print("STOMP error:", frame.body, file=sys.stderr)

    def on_disconnected(self):
        print("Disconnected from feed.", file=sys.stderr)

    def on_message(self, frame):
        """
        The Train Movements feed sends a JSON list.
        Each element is one movement-related message with 'header' and 'body'.
        """
        try:
            payload = json.loads(frame.body)
        except json.JSONDecodeError as e:
            print(f"Failed to decode message batch: {e}", file=sys.stderr)
            return

        if not isinstance(payload, list):
            print("Unexpected payload type; expected a JSON list.", file=sys.stderr)
            return

        received_at = datetime.now(timezone.utc).isoformat()

        with self.output_path.open("a", encoding="utf-8") as f:
            for msg in payload:
                row = {
                    "received_at_utc": received_at,
                    "message": msg,
                }
                f.write(json.dumps(row) + "\n")

        self.batch_count += 1
        self.message_count += len(payload)

        if self.batch_count % 20 == 0:
            print(
                f"Captured {self.message_count:,} messages "
                f"in {self.batch_count:,} batches"
            )


def build_output_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return RAW_DIR / f"train_movements_{ts}.jsonl"


def main() -> None:
    #username = os.environ.get("NR_USERNAME")
    #password = os.environ.get("NR_PASSWORD")
    username = "cblaxlandkay@gmail.com"
    password = "Hyperion00!!"

    if not username or not password:
        raise ValueError("Set NR_USERNAME and NR_PASSWORD environment variables first.")

    output_path = build_output_path()
    listener = TrainMovementListener(output_path)

    conn = stomp.Connection12(
        host_and_ports=[(HOST, PORT)],
        heartbeats=(10000, 10000),
        keepalive=True,
    )
    conn.set_listener("", listener)

    stop_requested = False

    def handle_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True
        print("\nStop requested, disconnecting...")

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    print(f"Connecting to {HOST}:{PORT}")
    conn.connect(login=username, passcode=password, wait=True)

    # Durable subscriptions are recommended in the docs, but for a first
    # collector this simple subscription is enough to start investigating.
    conn.subscribe(destination=TOPIC, id="train-movements", ack="auto")

    print(f"Subscribed to {TOPIC}")
    print(f"Writing raw messages to {output_path}")

    try:
        while not stop_requested:
            time.sleep(1)
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
        print(f"Finished. Raw file saved to {output_path}")


if __name__ == "__main__":
    main()