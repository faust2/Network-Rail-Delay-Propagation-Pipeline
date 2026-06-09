import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

DB_PATH = "data/railway.db"
OUTPUT_DIR = Path("outputs/figures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_top_locations(top_n: int = 15) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    query = f"""
    SELECT
        location_name,
        location_causal_score,
        n_causal_pairs,
        mean_post_worsening,
        n_affected
    FROM causal_propagation_locations
    ORDER BY location_causal_score DESC
    LIMIT {top_n};
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def main() -> None:
    top_n = 15
    df = load_top_locations(top_n=top_n)

    if df.empty:
        print("No causal propagation locations found.")
        return

    df = df.sort_values("location_causal_score", ascending=True)

    plt.figure(figsize=(11, 8))
    plt.barh(df["location_name"], df["location_causal_score"])
    plt.xlabel("Location causal score")
    plt.ylabel("Location")
    plt.title("Top Causal Propagation Locations")
    plt.tight_layout()

    output_path = OUTPUT_DIR / "top_causal_locations_bar_chart.png"
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    print(f"Saved figure to: {output_path}")


if __name__ == "__main__":
    main()