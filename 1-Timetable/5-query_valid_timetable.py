from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime
from datetime import date
import pandas as pd

DB_PATH = Path("data/railway.db")


def get_day_index(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.weekday()  # Monday=0, Sunday=6


def is_valid_days_runs(days_runs: str, day_index: int) -> bool:
    if not isinstance(days_runs, str):
        return False
    if len(days_runs) != 7:
        return False
    return days_runs[day_index] == "1"


def load_candidate_schedules(
    conn: sqlite3.Connection,
    date_str: str,
    atoc_code: str | None = None,
    cif_train_uid: str | None = None,
) -> pd.DataFrame:
    query = """
    SELECT
        schedule_id,
        cif_train_uid,
        transaction_type,
        schedule_start_date,
        schedule_end_date,
        schedule_days_runs,
        cif_bank_holiday_running,
        train_status,
        cif_stp_indicator,
        atoc_code
    FROM schedules
    WHERE schedule_start_date <= :date_str
      AND schedule_end_date >= :date_str
    """

    params = {"date_str": date_str}

    if atoc_code is not None:
        query += " AND atoc_code = :atoc_code"
        params["atoc_code"] = atoc_code

    if cif_train_uid is not None:
        query += " AND cif_train_uid = :cif_train_uid"
        params["cif_train_uid"] = cif_train_uid

    return pd.read_sql_query(query, conn, params=params)


def filter_valid_for_day(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    day_index = get_day_index(date_str)
    out = df.copy()
    out["runs_on_date"] = out["schedule_days_runs"].apply(
        lambda x: is_valid_days_runs(x, day_index)
    )

    out = out.loc[out["runs_on_date"] == True].copy()
    out.drop(columns=["runs_on_date"], errors="ignore", inplace=True)
    return out


def resolve_basic_stp(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    priority_map = {
        "O": 3,
        "P": 2,
        "N": 1,
        "C": 0,
    }

    out = df.copy()
    out["stp_priority"] = out["cif_stp_indicator"].map(priority_map).fillna(-1)

    out = out.sort_values(
        by=["cif_train_uid", "stp_priority", "schedule_start_date"],
        ascending=[True, False, False]
    )

    out = out.drop_duplicates(subset=["cif_train_uid"], keep="first").copy()
    out.drop(columns=["stp_priority"], inplace=True)

    out = out[out["cif_stp_indicator"] != "C"].copy()
    return out


def get_schedule_locations(
    conn: sqlite3.Connection,
    schedule_id: int,
) -> pd.DataFrame:
    query = """
    SELECT
        l.schedule_id,
        l.location_index,
        l.tiploc_code,
        t.tps_description,
        l.arrival,
        l.departure,
        l.pass,
        l.platform,
        l.line,
        l.path
    FROM schedule_locations l
    LEFT JOIN tiploc t
      ON l.tiploc_code = t.tiploc_code
    WHERE l.schedule_id = :schedule_id
    ORDER BY l.location_index
    """
    return pd.read_sql_query(query, conn, params={"schedule_id": schedule_id})


def build_valid_timetable_for_date(
    date_str: str,
    atoc_code: str | None = None,
    cif_train_uid: str | None = None,
) -> tuple[pd.DataFrame, dict[int, pd.DataFrame]]:
    conn = sqlite3.connect(DB_PATH)
    try:
        candidates = load_candidate_schedules(
            conn=conn,
            date_str=date_str,
            atoc_code=atoc_code,
            cif_train_uid=cif_train_uid,
        )

        print("Candidate schedules before weekday filtering:", len(candidates))

        valid = filter_valid_for_day(candidates, date_str)
        valid = resolve_basic_stp(valid)

        timetable_dict: dict[int, pd.DataFrame] = {}
        for schedule_id in valid["schedule_id"].tolist():
            timetable_dict[schedule_id] = get_schedule_locations(conn, schedule_id)

        return valid, timetable_dict

    finally:
        conn.close()


def find_longest_valid_schedule(
    valid_schedules: pd.DataFrame,
    timetable_dict: dict[int, pd.DataFrame]
) -> tuple[int | None, int]:
    if valid_schedules.empty:
        return None, 0

    best_schedule_id = None
    max_locations = -1

    for schedule_id in valid_schedules["schedule_id"].tolist():
        n_locations = len(timetable_dict[schedule_id])
        if n_locations > max_locations:
            max_locations = n_locations
            best_schedule_id = schedule_id

    return best_schedule_id, max_locations


def main() -> None:
    #date_str = "2026-04-16"
    date_str = date.today().isoformat()
    atoc_code = "GW"
    cif_train_uid = None

    valid_schedules, timetable_dict = build_valid_timetable_for_date(
        date_str=date_str,
        atoc_code=atoc_code,
        cif_train_uid=cif_train_uid,
    )

    print(f"\nValid schedules for {date_str}:")
    print(valid_schedules.head(20))
    print(f"\nTotal valid schedules returned: {len(valid_schedules)}")

    best_schedule_id, max_locations = find_longest_valid_schedule(
        valid_schedules, timetable_dict
    )

    if best_schedule_id is not None:
        best_uid = valid_schedules.loc[
            valid_schedules["schedule_id"] == best_schedule_id, "cif_train_uid"
        ].iloc[0]

        print(
            f"\nLongest valid schedule for {date_str}: "
            f"schedule_id = {best_schedule_id}, "
            f"cif_train_uid = {best_uid}, "
            f"n_locations = {max_locations}"
        )
        print(timetable_dict[best_schedule_id].head(100))
    else:
        print("\nNo valid schedules found for the chosen filters.")


if __name__ == "__main__":
    main()