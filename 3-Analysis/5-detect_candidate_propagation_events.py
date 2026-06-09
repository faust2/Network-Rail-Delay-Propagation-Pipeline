import sqlite3
import pandas as pd

DB_PATH = "data/railway.db"


def load_events_for_interaction_analysis(min_location_events: int = 5) -> pd.DataFrame:
    """
    Load enriched train movement events with enough information to look for
    possible delay propagation between trains at the same location.

    We keep only rows with:
    - mapped location_name
    - actual_time_utc
    - timetable_variation
    """
    conn = sqlite3.connect(DB_PATH)

    query = """
    WITH location_counts AS (
        SELECT
            loc_stanox,
            COUNT(*) AS n_events
        FROM train_movements_enriched
        WHERE loc_stanox IS NOT NULL
          AND actual_time_utc IS NOT NULL
          AND timetable_variation IS NOT NULL
        GROUP BY loc_stanox
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
        lc.n_events AS location_event_count
    FROM train_movements_enriched tme
    JOIN location_counts lc
      ON tme.loc_stanox = lc.loc_stanox
    WHERE lc.n_events >= ?
      AND tme.loc_stanox IS NOT NULL
      AND tme.location_name IS NOT NULL
      AND tme.actual_time_utc IS NOT NULL
      AND tme.timetable_variation IS NOT NULL
    ORDER BY tme.loc_stanox, tme.actual_time_utc;
    """

    df = pd.read_sql_query(query, conn, params=(min_location_events,))
    conn.close()

    if not df.empty:
        df["actual_time_utc"] = pd.to_datetime(df["actual_time_utc"], errors="coerce")
        df["planned_time_utc"] = pd.to_datetime(df["planned_time_utc"], errors="coerce")

    return df


def build_same_location_pairs(
    df: pd.DataFrame,
    max_gap_minutes: int = 10,
    require_same_direction: bool = True,
) -> pd.DataFrame:
    """
    Build candidate pairs of trains at the same location and close in time.

    Idea:
    - earlier train = potential source of delay
    - later train = potential affected train

    This is only a candidate interaction table, not proof of causality.
    """
    if df.empty:
        return pd.DataFrame()

    rows = []

    for loc_stanox, g in df.groupby("loc_stanox"):
        g = g.sort_values("actual_time_utc").reset_index(drop=True)

        for i in range(len(g) - 1):
            row_i = g.iloc[i]

            for j in range(i + 1, len(g)):
                row_j = g.iloc[j]

                time_gap = (row_j["actual_time_utc"] - row_i["actual_time_utc"]).total_seconds() / 60.0

                if time_gap < 0:
                    continue

                if time_gap > max_gap_minutes:
                    break

                if row_i["train_id"] == row_j["train_id"]:
                    continue

                if require_same_direction and row_i["direction_ind"] != row_j["direction_ind"]:
                    continue

                candidate = {
                    "location_name": row_i["location_name"],
                    "loc_stanox": row_i["loc_stanox"],
                    "direction_ind": row_i["direction_ind"],

                    "source_train_id": row_i["train_id"],
                    "source_event_type": row_i["event_type"],
                    "source_actual_time": row_i["actual_time_utc"],
                    "source_variation": row_i["timetable_variation"],
                    "source_variation_status": row_i["variation_status"],

                    "affected_train_id": row_j["train_id"],
                    "affected_event_type": row_j["event_type"],
                    "affected_actual_time": row_j["actual_time_utc"],
                    "affected_variation": row_j["timetable_variation"],
                    "affected_variation_status": row_j["variation_status"],

                    "time_gap_minutes": time_gap,
                    "variation_difference": row_j["timetable_variation"] - row_i["timetable_variation"],
                }

                rows.append(candidate)

    return pd.DataFrame(rows)


def classify_candidate_pairs(
    pairs: pd.DataFrame,
    source_delay_threshold: int = 5,
    affected_delay_threshold: int = 5,
    propagation_gap_threshold: int = 10,
) -> pd.DataFrame:
    """
    Add a simple candidate-propagation classification.

    Heuristic idea:
    candidate if:
    - source train is meaningfully delayed
    - affected train is also meaningfully delayed
    - they are close in time at the same location
    - affected train is at least as delayed, or more delayed

    This is still not causal proof.
    """
    if pairs.empty:
        return pairs.copy()

    out = pairs.copy()

    out["source_is_delayed"] = (out["source_variation"] >= source_delay_threshold).astype(int)
    out["affected_is_delayed"] = (out["affected_variation"] >= affected_delay_threshold).astype(int)
    out["close_in_time"] = (out["time_gap_minutes"] <= propagation_gap_threshold).astype(int)
    out["affected_not_better"] = (out["variation_difference"] >= 0).astype(int)

    out["candidate_propagation"] = (
        (out["source_is_delayed"] == 1) &
        (out["affected_is_delayed"] == 1) &
        (out["close_in_time"] == 1) &
        (out["affected_not_better"] == 1)
    ).astype(int)

    # A simple score: higher if source is late, affected is late, and the time gap is short
    out["candidate_score"] = (
        out["source_variation"].clip(lower=0)
        + out["affected_variation"].clip(lower=0)
        - 0.5 * out["time_gap_minutes"]
        + 2 * out["candidate_propagation"]
    )

    return out


def summarise_candidate_locations(pairs: pd.DataFrame, min_pairs: int = 3) -> pd.DataFrame:
    """
    Summarise which locations most often appear in candidate propagation pairs.
    """
    if pairs.empty:
        return pd.DataFrame()

    work = pairs[pairs["candidate_propagation"] == 1].copy()

    if work.empty:
        return pd.DataFrame()

    summary = (
        work.groupby(["loc_stanox", "location_name"])
        .agg(
            n_candidate_pairs=("candidate_propagation", "size"),
            n_unique_source_trains=("source_train_id", "nunique"),
            n_unique_affected_trains=("affected_train_id", "nunique"),
            mean_time_gap=("time_gap_minutes", "mean"),
            mean_source_variation=("source_variation", "mean"),
            mean_affected_variation=("affected_variation", "mean"),
            max_candidate_score=("candidate_score", "max"),
        )
        .reset_index()
    )

    summary = summary[summary["n_candidate_pairs"] >= min_pairs].copy()

    summary["propagation_location_score"] = (
        summary["n_candidate_pairs"]
        + 0.5 * summary["n_unique_affected_trains"]
        + 0.2 * summary["mean_affected_variation"]
        - 0.2 * summary["mean_time_gap"]
    )

    return summary.sort_values(
        by=["propagation_location_score", "n_candidate_pairs", "mean_affected_variation"],
        ascending=[False, False, False]
    ).reset_index(drop=True)


def print_top_candidate_pairs(pairs: pd.DataFrame, top_n: int = 20) -> None:
    if pairs.empty:
        print("No candidate train pairs found.")
        return

    subset = pairs[pairs["candidate_propagation"] == 1].copy()

    if subset.empty:
        print("No candidate propagation pairs found under the current thresholds.")
        return

    subset = subset.sort_values(
        by=["candidate_score", "time_gap_minutes"],
        ascending=[False, True]
    )

    cols = [
        "location_name",
        "direction_ind",
        "source_train_id",
        "source_event_type",
        "source_actual_time",
        "source_variation",
        "affected_train_id",
        "affected_event_type",
        "affected_actual_time",
        "affected_variation",
        "time_gap_minutes",
        "variation_difference",
        "candidate_score",
    ]

    print(f"\nTop {top_n} candidate propagation pairs:")
    print(subset[cols].head(top_n))


def print_top_candidate_locations(summary: pd.DataFrame, top_n: int = 20) -> None:
    if summary.empty:
        print("No candidate propagation locations found.")
        return

    print(f"\nTop {top_n} candidate propagation locations:")
    print(summary.head(top_n))


def main():
    min_location_events = 5
    max_gap_minutes = 10
    require_same_direction = True

    events = load_events_for_interaction_analysis(min_location_events=min_location_events)

    if events.empty:
        print("No suitable enriched movement events found.")
        return

    pairs = build_same_location_pairs(
        events,
        max_gap_minutes=max_gap_minutes,
        require_same_direction=require_same_direction,
    )

    pairs = classify_candidate_pairs(
        pairs,
        source_delay_threshold=5,
        affected_delay_threshold=5,
        propagation_gap_threshold=max_gap_minutes,
    )

    summary = summarise_candidate_locations(pairs, min_pairs=3)

    print("\nCandidate pair summary:")
    print(f"Total same-location close-time pairs: {len(pairs)}")
    print(f"Candidate propagation pairs: {(pairs['candidate_propagation'] == 1).sum()}")

    print_top_candidate_pairs(pairs, top_n=20)
    print_top_candidate_locations(summary, top_n=20)


if __name__ == "__main__":
    main()