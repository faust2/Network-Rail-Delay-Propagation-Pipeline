import sqlite3
import pandas as pd

DB_PATH = "data/railway.db"


def load_enriched_events() -> pd.DataFrame:
    """
    Load enriched movement events with readable location names.
    """
    conn = sqlite3.connect(DB_PATH)

    query = """
    SELECT
        train_id,
        event_type,
        loc_stanox,
        location_name,
        actual_time_utc,
        planned_time_utc,
        timetable_variation,
        variation_status,
        platform,
        direction_ind,
        toc_id
    FROM train_movements_enriched
    WHERE train_id IS NOT NULL
      AND location_name IS NOT NULL
      AND actual_time_utc IS NOT NULL
      AND timetable_variation IS NOT NULL
    ORDER BY train_id, actual_time_utc;
    """

    df = pd.read_sql_query(query, conn)
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
    Build candidate source/affected train pairs at the same location and close in time.
    """
    if df.empty:
        return pd.DataFrame()

    rows = []

    for loc_stanox, g in df.groupby("loc_stanox"):
        g = g.sort_values("actual_time_utc").reset_index(drop=True)

        for i in range(len(g) - 1):
            src = g.iloc[i]

            for j in range(i + 1, len(g)):
                aft = g.iloc[j]

                gap = (aft["actual_time_utc"] - src["actual_time_utc"]).total_seconds() / 60.0

                if gap < 0:
                    continue
                if gap > max_gap_minutes:
                    break
                if src["train_id"] == aft["train_id"]:
                    continue
                if require_same_direction and src["direction_ind"] != aft["direction_ind"]:
                    continue

                rows.append(
                    {
                        "location_name": src["location_name"],
                        "loc_stanox": src["loc_stanox"],
                        "direction_ind": src["direction_ind"],
                        "source_train_id": src["train_id"],
                        "source_event_type": src["event_type"],
                        "source_actual_time": src["actual_time_utc"],
                        "source_variation": src["timetable_variation"],
                        "affected_train_id": aft["train_id"],
                        "affected_event_type": aft["event_type"],
                        "affected_actual_time": aft["actual_time_utc"],
                        "affected_variation_at_interaction": aft["timetable_variation"],
                        "time_gap_minutes": gap,
                    }
                )

    return pd.DataFrame(rows)


def classify_candidate_pairs(
    pairs: pd.DataFrame,
    source_delay_threshold: int = 5,
    affected_delay_threshold: int = 5,
) -> pd.DataFrame:
    """
    Keep a simple candidate-propagation flag based on same-location delay co-occurrence.
    """
    if pairs.empty:
        return pairs.copy()

    out = pairs.copy()

    out["candidate_propagation"] = (
        (out["source_variation"] >= source_delay_threshold)
        & (out["affected_variation_at_interaction"] >= affected_delay_threshold)
    ).astype(int)

    out["candidate_score"] = (
        out["source_variation"].clip(lower=0)
        + out["affected_variation_at_interaction"].clip(lower=0)
        - 0.5 * out["time_gap_minutes"]
        + 2 * out["candidate_propagation"]
    )

    return out


def build_train_sequences(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Build per-train ordered event sequences with next-event variation info.
    """
    sequences = {}

    for train_id, g in df.groupby("train_id"):
        g = g.sort_values("actual_time_utc").reset_index(drop=True).copy()
        g["next_variation"] = g["timetable_variation"].shift(-1)
        g["next_location_name"] = g["location_name"].shift(-1)
        g["next_actual_time"] = g["actual_time_utc"].shift(-1)
        g["post_interaction_increase"] = g["next_variation"] - g["timetable_variation"]
        sequences[train_id] = g

    return sequences


def attach_post_interaction_effect(
    pairs: pd.DataFrame,
    sequences: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    For each candidate pair, inspect the affected train's next recorded event
    after the shared-location interaction.

    This is the key step:
    - if the affected train gets more delayed AFTER the interaction point,
      that is stronger evidence of propagation-like behaviour.
    """
    if pairs.empty:
        return pairs.copy()

    rows = []

    for _, pair in pairs.iterrows():
        affected_train_id = pair["affected_train_id"]
        affected_time = pair["affected_actual_time"]
        loc_stanox = pair["loc_stanox"]

        seq = sequences.get(affected_train_id)
        if seq is None or seq.empty:
            continue

        # Match the specific interaction event for the affected train:
        match = seq[
            (seq["actual_time_utc"] == affected_time)
            & (seq["loc_stanox"] == loc_stanox)
        ].copy()

        if match.empty:
            continue

        # If multiple rows match, take the first.
        row = match.iloc[0]

        out = pair.to_dict()
        out["affected_next_actual_time"] = row.get("next_actual_time")
        out["affected_next_location_name"] = row.get("next_location_name")
        out["affected_next_variation"] = row.get("next_variation")
        out["affected_post_increase"] = row.get("post_interaction_increase")

        # Boolean flags
        post_inc = row.get("post_interaction_increase")
        if pd.isna(post_inc):
            out["affected_delay_increases_after_interaction"] = 0
        else:
            out["affected_delay_increases_after_interaction"] = int(post_inc > 0)

        rows.append(out)

    return pd.DataFrame(rows)


def summarise_post_interaction_results(df: pd.DataFrame) -> pd.DataFrame:
    """
    Summarise by location how often affected trains get worse AFTER the interaction point.
    """
    if df.empty:
        return pd.DataFrame()

    work = df[df["candidate_propagation"] == 1].copy()
    if work.empty:
        return pd.DataFrame()

    summary = (
        work.groupby(["loc_stanox", "location_name"])
        .agg(
            n_candidate_pairs=("candidate_propagation", "size"),
            n_post_increase=("affected_delay_increases_after_interaction", "sum"),
            mean_source_variation=("source_variation", "mean"),
            mean_affected_variation_at_interaction=("affected_variation_at_interaction", "mean"),
            mean_post_increase=("affected_post_increase", "mean"),
            mean_time_gap=("time_gap_minutes", "mean"),
            n_unique_affected_trains=("affected_train_id", "nunique"),
        )
        .reset_index()
    )

    summary["pct_post_increase"] = summary["n_post_increase"] / summary["n_candidate_pairs"]

    summary["causal_signal_score"] = (
        10 * summary["pct_post_increase"]
        + summary["mean_post_increase"].fillna(0)
        + 0.3 * summary["n_unique_affected_trains"]
        - 0.2 * summary["mean_time_gap"]
    )

    return summary.sort_values(
        by=["causal_signal_score", "pct_post_increase", "mean_post_increase"],
        ascending=[False, False, False]
    ).reset_index(drop=True)


def print_top_pairs(df: pd.DataFrame, top_n: int = 20) -> None:
    if df.empty:
        print("No post-interaction candidate rows found.")
        return

    work = df[
        (df["candidate_propagation"] == 1)
        & (df["affected_delay_increases_after_interaction"] == 1)
    ].copy()

    if work.empty:
        print("No candidate pairs where the affected train got worse afterward.")
        return

    work = work.sort_values(
        by=["affected_post_increase", "candidate_score"],
        ascending=[False, False]
    )

    cols = [
        "location_name",
        "direction_ind",
        "source_train_id",
        "source_variation",
        "affected_train_id",
        "affected_variation_at_interaction",
        "affected_next_location_name",
        "affected_next_variation",
        "affected_post_increase",
        "time_gap_minutes",
        "candidate_score",
    ]

    print(f"\nTop {top_n} candidate pairs with post-interaction worsening:")
    print(work[cols].head(top_n))


def print_top_locations(summary: pd.DataFrame, top_n: int = 20) -> None:
    if summary.empty:
        print("No location summary available.")
        return

    print(f"\nTop {top_n} locations by post-interaction worsening signal:")
    print(summary.head(top_n))


def main():
    max_gap_minutes = 10
    require_same_direction = True

    events = load_enriched_events()
    if events.empty:
        print("No enriched movement events found.")
        return

    pairs = build_same_location_pairs(
        events,
        max_gap_minutes=max_gap_minutes,
        require_same_direction=require_same_direction,
    )

    if pairs.empty:
        print("No same-location close-time pairs found.")
        return

    pairs = classify_candidate_pairs(
        pairs,
        source_delay_threshold=5,
        affected_delay_threshold=5,
    )

    sequences = build_train_sequences(events)
    post_df = attach_post_interaction_effect(pairs, sequences)

    if post_df.empty:
        print("No post-interaction rows could be built.")
        return

    summary = summarise_post_interaction_results(post_df)

    total_pairs = len(pairs)
    candidate_pairs = int((pairs["candidate_propagation"] == 1).sum())
    post_increase_pairs = int(
        (
            (post_df["candidate_propagation"] == 1)
            & (post_df["affected_delay_increases_after_interaction"] == 1)
        ).sum()
    )

    print("\nPost-interaction candidate summary:")
    print(f"Total same-location close-time pairs: {total_pairs}")
    print(f"Candidate propagation pairs: {candidate_pairs}")
    print(f"Candidate pairs with post-interaction worsening: {post_increase_pairs}")

    print_top_pairs(post_df, top_n=20)
    print_top_locations(summary, top_n=20)


if __name__ == "__main__":
    main()