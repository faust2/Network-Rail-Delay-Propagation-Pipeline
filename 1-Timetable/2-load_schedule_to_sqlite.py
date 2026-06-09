import gzip
import json
import sqlite3
from pathlib import Path

# Paths
DB_PATH = Path("data/railway.db")
INPUT_PATH = Path("data/raw/schedule_full.json.gz")
SCHEMA_PATH = Path("sql/schema.sql")


def create_database():
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    return conn


def insert_tiploc(conn, payload):
    conn.execute("""
        INSERT OR REPLACE INTO tiploc VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        payload.get("tiploc_code"),
        payload.get("transaction_type"),
        payload.get("nalco"),
        payload.get("stanox"),
        payload.get("crs_code"),
        payload.get("description"),
        payload.get("tps_description"),
    ))


def insert_schedule(conn, payload):
    cursor = conn.execute("""
        INSERT INTO schedules (
            cif_train_uid,
            transaction_type,
            schedule_start_date,
            schedule_end_date,
            schedule_days_runs,
            cif_bank_holiday_running,
            train_status,
            cif_stp_indicator,
            atoc_code
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        payload.get("CIF_train_uid"),
        payload.get("transaction_type"),
        payload.get("schedule_start_date"),
        payload.get("schedule_end_date"),
        payload.get("schedule_days_runs"),
        payload.get("CIF_bank_holiday_running"),
        payload.get("train_status"),
        payload.get("CIF_stp_indicator"),
        payload.get("atoc_code"),
    ))

    return cursor.lastrowid


def insert_locations(conn, schedule_id, payload):
    segment = payload.get("schedule_segment", {})
    locations = segment.get("schedule_location", [])

    for i, loc in enumerate(locations):
        conn.execute("""
            INSERT INTO schedule_locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            schedule_id,
            i,
            loc.get("tiploc_code"),
            loc.get("arrival"),
            loc.get("departure"),
            loc.get("pass"),
            loc.get("platform"),
            loc.get("line"),
            loc.get("path"),
        ))


def load_data():
    conn = create_database()

    with gzip.open(INPUT_PATH, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            record = json.loads(line)

            if "TiplocV1" in record:
                insert_tiploc(conn, record["TiplocV1"])

            elif "JsonScheduleV1" in record:
                payload = record["JsonScheduleV1"]
                schedule_id = insert_schedule(conn, payload)
                insert_locations(conn, schedule_id, payload)

            if i % 5000 == 0:
                conn.commit()
                print(f"Processed {i:,} lines")

    conn.commit()
    conn.close()
    print("Database created successfully!")


if __name__ == "__main__":
    load_data()