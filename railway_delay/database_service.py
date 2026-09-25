from __future__ import annotations

import gzip
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TIMETABLE_SCHEMA = PROJECT_ROOT / "sql" / "schema.sql"
MOVEMENT_SCHEMA = PROJECT_ROOT / "sql" / "movement_schema.sql"
SCHEDULE_URL = (
    "https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate"
)

ProgressCallback = Callable[[int, int, str], None]


def download_timetable(
    username: str,
    password: str,
    output_path: Path,
    progress: ProgressCallback,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".part")

    try:
        with requests.get(
            SCHEDULE_URL,
            params={"type": "CIF_ALL_FULL_DAILY", "day": "toc-full"},
            auth=(username, password),
            allow_redirects=True,
            timeout=120,
            stream=True,
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", "0") or 0)
            received = 0
            with temporary_path.open("wb") as output_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output_file.write(chunk)
                    received += len(chunk)
                    progress(received, total, f"Downloaded {received / 1048576:.1f} MB")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return output_path


def build_timetable_database(
    input_path: Path,
    database_path: Path,
    progress: ProgressCallback,
) -> dict[str, int]:
    if not input_path.exists():
        raise FileNotFoundError(f"Timetable file not found: {input_path}")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    schedules = 0
    locations = 0
    tiplocs = 0
    lines = 0

    try:
        connection.executescript(TIMETABLE_SCHEMA.read_text(encoding="utf-8"))
        with gzip.open(input_path, "rt", encoding="utf-8") as input_file:
            for lines, line in enumerate(input_file, start=1):
                record = json.loads(line)
                if "TiplocV1" in record:
                    payload = record["TiplocV1"]
                    connection.execute(
                        "INSERT OR REPLACE INTO tiploc VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            payload.get("tiploc_code"),
                            payload.get("transaction_type"),
                            payload.get("nalco"),
                            payload.get("stanox"),
                            payload.get("crs_code"),
                            payload.get("description"),
                            payload.get("tps_description"),
                        ),
                    )
                    tiplocs += 1
                elif "JsonScheduleV1" in record:
                    payload = record["JsonScheduleV1"]
                    cursor = connection.execute(
                        """
                        INSERT INTO schedules (
                            cif_train_uid, transaction_type, schedule_start_date,
                            schedule_end_date, schedule_days_runs,
                            cif_bank_holiday_running, train_status,
                            cif_stp_indicator, atoc_code
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            payload.get("CIF_train_uid"),
                            payload.get("transaction_type"),
                            payload.get("schedule_start_date"),
                            payload.get("schedule_end_date"),
                            payload.get("schedule_days_runs"),
                            payload.get("CIF_bank_holiday_running"),
                            payload.get("train_status"),
                            payload.get("CIF_stp_indicator"),
                            payload.get("atoc_code"),
                        ),
                    )
                    schedules += 1
                    schedule_id = cursor.lastrowid
                    schedule_locations = (
                        payload.get("schedule_segment", {}).get("schedule_location", [])
                    )
                    for index, location in enumerate(schedule_locations):
                        connection.execute(
                            "INSERT INTO schedule_locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                schedule_id,
                                index,
                                location.get("tiploc_code"),
                                location.get("arrival"),
                                location.get("departure"),
                                location.get("pass"),
                                location.get("platform"),
                                location.get("line"),
                                location.get("path"),
                            ),
                        )
                    locations += len(schedule_locations)

                if lines % 5000 == 0:
                    connection.commit()
                    progress(
                        lines,
                        0,
                        f"Processed {lines:,} records — {schedules:,} schedules",
                    )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "records": lines,
        "tiplocs": tiplocs,
        "schedules": schedules,
        "locations": locations,
    }


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _normalise_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ms_to_utc_iso(value: Any) -> str | None:
    milliseconds = _safe_int(value)
    if milliseconds is None:
        return None
    try:
        return datetime.fromtimestamp(
            milliseconds / 1000, tz=timezone.utc
        ).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


MOVEMENT_INSERT = """
INSERT INTO train_movements (
    received_at_utc, msg_queue_timestamp_ms, msg_queue_time_utc, msg_type,
    original_data_source, source_system_id, train_id, event_type,
    planned_event_type, event_source, loc_stanox, reporting_stanox,
    next_report_stanox, next_report_run_time, planned_timestamp_ms,
    actual_timestamp_ms, gbtt_timestamp_ms, planned_time_utc,
    actual_time_utc, gbtt_time_utc, timetable_variation, variation_status,
    direction_ind, platform, route, train_service_code, division_code, toc_id,
    train_terminated, delay_monitoring_point, auto_expected, correction_ind,
    offroute_ind, raw_body_json
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""


def _movement_row(record: dict[str, Any]) -> tuple[Any, ...]:
    received_at = record.get("received_at_utc")
    message = record.get("message", {}) or {}
    header = message.get("header", {}) or {}
    body = message.get("body", {}) or {}
    queue_ms = _safe_int(header.get("msg_queue_timestamp"))
    planned_ms = _safe_int(body.get("planned_timestamp"))
    actual_ms = _safe_int(body.get("actual_timestamp"))
    gbtt_ms = _safe_int(body.get("gbtt_timestamp"))

    return (
        received_at,
        queue_ms,
        _ms_to_utc_iso(queue_ms),
        _safe_str(header.get("msg_type")),
        _safe_str(header.get("original_data_source")),
        _safe_str(header.get("source_system_id")),
        _safe_str(body.get("train_id")),
        _safe_str(body.get("event_type")),
        _safe_str(body.get("planned_event_type")),
        _safe_str(body.get("event_source")),
        _normalise_text(body.get("loc_stanox")),
        _normalise_text(body.get("reporting_stanox")),
        _normalise_text(body.get("next_report_stanox")),
        _safe_int(body.get("next_report_run_time")),
        planned_ms,
        actual_ms,
        gbtt_ms,
        _ms_to_utc_iso(planned_ms),
        _ms_to_utc_iso(actual_ms),
        _ms_to_utc_iso(gbtt_ms),
        _safe_int(body.get("timetable_variation")),
        _safe_str(body.get("variation_status")),
        _safe_str(body.get("direction_ind")),
        _normalise_text(body.get("platform")),
        _normalise_text(body.get("route")),
        _safe_str(body.get("train_service_code")),
        _safe_str(body.get("division_code")),
        _safe_str(body.get("toc_id")),
        _safe_str(body.get("train_terminated")),
        _safe_str(body.get("delay_monitoring_point")),
        _safe_str(body.get("auto_expected")),
        _safe_str(body.get("correction_ind")),
        _safe_str(body.get("offroute_ind")),
        json.dumps(body, ensure_ascii=False),
    )


def replace_movement_table(
    input_path: Path,
    database_path: Path,
    progress: ProgressCallback,
) -> dict[str, int]:
    if not input_path.exists():
        raise FileNotFoundError(f"Movement file not found: {input_path}")
    if not database_path.exists():
        raise FileNotFoundError("Build the timetable database first")

    connection = sqlite3.connect(database_path)
    inserted = 0
    bad_lines = 0
    try:
        connection.executescript(MOVEMENT_SCHEMA.read_text(encoding="utf-8"))
        with input_path.open("r", encoding="utf-8") as input_file:
            for line_number, line in enumerate(input_file, start=1):
                if not line.strip():
                    continue
                try:
                    connection.execute(
                        MOVEMENT_INSERT, _movement_row(json.loads(line))
                    )
                    inserted += 1
                except Exception:
                    bad_lines += 1
                if line_number % 5000 == 0:
                    connection.commit()
                    progress(
                        line_number,
                        0,
                        f"Inserted {inserted:,} movements — {bad_lines:,} skipped",
                    )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"inserted": inserted, "bad_lines": bad_lines}


def inspect_database(database_path: Path) -> dict[str, int | str | bool]:
    summary: dict[str, int | str | bool] = {
        "exists": database_path.exists(),
        "path": str(database_path),
        "size_bytes": database_path.stat().st_size if database_path.exists() else 0,
        "tiplocs": 0,
        "schedules": 0,
        "locations": 0,
        "movements": 0,
    }
    if not database_path.exists():
        return summary

    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for key, table in (
            ("tiplocs", "tiploc"),
            ("schedules", "schedules"),
            ("locations", "schedule_locations"),
            ("movements", "train_movements"),
        ):
            if table in tables:
                summary[key] = connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
    finally:
        connection.close()
    return summary
