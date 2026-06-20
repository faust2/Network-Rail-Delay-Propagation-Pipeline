import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

DB_PATH = "data/railway.db"
OUTPUT_DIR = Path("outputs/figures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_top_causal_edge() -> pd.Series | None:
    """
    Load the strongest causal propagation edge.

    This identifies the source train that most strongly appears to have
    precipitated downstream delay propagation.
    """
    conn = sqlite3.connect(DB_PATH)

    query = """
    SELECT
        source_train_id,
        affected_train_id,
        sample_location,
        edge_weight,
        n_causal_events,
        mean_post_worsening,
        mean_incremental_worsening
    FROM causal_propagation_edges
    ORDER BY edge_weight DESC
    LIMIT 1;
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        return None

    return df.iloc[0]


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
        df = df.dropna(subset=["actual_time_utc"]).reset_index(drop=True)

    return df


def annotate_interaction_location(
    ax,
    df: pd.DataFrame,
    interaction_location: str,
) -> None:
    """
    Mark rows where the selected source train is observed at the sample
    interaction location.
    """
    matches = df[df["location_name"] == interaction_location]

    if matches.empty:
        return

    for idx, row in matches.iterrows():
        ax.axvline(
            x=idx,
            linestyle="--",
            linewidth=1.2,
            alpha=0.7,
        )

        ax.text(
            idx,
            row["timetable_variation"],
            f"  interaction: {interaction_location}",
            fontsize=8,
            va="bottom",
        )


def plot_train_timeline(
    df: pd.DataFrame,
    train_id: str,
    causal_edge: pd.Series,
) -> Path:
    x = range(len(df))

    fig, ax = plt.subplots(figsize=(14, 7))

    ax.plot(
        x,
        df["timetable_variation"],
        marker="o",
        linewidth=2,
    )

    for i, row in df.iterrows():
        label = f"{row['location_name']} ({row['event_type']})"
        ax.annotate(
            label,
            (i, row["timetable_variation"]),
            fontsize=7,
            xytext=(0, 8),
            textcoords="offset points",
            rotation=20,
            ha="left",
        )

    interaction_location = causal_edge["sample_location"]
    annotate_interaction_location(ax, df, interaction_location)

    x_labels = [
        t.strftime("%H:%M") if pd.notna(t) else ""
        for t in df["actual_time_utc"]
    ]

    ax.set_xticks(list(x))
    ax.set_xticklabels(x_labels, rotation=45, ha="right")

    ax.set_xlabel("Actual event time")
    ax.set_ylabel("Timetable variation (minutes)")

    title = (
        f"Delay Timeline for Source Train {train_id}\n"
        f"Strongest causal edge: {causal_edge['source_train_id']} → "
        f"{causal_edge['affected_train_id']} at {causal_edge['sample_location']} "
        f"(edge weight={causal_edge['edge_weight']:.2f})"
    )

    ax.set_title(title)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()

    output_path = OUTPUT_DIR / f"source_train_delay_timeline_{train_id}.png"
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    return output_path


def main() -> None:
    causal_edge = load_top_causal_edge()

    if causal_edge is None:
        print("No causal propagation edges found. Run build_causal_propagation_network.py first.")
        return

    source_train_id = causal_edge["source_train_id"]
    affected_train_id = causal_edge["affected_train_id"]
    interaction_location = causal_edge["sample_location"]

    print("Selected strongest causal propagation edge:")
    print(f"  Source train:   {source_train_id}")
    print(f"  Affected train: {affected_train_id}")
    print(f"  Location:       {interaction_location}")
    print(f"  Edge weight:    {causal_edge['edge_weight']:.2f}")

    df = load_train_sequence(source_train_id)

    if df.empty:
        print(f"No movement events found for source train_id={source_train_id}")
        return

    output_path = plot_train_timeline(
        df=df,
        train_id=source_train_id,
        causal_edge=causal_edge,
    )

    print(f"Saved source-train delay timeline to: {output_path}")


if __name__ == "__main__":
    main()