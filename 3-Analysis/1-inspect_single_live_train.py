import sqlite3
import pandas as pd

DB_PATH = "data/railway.db"


def get_candidate_trains(limit: int = 20) -> pd.DataFrame:
    """
    Find train_ids with the most recorded movement events.
    """
    conn = sqlite3.connect(DB_PATH)

    query = f"""
    SELECT
        train_id,
        COUNT(*) AS n_events,
        MIN(actual_time_utc) AS first_event_time,
        MAX(actual_time_utc) AS last_event_time
    FROM train_movements
    WHERE train_id IS NOT NULL
    GROUP BY train_id
    ORDER BY n_events DESC
    LIMIT {limit};
    """

    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def get_train_event_sequence(train_id: str) -> pd.DataFrame:
    """
    Return the ordered event sequence for a single live train.
    Also compute change in timetable variation between successive events.
    """
    conn = sqlite3.connect(DB_PATH)

    query = """
    SELECT
        train_id,
        event_type,
        loc_stanox,
        reporting_stanox,
        planned_time_utc,
        actual_time_utc,
        timetable_variation,
        variation_status,
        platform,
        direction_ind,
        train_service_code,
        toc_id
    FROM train_movements
    WHERE train_id = ?
      AND actual_time_utc IS NOT NULL
    ORDER BY actual_time_utc;
    """

    df = pd.read_sql_query(query, conn, params=(train_id,))
    conn.close()

    if df.empty:
        return df

    df["previous_variation"] = df["timetable_variation"].shift(1)
    df["variation_change"] = df["timetable_variation"] - df["previous_variation"]

    return df


def summarise_variation_dynamics(df: pd.DataFrame) -> None:
    """
    Print a small summary of whether delay grew, shrank, or stayed stable.
    """
    if df.empty:
        print("No events found for this train.")
        return

    n_increase = (df["variation_change"] > 0).sum()
    n_decrease = (df["variation_change"] < 0).sum()
    n_same = (df["variation_change"] == 0).sum()

    print("\nDelay dynamics summary:")
    print(f"Number of times variation increased: {n_increase}")
    print(f"Number of times variation decreased: {n_decrease}")
    print(f"Number of times variation stayed the same: {n_same}")

    print("\nStart and end variation:")
    print(f"First recorded variation: {df['timetable_variation'].iloc[0]}")
    print(f"Last recorded variation:  {df['timetable_variation'].iloc[-1]}")


def main():
    print("\nTop candidate live trains by number of recorded events:")
    candidates = get_candidate_trains(limit=20)
    print(candidates)

    # Pick one manually from the candidate list if you want.
    # Replace this with a train_id from the printed output.
    chosen_train_id = None

    if chosen_train_id is None:
        chosen_train_id = candidates.iloc[0]["train_id"]
        print(f"\nNo train_id manually chosen, using top candidate: {chosen_train_id}")

    seq = get_train_event_sequence(chosen_train_id)

    print(f"\nOrdered event sequence for train_id = {chosen_train_id}:")
    print(seq)

    summarise_variation_dynamics(seq)


if __name__ == "__main__":
    main()