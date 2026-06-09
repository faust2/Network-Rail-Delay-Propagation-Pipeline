import sqlite3
import pandas as pd

DB_PATH = "data/railway.db"


def load_train_event_sequences(min_events: int = 5) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)

    query = """
    WITH train_counts AS (
        SELECT
            train_id,
            COUNT(*) AS n_events
        FROM train_movements
        WHERE train_id IS NOT NULL
          AND actual_time_utc IS NOT NULL
          AND timetable_variation IS NOT NULL
        GROUP BY train_id
    )
    SELECT
        tm.train_id,
        tm.event_type,
        tm.loc_stanox,
        tm.reporting_stanox,
        tm.planned_time_utc,
        tm.actual_time_utc,
        tm.timetable_variation,
        tm.variation_status,
        tm.platform,
        tm.direction_ind,
        tm.train_service_code,
        tm.toc_id,
        tc.n_events
    FROM train_movements tm
    JOIN train_counts tc
      ON tm.train_id = tc.train_id
    WHERE tc.n_events >= ?
      AND tm.actual_time_utc IS NOT NULL
      AND tm.timetable_variation IS NOT NULL
    ORDER BY tm.train_id, tm.actual_time_utc;
    """

    df = pd.read_sql_query(query, conn, params=(min_events,))
    conn.close()
    return df


def classify_case(row: pd.Series) -> str:
    max_jump = row["max_single_increase"]
    n_increases = row["n_increases"]
    net_change = row["net_change"]

    if pd.isna(max_jump):
        max_jump = 0
    if pd.isna(n_increases):
        n_increases = 0
    if pd.isna(net_change):
        net_change = 0

    if max_jump >= 10 and n_increases <= 2:
        return "SUDDEN_DISRUPTION"

    if n_increases >= 2 and net_change > 0 and max_jump < 10:
        return "PROPAGATION"

    if n_increases >= 2 and net_change > 0 and max_jump >= 10:
        return "MIXED"

    return "STABLE_OR_RECOVERING"


def compute_train_metrics(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["previous_variation"] = work.groupby("train_id")["timetable_variation"].shift(1)
    work["variation_change"] = work["timetable_variation"] - work["previous_variation"]

    summary = (
        work.groupby("train_id")
        .agg(
            n_events=("train_id", "size"),
            start_variation=("timetable_variation", "first"),
            end_variation=("timetable_variation", "last"),
            max_variation=("timetable_variation", "max"),
            min_variation=("timetable_variation", "min"),
            first_actual_time=("actual_time_utc", "first"),
            last_actual_time=("actual_time_utc", "last"),
            toc_id=("toc_id", "first"),
            train_service_code=("train_service_code", "first"),
        )
        .reset_index()
    )

    change_stats = (
        work.groupby("train_id")["variation_change"]
        .agg(
            max_single_increase="max",
            max_single_decrease="min",
        )
        .reset_index()
    )

    direction_counts = (
        work.assign(
            increased=(work["variation_change"] > 0).astype(int),
            decreased=(work["variation_change"] < 0).astype(int),
            unchanged=(work["variation_change"] == 0).astype(int),
        )
        .groupby("train_id")
        .agg(
            n_increases=("increased", "sum"),
            n_decreases=("decreased", "sum"),
            n_same=("unchanged", "sum"),
        )
        .reset_index()
    )

    summary = summary.merge(change_stats, on="train_id", how="left")
    summary = summary.merge(direction_counts, on="train_id", how="left")

    summary["net_change"] = summary["end_variation"] - summary["start_variation"]
    summary["variation_range"] = summary["max_variation"] - summary["min_variation"]

    summary["case_type"] = summary.apply(classify_case, axis=1)

    # Optional scoring columns for ranking within each class
    summary["sudden_score"] = (
        summary["max_single_increase"].fillna(0)
        + summary["net_change"].clip(lower=0).fillna(0)
    )

    summary["propagation_score"] = (
        summary["net_change"].clip(lower=0).fillna(0)
        + 0.5 * summary["n_increases"].fillna(0)
        - 0.2 * summary["max_single_increase"].fillna(0)
    )

    return summary


def get_train_sequence(train_id: str) -> pd.DataFrame:
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
      AND timetable_variation IS NOT NULL
    ORDER BY actual_time_utc;
    """

    df = pd.read_sql_query(query, conn, params=(train_id,))
    conn.close()

    if not df.empty:
        df["previous_variation"] = df["timetable_variation"].shift(1)
        df["variation_change"] = df["timetable_variation"] - df["previous_variation"]

    return df


def print_top_cases(summary: pd.DataFrame, case_type: str, score_col: str, top_n: int = 10) -> None:
    subset = summary[summary["case_type"] == case_type].copy()

    if subset.empty:
        print(f"\nNo cases found for {case_type}.")
        return

    subset = subset.sort_values(by=score_col, ascending=False)

    print(f"\nTop {top_n} {case_type} cases:")
    print(subset.head(top_n))


def main():
    min_events = 5

    events = load_train_event_sequences(min_events=min_events)

    if events.empty:
        print("No train movement events found matching the criteria.")
        return

    summary = compute_train_metrics(events)

    print("\nCase type counts:")
    print(summary["case_type"].value_counts())

    print_top_cases(summary, case_type="SUDDEN_DISRUPTION", score_col="sudden_score", top_n=10)
    print_top_cases(summary, case_type="PROPAGATION", score_col="propagation_score", top_n=10)
    print_top_cases(summary, case_type="MIXED", score_col="propagation_score", top_n=10)

    # Show detailed sequence for the top propagation case
    propagation_cases = summary[summary["case_type"] == "PROPAGATION"].sort_values(
        by="propagation_score", ascending=False
    )

    if not propagation_cases.empty:
        top_prop_train = propagation_cases.iloc[0]["train_id"]
        print(f"\nDetailed sequence for top PROPAGATION case: {top_prop_train}")
        print(get_train_sequence(top_prop_train))

    sudden_cases = summary[summary["case_type"] == "SUDDEN_DISRUPTION"].sort_values(
        by="sudden_score", ascending=False
    )

    if not sudden_cases.empty:
        top_sudden_train = sudden_cases.iloc[0]["train_id"]
        print(f"\nDetailed sequence for top SUDDEN_DISRUPTION case: {top_sudden_train}")
        print(get_train_sequence(top_sudden_train))


if __name__ == "__main__":
    main()