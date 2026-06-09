import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = "data/railway.db"
OUTPUT_DIR = Path("outputs/tables")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SENSITIVITY_OUTPUT = OUTPUT_DIR / "validation_threshold_sensitivity.csv"
NEGATIVE_OUTPUT = OUTPUT_DIR / "validation_negative_tests.csv"


def load_events() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)

    query = """
    SELECT
        train_id,
        event_type,
        loc_stanox,
        location_name,
        actual_time_utc,
        timetable_variation,
        direction_ind,
        platform,
        toc_id
    FROM train_movements_enriched
    WHERE train_id IS NOT NULL
      AND loc_stanox IS NOT NULL
      AND location_name IS NOT NULL
      AND actual_time_utc IS NOT NULL
      AND timetable_variation IS NOT NULL;
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    df["actual_time_utc"] = pd.to_datetime(df["actual_time_utc"], errors="coerce")
    df = df.dropna(subset=["actual_time_utc"])

    return df


def add_sequence_context(events: pd.DataFrame) -> pd.DataFrame:
    df = events.sort_values(["train_id", "actual_time_utc"]).copy()

    df["prev_variation"] = df.groupby("train_id")["timetable_variation"].shift(1)
    df["next_variation"] = df.groupby("train_id")["timetable_variation"].shift(-1)

    df["pre_change"] = df["timetable_variation"] - df["prev_variation"]
    df["post_change"] = df["next_variation"] - df["timetable_variation"]
    df["post_worsened"] = (df["post_change"] > 0).astype(int)

    return df


def build_pairs(
    events: pd.DataFrame,
    max_gap_minutes: float,
    delay_threshold: float,
    mode: str = "same_direction",
) -> pd.DataFrame:
    """
    mode options:
    - same_direction: normal validation logic
    - opposite_direction: negative control
    - large_gap: negative control using relaxed gap behaviour
    """
    rows = []

    for loc_stanox, group in events.groupby("loc_stanox"):
        g = group.sort_values("actual_time_utc").reset_index(drop=True)

        for i in range(len(g) - 1):
            src = g.iloc[i]

            for j in range(i + 1, len(g)):
                aft = g.iloc[j]

                gap = (aft["actual_time_utc"] - src["actual_time_utc"]).total_seconds() / 60.0

                if gap <= 0:
                    continue

                if mode != "large_gap" and gap > max_gap_minutes:
                    break

                if mode == "large_gap":
                    if gap <= max_gap_minutes or gap > 3 * max_gap_minutes:
                        continue

                if src["train_id"] == aft["train_id"]:
                    continue

                same_direction = src["direction_ind"] == aft["direction_ind"]

                if mode == "same_direction" and not same_direction:
                    continue

                if mode == "opposite_direction" and same_direction:
                    continue

                if src["timetable_variation"] < delay_threshold:
                    continue

                if aft["timetable_variation"] < delay_threshold:
                    continue

                post_change = aft.get("post_change")
                pre_change = aft.get("pre_change")

                post_worsened = int(pd.notna(post_change) and post_change > 0)
                post_stronger_than_pre = int(
                    pd.notna(post_change)
                    and pd.notna(pre_change)
                    and post_change > pre_change
                )

                rows.append(
                    {
                        "mode": mode,
                        "source_train_id": src["train_id"],
                        "affected_train_id": aft["train_id"],
                        "location_name": src["location_name"],
                        "loc_stanox": src["loc_stanox"],
                        "source_time": src["actual_time_utc"],
                        "affected_time": aft["actual_time_utc"],
                        "time_gap_minutes": gap,
                        "source_variation": src["timetable_variation"],
                        "affected_variation": aft["timetable_variation"],
                        "affected_pre_change": pre_change,
                        "affected_post_change": post_change,
                        "post_worsened": post_worsened,
                        "post_stronger_than_pre": post_stronger_than_pre,
                    }
                )

    return pd.DataFrame(rows)


def summarise_pairs(
    pairs: pd.DataFrame,
    max_gap_minutes: float,
    delay_threshold: float,
    mode: str,
) -> dict:
    if pairs.empty:
        return {
            "mode": mode,
            "max_gap_minutes": max_gap_minutes,
            "delay_threshold": delay_threshold,
            "n_pairs": 0,
            "n_post_worsened": 0,
            "pct_post_worsened": 0.0,
            "n_post_stronger_than_pre": 0,
            "pct_post_stronger_than_pre": 0.0,
            "mean_post_change": None,
            "median_post_change": None,
            "n_unique_locations": 0,
            "n_unique_source_trains": 0,
            "n_unique_affected_trains": 0,
            "top_locations": "",
        }

    top_locations = (
        pairs["location_name"]
        .value_counts()
        .head(5)
        .index
        .tolist()
    )

    return {
        "mode": mode,
        "max_gap_minutes": max_gap_minutes,
        "delay_threshold": delay_threshold,
        "n_pairs": len(pairs),
        "n_post_worsened": int(pairs["post_worsened"].sum()),
        "pct_post_worsened": float(pairs["post_worsened"].mean()),
        "n_post_stronger_than_pre": int(pairs["post_stronger_than_pre"].sum()),
        "pct_post_stronger_than_pre": float(pairs["post_stronger_than_pre"].mean()),
        "mean_post_change": float(pairs["affected_post_change"].mean()),
        "median_post_change": float(pairs["affected_post_change"].median()),
        "n_unique_locations": pairs["location_name"].nunique(),
        "n_unique_source_trains": pairs["source_train_id"].nunique(),
        "n_unique_affected_trains": pairs["affected_train_id"].nunique(),
        "top_locations": "; ".join(top_locations),
    }


def run_threshold_sensitivity(events: pd.DataFrame) -> pd.DataFrame:
    gap_values = [5, 10, 15]
    delay_values = [3, 5, 10]

    rows = []

    for gap in gap_values:
        for delay in delay_values:
            pairs = build_pairs(
                events=events,
                max_gap_minutes=gap,
                delay_threshold=delay,
                mode="same_direction",
            )

            rows.append(
                summarise_pairs(
                    pairs=pairs,
                    max_gap_minutes=gap,
                    delay_threshold=delay,
                    mode="same_direction",
                )
            )

    return pd.DataFrame(rows)


def run_negative_tests(events: pd.DataFrame) -> pd.DataFrame:
    """
    Runs two negative/stress tests:
    1. opposite_direction: should usually be weaker than same-direction logic
    2. large_gap: uses much larger gaps, which should weaken immediate propagation logic
    """
    max_gap_minutes = 10
    delay_threshold = 5

    modes = ["same_direction", "opposite_direction", "large_gap"]
    rows = []

    for mode in modes:
        pairs = build_pairs(
            events=events,
            max_gap_minutes=max_gap_minutes,
            delay_threshold=delay_threshold,
            mode=mode,
        )

        rows.append(
            summarise_pairs(
                pairs=pairs,
                max_gap_minutes=max_gap_minutes,
                delay_threshold=delay_threshold,
                mode=mode,
            )
        )

    return pd.DataFrame(rows)


def print_summary(sensitivity: pd.DataFrame, negative: pd.DataFrame) -> None:
    print("\nThreshold sensitivity summary:")
    print(
        sensitivity[
            [
                "max_gap_minutes",
                "delay_threshold",
                "n_pairs",
                "pct_post_worsened",
                "pct_post_stronger_than_pre",
                "n_unique_locations",
                "top_locations",
            ]
        ]
    )

    print("\nNegative/control stress-test summary:")
    print(
        negative[
            [
                "mode",
                "n_pairs",
                "pct_post_worsened",
                "pct_post_stronger_than_pre",
                "mean_post_change",
                "n_unique_locations",
                "top_locations",
            ]
        ]
    )


def main() -> None:
    events = load_events()
    events = add_sequence_context(events)

    sensitivity = run_threshold_sensitivity(events)
    negative = run_negative_tests(events)

    sensitivity.to_csv(SENSITIVITY_OUTPUT, index=False)
    negative.to_csv(NEGATIVE_OUTPUT, index=False)

    print_summary(sensitivity, negative)

    print(f"\nSaved threshold sensitivity table to: {SENSITIVITY_OUTPUT}")
    print(f"Saved negative-control table to: {NEGATIVE_OUTPUT}")


if __name__ == "__main__":
    main()