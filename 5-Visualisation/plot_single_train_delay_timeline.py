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
        timetable_variation,
        variation_status
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


def main() -> None:
    # Replace with a train you want to inspect
    train_id = "815C30M716"

    df = load_train_sequence(train_id)

    if df.empty:
        print(f"No events found for train_id={train_id}")
        return

    x = range(len(df))

    plt.figure(figsize=(12, 6))
    plt.plot(x, df["timetable_variation"], marker="o")

    for i, row in df.iterrows():
        label = f"{row['location_name']} ({row['event_type']})"
        plt.annotate(label, (i, row["timetable_variation"]), fontsize=7, xytext=(0, 6), textcoords="offset points")

    plt.xticks(list(x), [t.strftime("%H:%M") if pd.notna(t) else "" for t in df["actual_time_utc"]], rotation=45)
    plt.xlabel("Actual event time")
    plt.ylabel("Timetable variation (minutes)")
    plt.title(f"Single-Train Delay Timeline: {train_id}")
    plt.tight_layout()

    output_path = OUTPUT_DIR / f"single_train_timeline_{train_id}.png"
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    print(f"Saved figure to: {output_path}")


if __name__ == "__main__":
    main()