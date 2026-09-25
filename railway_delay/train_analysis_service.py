from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


def list_candidate_trains(database_path: Path, limit: int = 100) -> pd.DataFrame:
    if not database_path.exists():
        raise FileNotFoundError("railway.db does not exist in the selected workspace")
    connection = sqlite3.connect(database_path)
    try:
        return pd.read_sql_query(
            """
            SELECT
                train_id,
                COUNT(*) AS n_events,
                MIN(actual_time_utc) AS first_event_time,
                MAX(actual_time_utc) AS last_event_time
            FROM train_movements
            WHERE train_id IS NOT NULL
              AND actual_time_utc IS NOT NULL
            GROUP BY train_id
            ORDER BY n_events DESC, train_id
            LIMIT ?
            """,
            connection,
            params=(limit,),
        )
    finally:
        connection.close()


def get_train_events(database_path: Path, train_id: str) -> pd.DataFrame:
    if not database_path.exists():
        raise FileNotFoundError("railway.db does not exist in the selected workspace")

    connection = sqlite3.connect(database_path)
    try:
        events = pd.read_sql_query(
            """
            SELECT
                tm.train_id,
                tm.event_type,
                tm.loc_stanox,
                COALESCE(
                    (
                        SELECT COALESCE(t.tps_description, t.description)
                        FROM tiploc t
                        WHERE t.stanox = tm.loc_stanox
                        LIMIT 1
                    ),
                    tm.loc_stanox,
                    'Unknown location'
                ) AS location_name,
                tm.reporting_stanox,
                tm.planned_time_utc,
                tm.actual_time_utc,
                tm.timetable_variation,
                tm.variation_status,
                tm.platform,
                tm.direction_ind,
                tm.train_service_code,
                tm.toc_id
            FROM train_movements tm
            WHERE tm.train_id = ?
              AND tm.actual_time_utc IS NOT NULL
            ORDER BY tm.actual_time_utc
            """,
            connection,
            params=(train_id,),
        )
    finally:
        connection.close()

    if events.empty:
        return events

    events["actual_time_utc"] = pd.to_datetime(
        events["actual_time_utc"], errors="coerce", utc=True
    )
    events["planned_time_utc"] = pd.to_datetime(
        events["planned_time_utc"], errors="coerce", utc=True
    )
    events["timetable_variation"] = pd.to_numeric(
        events["timetable_variation"], errors="coerce"
    )
    events = events.dropna(subset=["actual_time_utc"]).reset_index(drop=True)
    events["previous_variation"] = events["timetable_variation"].shift(1)
    events["variation_change"] = (
        events["timetable_variation"] - events["previous_variation"]
    )
    return events


def summarise_train(events: pd.DataFrame) -> dict[str, int | float | str]:
    if events.empty:
        return {
            "n_events": 0,
            "start_variation": 0,
            "end_variation": 0,
            "net_change": 0,
            "max_variation": 0,
            "n_increases": 0,
            "n_decreases": 0,
            "n_same": 0,
            "case_type": "NO DATA",
        }

    valid = events["timetable_variation"].dropna()
    if valid.empty:
        start = end = maximum = net = 0.0
    else:
        start = float(valid.iloc[0])
        end = float(valid.iloc[-1])
        maximum = float(valid.max())
        net = end - start

    changes = events["variation_change"].dropna()
    n_increases = int((changes > 0).sum())
    n_decreases = int((changes < 0).sum())
    n_same = int((changes == 0).sum())
    maximum_jump = float(changes.max()) if not changes.empty else 0.0

    if maximum_jump >= 10 and n_increases <= 2:
        case_type = "SUDDEN DISRUPTION"
    elif n_increases >= 2 and net > 0 and maximum_jump < 10:
        case_type = "PROPAGATION"
    elif n_increases >= 2 and net > 0 and maximum_jump >= 10:
        case_type = "MIXED"
    else:
        case_type = "STABLE OR RECOVERING"

    return {
        "n_events": len(events),
        "start_variation": start,
        "end_variation": end,
        "net_change": net,
        "max_variation": maximum,
        "n_increases": n_increases,
        "n_decreases": n_decreases,
        "n_same": n_same,
        "case_type": case_type,
    }
