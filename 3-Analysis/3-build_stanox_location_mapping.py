import sqlite3
import pandas as pd

DB_PATH = "data/railway.db"


def build_stanox_lookup(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Build a simple STANOX -> human-readable location lookup from the tiploc table.

    Notes:
    - Multiple TIPLOC rows can share the same STANOX.
    - We choose one representative name using MIN(tps_description) for now.
    - We also keep a count so ambiguity is visible.
    """
    query = """
    SELECT
        TRIM(stanox) AS stanox,
        MIN(tps_description) AS location_name,
        COUNT(*) AS stanox_match_count
    FROM tiploc
    WHERE stanox IS NOT NULL
      AND TRIM(stanox) <> ''
      AND tps_description IS NOT NULL
      AND TRIM(tps_description) <> ''
    GROUP BY TRIM(stanox)
    ORDER BY stanox;
    """
    return pd.read_sql_query(query, conn)


def save_stanox_lookup(conn: sqlite3.Connection, lookup_df: pd.DataFrame) -> None:
    """
    Save the lookup table into SQLite.
    """
    conn.execute("DROP TABLE IF EXISTS stanox_lookup;")
    lookup_df.to_sql("stanox_lookup", conn, index=False, if_exists="replace")
    conn.commit()


def build_enriched_train_movements(conn: sqlite3.Connection) -> None:
    """
    Create a new table joining train_movements to stanox_lookup.
    """
    conn.execute("DROP TABLE IF EXISTS train_movements_enriched;")

    create_sql = """
    CREATE TABLE train_movements_enriched AS
    SELECT
        tm.*,
        sl.location_name,
        sl.stanox_match_count
    FROM train_movements tm
    LEFT JOIN stanox_lookup sl
      ON TRIM(tm.loc_stanox) = sl.stanox;
    """

    conn.execute(create_sql)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tme_train_id
        ON train_movements_enriched (train_id);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tme_loc_stanox
        ON train_movements_enriched (loc_stanox);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tme_location_name
        ON train_movements_enriched (location_name);
    """)
    conn.commit()


def print_mapping_summary(conn: sqlite3.Connection) -> None:
    """
    Print a quick summary of match quality.
    """
    q_total = "SELECT COUNT(*) AS n FROM train_movements_enriched;"
    q_matched = """
    SELECT COUNT(*) AS n
    FROM train_movements_enriched
    WHERE location_name IS NOT NULL;
    """
    q_unmatched = """
    SELECT COUNT(*) AS n
    FROM train_movements_enriched
    WHERE location_name IS NULL;
    """
    q_ambiguous = """
    SELECT COUNT(*) AS n
    FROM train_movements_enriched
    WHERE stanox_match_count > 1;
    """

    total = pd.read_sql_query(q_total, conn)["n"].iloc[0]
    matched = pd.read_sql_query(q_matched, conn)["n"].iloc[0]
    unmatched = pd.read_sql_query(q_unmatched, conn)["n"].iloc[0]
    ambiguous = pd.read_sql_query(q_ambiguous, conn)["n"].iloc[0]

    print("\nMapping summary:")
    print(f"Total movement rows:         {total}")
    print(f"Rows with matched location:  {matched}")
    print(f"Rows with no location match: {unmatched}")
    print(f"Rows with ambiguous STANOX:  {ambiguous}")


def print_sample_rows(conn: sqlite3.Connection, n: int = 20) -> None:
    """
    Show a few enriched movement rows.
    """
    query = f"""
    SELECT
        train_id,
        event_type,
        loc_stanox,
        location_name,
        planned_time_utc,
        actual_time_utc,
        timetable_variation,
        variation_status,
        platform
    FROM train_movements_enriched
    LIMIT {n};
    """
    df = pd.read_sql_query(query, conn)
    print("\nSample enriched movement rows:")
    print(df)


def print_unmatched_stanox(conn: sqlite3.Connection, n: int = 20) -> None:
    """
    Show common STANOX values that failed to map.
    """
    query = f"""
    SELECT
        loc_stanox,
        COUNT(*) AS n_rows
    FROM train_movements_enriched
    WHERE location_name IS NULL
      AND loc_stanox IS NOT NULL
      AND TRIM(loc_stanox) <> ''
    GROUP BY loc_stanox
    ORDER BY n_rows DESC
    LIMIT {n};
    """
    df = pd.read_sql_query(query, conn)
    print("\nTop unmatched STANOX values:")
    print(df)


def print_ambiguous_stanox(conn: sqlite3.Connection, n: int = 20) -> None:
    """
    Show STANOX values with multiple possible TIPLOC matches.
    """
    query = f"""
    SELECT
        stanox,
        location_name,
        stanox_match_count
    FROM stanox_lookup
    WHERE stanox_match_count > 1
    ORDER BY stanox_match_count DESC, stanox
    LIMIT {n};
    """
    df = pd.read_sql_query(query, conn)
    print("\nSample ambiguous STANOX mappings:")
    print(df)


def main():
    conn = sqlite3.connect(DB_PATH)

    try:
        lookup_df = build_stanox_lookup(conn)
        save_stanox_lookup(conn, lookup_df)
        build_enriched_train_movements(conn)

        print_mapping_summary(conn)
        print_sample_rows(conn, n=20)
        print_unmatched_stanox(conn, n=20)
        print_ambiguous_stanox(conn, n=20)

    finally:
        conn.close()


if __name__ == "__main__":
    main()