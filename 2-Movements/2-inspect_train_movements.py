import json
from pathlib import Path
from pprint import pprint

# 🔧 CHANGE THIS to your actual filename

#FILE_PATH = Path("data/raw/train_movements_20260416_211237.jsonl")

import sys
from datetime import date
#from pathlib import Path

if len(sys.argv) > 1:
    target_date = sys.argv[1]  # e.g. 20260416
else:
    target_date = date.today().strftime("%Y%m%d")

data_dir = Path("data/raw")

matching_files = sorted(data_dir.glob(f"train_movements_{target_date}_*.jsonl"))

if not matching_files:
    raise FileNotFoundError(f"No movement files found for {target_date}")

FILE_PATH = matching_files[-1]

print(f"Using movement file: {FILE_PATH}")


def inspect_file(n_lines: int = 5):
    """
    Print the first n_lines of the JSONL file in a readable format.
    """

    if not FILE_PATH.exists():
        print(f"File not found: {FILE_PATH}")
        return

    print(f"\nInspecting first {n_lines} records from:\n{FILE_PATH}\n")

    with FILE_PATH.open("r", encoding="utf-8") as f:
        for i in range(n_lines):
            line = f.readline()
            if not line:
                break

            print(f"\n--- RECORD {i+1} ---")

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Failed to parse JSON: {e}")
                continue

            # Top-level structure
            print("\nTop-level keys:")
            print(record.keys())

            # Timestamp
            print("\nReceived at:")
            print(record.get("received_at_utc"))

            message = record.get("message", {})

            print("\nMessage keys:")
            print(message.keys())

            # Header
            header = message.get("header", {})
            print("\nHeader:")
            pprint(header)

            # Body
            body = message.get("body", {})
            print("\nBody keys:")
            print(body.keys())

            print("\nBody (full):")
            pprint(body)


def count_message_types(n_lines: int = 1000):
    """
    Count the frequency of different message types in the first n_lines.
    """

    if not FILE_PATH.exists():
        print(f"File not found: {FILE_PATH}")
        return

    counts = {}

    with FILE_PATH.open("r", encoding="utf-8") as f:
        for i in range(n_lines):
            line = f.readline()
            if not line:
                break

            try:
                record = json.loads(line)
                body = record.get("message", {}).get("body", {})
                msg_type = body.get("event_type", "UNKNOWN")
            except Exception:
                msg_type = "ERROR"

            counts[msg_type] = counts.get(msg_type, 0) + 1

    print(f"\nMessage type counts (first {n_lines} records):")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"{k}: {v}")


if __name__ == "__main__":
    # Step 1: inspect a few records
    inspect_file(n_lines=5)

    # Step 2: get a rough idea of message types
    count_message_types(n_lines=1000)