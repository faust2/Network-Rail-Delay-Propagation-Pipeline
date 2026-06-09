import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = "data/railway.db"
OUTPUT_DIR = Path("outputs/tables")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_PATH = OUTPUT_DIR / "validation_control_comparison.csv"


def load_events() -> pd.DataFrame:
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
      AND loc_stanox IS NOT NULL
      AND location_name IS NOT NULL
      AND actual_time_utc IS NOT NULL
      AND timetable_variation IS NOT NULL
    ORDER BY train_id, actual_time_utc;
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    df["actual_time_utc"] = pd.to_datetime(df["actual_time_utc"], errors="coerce")
    df["planned_time_utc"] = pd.to_datetime(df["planned_time_utc"], errors="coerce")

    return df.dropna(subset=["actual_time_utc"])


def add_train_sequence_context(events: pd.DataFrame) -> pd.DataFrame:
    df = events.sort_values(["train_id", "actual_time_utc"]).copy()

    df["previous_variation"] = df.groupby("train_id")["timetable_variation"].shift(1)
    df["next_variation"] = df.groupby("train_id")["timetable_variation"].shift(-1)

    df["pre_change"] = df["timetable_variation"] - df["previous_variation"]
    df["post_change"] = df["next_variation"] - df["timetable_variation"]

    df["post_worsened"] = (df["post_change"] > 0).astype(int)

    return df


def load_high_confidence_cases() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)

    query = """
    SELECT
        source_train_id,
        affected_train_id,
        sample_location,
        edge_weight,
        mean_time_gap,
        mean_post_worsening,
        mean_incremental_worsening
    FROM causal_propagation_edges
    ORDER BY edge_weight DESC;
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    return df


def find_case_interaction_row(
    events: pd.DataFrame,
    source_train_id: str,
    affected_train_id: str,
    location_name: str,
    max_gap_minutes: float,
) -> dict | None:
    source_rows = events[
        (events["train_id"] == source_train_id)
        & (events["location_name"] == location_name)
    ].copy()

    affected_rows = events[
        (events["train_id"] == affected_train_id)
        & (events["location_name"] == location_name)
    ].copy()

    if source_rows.empty or affected_rows.empty:
        return None

    best = None

    for _, src in source_rows.iterrows():
        for _, aft in affected_rows.iterrows():
            gap = (aft["actual_time_utc"] - src["actual_time_utc"]).total_seconds() / 60.0

            if gap <= 0 or gap > max_gap_minutes:
                continue

            if src["direction_ind"] != aft["direction_ind"]:
                continue

            candidate = {
                "source_train_id": source_train_id,
                "affected_train_id": affected_train_id,
                "location_name": location_name,
                "loc_stanox": aft["loc_stanox"],
                "direction_ind": aft["direction_ind"],
                "source_time": src["actual_time_utc"],
                "affected_time": aft["actual_time_utc"],
                "time_gap_minutes": gap,
                "source_variation": src["timetable_variation"],
                "affected_variation": aft["timetable_variation"],
                "affected_pre_change": aft["pre_change"],
                "affected_post_change": aft["post_change"],
                "affected_post_worsened": aft["post_worsened"],
            }

            if best is None or candidate["time_gap_minutes"] < best["time_gap_minutes"]:
                best = candidate

    return best


def build_control_pool(
    events: pd.DataFrame,
    case: dict,
    control_window_minutes: float = 30,
    candidate_gap_minutes: float = 10,
) -> pd.DataFrame:
    """
    Controls are trains at the same location/direction near the case time,
    excluding the source/affected trains, that did NOT closely follow
    a delayed source train under the same candidate rule.

    This is a public-data control, not proof of operational non-interaction.
    """
    location = case["location_name"]
    direction = case["direction_ind"]
    case_time = case["affected_time"]
    source_train = case["source_train_id"]
    affected_train = case["affected_train_id"]

    start = case_time - pd.Timedelta(minutes=control_window_minutes)
    end = case_time + pd.Timedelta(minutes=control_window_minutes)

    local = events[
        (events["location_name"] == location)
        & (events["direction_ind"] == direction)
        & (events["actual_time_utc"] >= start)
        & (events["actual_time_utc"] <= end)
        & (~events["train_id"].isin([source_train, affected_train]))
    ].copy()

    if local.empty:
        return local

    # Remove events that themselves closely followed a delayed train.
    local_sorted = events[
        (events["location_name"] == location)
        & (events["direction_ind"] == direction)
        & (events["actual_time_utc"] >= start - pd.Timedelta(minutes=candidate_gap_minutes))
        & (events["actual_time_utc"] <= end)
    ].sort_values("actual_time_utc").copy()

    control_rows = []

    for _, row in local.iterrows():
        prior = local_sorted[
            (local_sorted["actual_time_utc"] < row["actual_time_utc"])
            & (local_sorted["actual_time_utc"] >= row["actual_time_utc"] - pd.Timedelta(minutes=candidate_gap_minutes))
            & (local_sorted["train_id"] != row["train_id"])
            & (local_sorted["timetable_variation"] >= 5)
        ]

        if prior.empty:
            control_rows.append(row)

    if not control_rows:
        return pd.DataFrame(columns=local.columns)

    return pd.DataFrame(control_rows)


def validate_against_controls(
    events: pd.DataFrame,
    causal_edges: pd.DataFrame,
    max_cases: int = 50,
    max_gap_minutes: float = 10,
    control_window_minutes: float = 30,
) -> pd.DataFrame:
    rows = []

    for _, edge in causal_edges.head(max_cases).iterrows():
        case = find_case_interaction_row(
            events=events,
            source_train_id=edge["source_train_id"],
            affected_train_id=edge["affected_train_id"],
            location_name=edge["sample_location"],
            max_gap_minutes=max_gap_minutes,
        )

        if case is None:
            continue

        controls = build_control_pool(
            events=events,
            case=case,
            control_window_minutes=control_window_minutes,
            candidate_gap_minutes=max_gap_minutes,
        )

        n_controls = len(controls)

        if n_controls > 0:
            control_post_worsening_rate = controls["post_worsened"].mean()
            control_mean_post_change = controls["post_change"].mean()
        else:
            control_post_worsening_rate = None
            control_mean_post_change = None

        candidate_post_worsening = int(case["affected_post_worsened"])
        candidate_post_change = case["affected_post_change"]

        if n_controls == 0:
            control_result = "NO_CONTROL_AVAILABLE"
        elif candidate_post_worsening == 1 and candidate_post_change > control_mean_post_change:
            control_result = "SUPPORTS_CASE"
        elif candidate_post_worsening == 1:
            control_result = "PARTIAL_SUPPORT"
        else:
            control_result = "DOES_NOT_SUPPORT"

        rows.append(
            {
                "source_train_id": case["source_train_id"],
                "affected_train_id": case["affected_train_id"],
                "location_name": case["location_name"],
                "loc_stanox": case["loc_stanox"],
                "direction_ind": case["direction_ind"],
                "source_time": case["source_time"],
                "affected_time": case["affected_time"],
                "time_gap_minutes": case["time_gap_minutes"],
                "source_variation": case["source_variation"],
                "affected_variation": case["affected_variation"],
                "affected_pre_change": case["affected_pre_change"],
                "affected_post_change": case["affected_post_change"],
                "candidate_post_worsening": candidate_post_worsening,
                "n_controls": n_controls,
                "control_post_worsening_rate": control_post_worsening_rate,
                "control_mean_post_change": control_mean_post_change,
                "control_result": control_result,
                "edge_weight": edge["edge_weight"],
                "mean_edge_post_worsening": edge["mean_post_worsening"],
                "mean_edge_incremental_worsening": edge["mean_incremental_worsening"],
            }
        )

    return pd.DataFrame(rows)


def summarise_results(results: pd.DataFrame) -> None:
    if results.empty:
        print("No validation-control results produced.")
        return

    print("\nValidation-control summary:")
    print(f"Cases evaluated: {len(results)}")

    print("\nControl result counts:")
    print(results["control_result"].value_counts())

    print("\nTop cases by edge weight:")
    cols = [
        "source_train_id",
        "affected_train_id",
        "location_name",
        "time_gap_minutes",
        "source_variation",
        "affected_variation",
        "affected_post_change",
        "n_controls",
        "control_post_worsening_rate",
        "control_mean_post_change",
        "control_result",
        "edge_weight",
    ]
    print(results[cols].sort_values("edge_weight", ascending=False).head(20))


def main() -> None:
    max_cases = 50
    max_gap_minutes = 10
    control_window_minutes = 30

    events = load_events()
    events = add_train_sequence_context(events)

    causal_edges = load_high_confidence_cases()

    if causal_edges.empty:
        print("No causal propagation edges found. Run build_causal_propagation_network.py first.")
        return

    results = validate_against_controls(
        events=events,
        causal_edges=causal_edges,
        max_cases=max_cases,
        max_gap_minutes=max_gap_minutes,
        control_window_minutes=control_window_minutes,
    )

    if results.empty:
        print("No cases could be matched to interaction events.")
        return

    results.to_csv(OUTPUT_PATH, index=False)
    summarise_results(results)

    print(f"\nSaved validation-control table to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()