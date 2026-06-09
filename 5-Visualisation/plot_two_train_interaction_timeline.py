import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

DB_PATH = "data/railway.db"
OUTPUT_DIR = Path("outputs/figures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_train_sequence(train_id: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT
        train_id,
        event_type,
        location_name,
        actual_time_utc,
        timetable_variation
    FROM train_movements_enriched
    WHERE train_id = ?
      AND actual_time_utc IS NOT NULL
      AND timetable_variation IS NOT NULL
      AND location_name IS NOT NULL
    ORDER BY actual_time_utc;
    """
    df = pd.read_sql_query(query, conn, params=(train_id,))
    conn.close()

    if not df.empty:
        df["actual_time_utc"] = pd.to_datetime(df["actual_time_utc"], errors="coerce")

    return df


def load_top_causal_edge() -> pd.Series | None:
    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT
        source_train_id,
        affected_train_id,
        sample_location
    FROM causal_propagation_edges
    ORDER BY edge_weight DESC
    LIMIT 1;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        return None
    return df.iloc[0]


def main() -> None:
    top_edge = load_top_causal_edge()
    if top_edge is None:
        print("No causal propagation edges found.")
        return

    source_train = top_edge["source_train_id"]
    affected_train = top_edge["affected_train_id"]
    interaction_location = top_edge["sample_location"]

    src = load_train_sequence(source_train)
    aft = load_train_sequence(affected_train)

    if src.empty or aft.empty:
        print("Could not load source/affected train sequences.")
        return

    plt.figure(figsize=(12, 6))
    plt.plot(src["actual_time_utc"], src["timetable_variation"], marker="o", label=f"Source: {source_train}")
    plt.plot(aft["actual_time_utc"], aft["timetable_variation"], marker="o", label=f"Affected: {affected_train}")

    # Mark first occurrence of the interaction location for each train if available
    src_inter = src[src["location_name"] == interaction_location]
    aft_inter = aft[aft["location_name"] == interaction_location]

    if not src_inter.empty:
        t = src_inter.iloc[0]["actual_time_utc"]
        plt.axvline(t, linestyle="--")
    if not aft_inter.empty:
        t = aft_inter.iloc[0]["actual_time_utc"]
        plt.axvline(t, linestyle="--")

    plt.xlabel("Actual event time")
    plt.ylabel("Timetable variation (minutes)")
    plt.title(f"Two-Train Interaction Timeline at {interaction_location}")
    plt.legend()
    plt.tight_layout()

    output_path = OUTPUT_DIR / f"two_train_interaction_{source_train}_{affected_train}.png"
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    print(f"Saved figure to: {output_path}")
    print(f"Source train: {source_train}")
    print(f"Affected train: {affected_train}")
    print(f"Interaction location: {interaction_location}")


if __name__ == "__main__":
    main()