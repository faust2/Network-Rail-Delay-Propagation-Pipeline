import sqlite3
import pandas as pd

DB_PATH = "data/railway.db"


def load_enriched_events(min_events_per_train: int = 5) -> pd.DataFrame:
    """
    Load movement events with mapped location names.
    Only keep trains with at least `min_events_per_train` recorded events,
    so the delay-change calculations are based on non-trivial journeys.
    """
    conn = sqlite3.connect(DB_PATH)

    query = """
    WITH train_counts AS (
        SELECT
            train_id,
            COUNT(*) AS n_events
        FROM train_movements_enriched
        WHERE train_id IS NOT NULL
          AND actual_time_utc IS NOT NULL
          AND timetable_variation IS NOT NULL
        GROUP BY train_id
    )
    SELECT
        tme.train_id,
        tme.event_type,
        tme.loc_stanox,
        tme.location_name,
        tme.actual_time_utc,
        tme.planned_time_utc,
        tme.timetable_variation,
        tme.variation_status,
        tme.platform,
        tme.direction_ind,
        tme.toc_id,
        tc.n_events
    FROM train_movements_enriched tme
    JOIN train_counts tc
      ON tme.train_id = tc.train_id
    WHERE tc.n_events >= ?
      AND tme.actual_time_utc IS NOT NULL
      AND tme.timetable_variation IS NOT NULL
    ORDER BY tme.train_id, tme.actual_time_utc;
    """

    df = pd.read_sql_query(query, conn, params=(min_events_per_train,))
    conn.close()
    return df


def add_delay_dynamics(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each train, compute how timetable variation changes from one event to the next.
    """
    if df.empty:
        return df.copy()

    out = df.copy()
    out["previous_variation"] = out.groupby("train_id")["timetable_variation"].shift(1)
    out["variation_change"] = out["timetable_variation"] - out["previous_variation"]

    out["is_increase"] = (out["variation_change"] > 0).astype(int)
    out["is_decrease"] = (out["variation_change"] < 0).astype(int)
    out["is_unchanged"] = (out["variation_change"] == 0).astype(int)

    return out


def build_location_summary(df: pd.DataFrame, min_location_events: int = 10) -> pd.DataFrame:
    """
    Summarise delay dynamics at each mapped location.

    Metrics:
    - total events
    - increase/decrease counts
    - mean timetable variation
    - mean variation change
    - mean positive jump
    - probability of delay increase
    """
    if df.empty:
        return pd.DataFrame()

    work = df.copy()

    # Keep only rows with a mapped readable location
    work = work[work["location_name"].notna()].copy()

    # Exclude first row of each train where variation_change is NaN
    work = work[work["variation_change"].notna()].copy()

    if work.empty:
        return pd.DataFrame()

    work["positive_jump"] = work["variation_change"].where(work["variation_change"] > 0, 0)
    work["negative_jump"] = work["variation_change"].where(work["variation_change"] < 0, 0)

    summary = (
        work.groupby(["loc_stanox", "location_name"])
        .agg(
            n_events=("train_id", "size"),
            n_unique_trains=("train_id", "nunique"),
            n_increases=("is_increase", "sum"),
            n_decreases=("is_decrease", "sum"),
            n_unchanged=("is_unchanged", "sum"),
            mean_variation=("timetable_variation", "mean"),
            mean_variation_change=("variation_change", "mean"),
            mean_positive_jump=("positive_jump", "mean"),
            max_positive_jump=("variation_change", "max"),
            mean_negative_jump=("negative_jump", "mean"),
        )
        .reset_index()
    )

    summary["pct_increase"] = summary["n_increases"] / summary["n_events"]
    summary["pct_decrease"] = summary["n_decreases"] / summary["n_events"]
    summary["pct_unchanged"] = summary["n_unchanged"] / summary["n_events"]

    # A simple hotspot score:
    # reward frequent increases, larger average positive jumps, and broad train coverage
    summary["delay_hotspot_score"] = (
        10 * summary["pct_increase"]
        + summary["mean_positive_jump"]
        + 0.1 * summary["n_unique_trains"]
    )

    summary = summary[summary["n_events"] >= min_location_events].copy()

    return summary.sort_values(
        by=["delay_hotspot_score", "pct_increase", "mean_positive_jump", "n_events"],
        ascending=[False, False, False, False]
    ).reset_index(drop=True)


def print_top_hotspots(summary: pd.DataFrame, top_n: int = 20) -> None:
    if summary.empty:
        print("No location summary available.")
        return

    cols = [
        "loc_stanox",
        "location_name",
        "n_events",
        "n_unique_trains",
        "n_increases",
        "pct_increase",
        "mean_variation",
        "mean_variation_change",
        "mean_positive_jump",
        "max_positive_jump",
        "delay_hotspot_score",
    ]

    print(f"\nTop {top_n} delay hotspot locations:")
    print(summary[cols].head(top_n))


def print_top_recovery_locations(summary: pd.DataFrame, top_n: int = 20) -> None:
    if summary.empty:
        print("No location summary available.")
        return

    recovery = summary.sort_values(
        by=["pct_decrease", "mean_variation_change"],
        ascending=[False, True]
    ).copy()

    cols = [
        "loc_stanox",
        "location_name",
        "n_events",
        "n_unique_trains",
        "n_decreases",
        "pct_decrease",
        "mean_variation",
        "mean_variation_change",
    ]

    print(f"\nTop {top_n} recovery locations:")
    print(recovery[cols].head(top_n))


def print_sample_increase_events(df: pd.DataFrame, location_name: str, top_n: int = 10) -> None:
    """
    Show sample events at one location where delay increased.
    """
    sample = df[
        (df["location_name"] == location_name) &
        (df["variation_change"] > 0)
    ].copy()

    if sample.empty:
        print(f"\nNo positive delay-change events found for {location_name}.")
        return

    cols = [
        "train_id",
        "event_type",
        "loc_stanox",
        "location_name",
        "planned_time_utc",
        "actual_time_utc",
        "previous_variation",
        "timetable_variation",
        "variation_change",
        "variation_status",
        "platform",
    ]

    print(f"\nSample positive delay-change events at {location_name}:")
    print(sample[cols].head(top_n))


def main():
    min_events_per_train = 5
    min_location_events = 10
    top_n = 20

    events = load_enriched_events(min_events_per_train=min_events_per_train)

    if events.empty:
        print("No enriched train movement events found.")
        return

    events = add_delay_dynamics(events)
    summary = build_location_summary(events, min_location_events=min_location_events)

    print_top_hotspots(summary, top_n=top_n)
    print_top_recovery_locations(summary, top_n=top_n)

    if not summary.empty:
        top_location = summary.iloc[0]["location_name"]
        print_sample_increase_events(events, location_name=top_location, top_n=10)


if __name__ == "__main__":
    main()