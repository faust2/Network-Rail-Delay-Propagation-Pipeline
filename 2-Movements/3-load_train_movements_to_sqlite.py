from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path("data/railway.db")
SCHEMA_PATH = Path("sql/movement_schema.sql")

# Change this if needed
#INPUT_PATH = Path("data/raw/train_movements_20260416_211237.jsonl")
import sys
from datetime import date
from pathlib import Path

if len(sys.argv) > 1:
    target_date = sys.argv[1]  # format: YYYYMMDD
else:
    target_date = date.today().strftime("%Y%m%d")

data_dir = Path("data/raw")

matching_files = sorted(data_dir.glob(f"train_movements_{target_date}_*.jsonl"))

if not matching_files:
    raise FileNotFoundError(f"No movement files found for {target_date}")

INPUT_PATH = matching_files[-1]

print(f"Using input file: {INPUT_PATH}")


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def run_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    sql = schema_path.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


def safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def normalise_text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s != "" else None


def ms_to_utc_iso(ms: Any) -> str | None:
    ms_int = safe_int(ms)
    if ms_int is None:
        return None
    try:
        dt = datetime.fromtimestamp(ms_int / 1000, tz=timezone.utc)
        return dt.isoformat()
    except (OSError, OverflowError, ValueError):
        return None


INSERT_SQL = """
INSERT INTO train_movements (
    received_at_utc,

    msg_queue_timestamp_ms,
    msg_queue_time_utc,
    msg_type,
    original_data_source,
    source_system_id,

    train_id,
    event_type,
    planned_event_type,
    event_source,

    loc_stanox,
    reporting_stanox,
    next_report_stanox,
    next_report_run_time,

    planned_timestamp_ms,
    actual_timestamp_ms,
    gbtt_timestamp_ms,

    planned_time_utc,
    actual_time_utc,
    gbtt_time_utc,

    timetable_variation,
    variation_status,

    direction_ind,
    platform,
    route,

    train_service_code,
    division_code,
    toc_id,

    train_terminated,
    delay_monitoring_point,
    auto_expected,
    correction_ind,
    offroute_ind,

    raw_body_json
)
VALUES (
    ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?, ?, ?,
    ?
)
"""


def build_row(record: dict[str, Any]) -> tuple[Any, ...]:
    received_at_utc = record.get("received_at_utc")
    message = record.get("message", {}) or {}
    header = message.get("header", {}) or {}
    body = message.get("body", {}) or {}

    msg_queue_timestamp_ms = safe_int(header.get("msg_queue_timestamp"))
    planned_timestamp_ms = safe_int(body.get("planned_timestamp"))
    actual_timestamp_ms = safe_int(body.get("actual_timestamp"))
    gbtt_timestamp_ms = safe_int(body.get("gbtt_timestamp"))

    row = (
        received_at_utc,

        msg_queue_timestamp_ms,
        ms_to_utc_iso(msg_queue_timestamp_ms),
        safe_str(header.get("msg_type")),
        safe_str(header.get("original_data_source")),
        safe_str(header.get("source_system_id")),

        safe_str(body.get("train_id")),
        safe_str(body.get("event_type")),
        safe_str(body.get("planned_event_type")),
        safe_str(body.get("event_source")),

        normalise_text(body.get("loc_stanox")),
        normalise_text(body.get("reporting_stanox")),
        normalise_text(body.get("next_report_stanox")),
        safe_int(body.get("next_report_run_time")),

        planned_timestamp_ms,
        actual_timestamp_ms,
        gbtt_timestamp_ms,

        ms_to_utc_iso(planned_timestamp_ms),
        ms_to_utc_iso(actual_timestamp_ms),
        ms_to_utc_iso(gbtt_timestamp_ms),

        safe_int(body.get("timetable_variation")),
        safe_str(body.get("variation_status")),

        safe_str(body.get("direction_ind")),
        normalise_text(body.get("platform")),
        normalise_text(body.get("route")),

        safe_str(body.get("train_service_code")),
        safe_str(body.get("division_code")),
        safe_str(body.get("toc_id")),

        safe_str(body.get("train_terminated")),
        safe_str(body.get("delay_monitoring_point")),
        safe_str(body.get("auto_expected")),
        safe_str(body.get("correction_ind")),
        safe_str(body.get("offroute_ind")),

        json.dumps(body, ensure_ascii=False),
    )

    # Defensive check so this never silently breaks again
    if len(row) != 34:
        raise ValueError(f"Expected 34 values, got {len(row)}")

    return row


def process_file(conn: sqlite3.Connection, input_path: Path, commit_every: int = 5000) -> None:
    inserted = 0
    bad_lines = 0

    with input_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
                row = build_row(record)
                conn.execute(INSERT_SQL, row)
                inserted += 1
            except Exception as e:
                bad_lines += 1
                print(f"Skipping line {line_num} because of error: {e}")

            if inserted > 0 and inserted % commit_every == 0:
                conn.commit()
                print(f"Inserted {inserted:,} rows so far...")

    conn.commit()
    print(f"Done. Inserted {inserted:,} rows. Bad lines: {bad_lines:,}.")


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database file not found: {DB_PATH}. Build railway.db first."
        )

    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_PATH}")

    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_PATH}")

    conn = get_connection(DB_PATH)
    try:
        run_schema(conn, SCHEMA_PATH)
        process_file(conn, INPUT_PATH)
    finally:
        conn.close()

    print(f"Movement events loaded into: {DB_PATH.resolve()}")


if __name__ == "__main__":
    main()