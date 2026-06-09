import sqlite3
from collections import deque
import pandas as pd

DB_PATH = "data/railway.db"


def load_enriched_events() -> pd.DataFrame:
    """
    Load enriched movement events with readable location names and usable timing fields.
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
    Build candidate source->affected train pairs at the same location and close in time.
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


def build_train_sequences(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Build per-train ordered event sequences with previous/next event variation info.
    """
    sequences = {}

    for train_id, g in df.groupby("train_id"):
        g = g.sort_values("actual_time_utc").reset_index(drop=True).copy()

        g["prev_variation"] = g["timetable_variation"].shift(1)
        g["prev_location_name"] = g["location_name"].shift(1)
        g["prev_actual_time"] = g["actual_time_utc"].shift(1)

        g["next_variation"] = g["timetable_variation"].shift(-1)
        g["next_location_name"] = g["location_name"].shift(-1)
        g["next_actual_time"] = g["actual_time_utc"].shift(-1)

        g["pre_interaction_change"] = g["timetable_variation"] - g["prev_variation"]
        g["post_interaction_change"] = g["next_variation"] - g["timetable_variation"]

        sequences[train_id] = g

    return sequences


def attach_sequence_context(
    pairs: pd.DataFrame,
    sequences: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    For each candidate pair, attach:
    - affected train previous event
    - affected train next event
    - pre- and post-interaction delay changes

    This is the basis for causal-style scoring.
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

        match = seq[
            (seq["actual_time_utc"] == affected_time)
            & (seq["loc_stanox"] == loc_stanox)
        ].copy()

        if match.empty:
            continue

        row = match.iloc[0]

        out = pair.to_dict()

        out["affected_prev_actual_time"] = row.get("prev_actual_time")
        out["affected_prev_location_name"] = row.get("prev_location_name")
        out["affected_prev_variation"] = row.get("prev_variation")
        out["affected_pre_interaction_change"] = row.get("pre_interaction_change")

        out["affected_next_actual_time"] = row.get("next_actual_time")
        out["affected_next_location_name"] = row.get("next_location_name")
        out["affected_next_variation"] = row.get("next_variation")
        out["affected_post_interaction_change"] = row.get("post_interaction_change")

        rows.append(out)

    return pd.DataFrame(rows)


def classify_candidate_pairs(
    pairs: pd.DataFrame,
    source_delay_threshold: int = 5,
    affected_delay_threshold: int = 5,
) -> pd.DataFrame:
    """
    Initial same-location delay co-occurrence filter.
    """
    if pairs.empty:
        return pairs.copy()

    out = pairs.copy()

    out["candidate_propagation"] = (
        (out["source_variation"] >= source_delay_threshold)
        & (out["affected_variation_at_interaction"] >= affected_delay_threshold)
    ).astype(int)

    return out


def add_causal_features(pairs: pd.DataFrame) -> pd.DataFrame:
    """
    Add causal-style features to each candidate pair.

    Features aim to distinguish:
    - coincident delay
    from
    - likely delay transmission
    """
    if pairs.empty:
        return pairs.copy()

    out = pairs.copy()

    # Source must be meaningfully delayed to plausibly "transmit" delay
    out["source_delay_strength"] = out["source_variation"].clip(lower=0)

    # Affected train should worsen after the interaction point
    out["affected_post_worsening"] = out["affected_post_interaction_change"].fillna(0)

    # If affected train was already worsening before interaction, that weakens causality
    out["affected_pre_worsening"] = out["affected_pre_interaction_change"].fillna(0)

    # Improvement in explanatory strength when post > pre
    out["incremental_worsening_signal"] = (
        out["affected_post_worsening"] - out["affected_pre_worsening"]
    )

    # Shorter time gaps are more plausible for operational interaction
    out["short_gap_bonus"] = (10 - out["time_gap_minutes"]).clip(lower=0)

    # Similarity / transfer-like effect:
    # if source is very delayed and affected later worsens by a non-trivial amount
    out["delay_transfer_signal"] = (
        out["source_variation"].clip(lower=0)
        .combine(out["affected_post_worsening"].clip(lower=0), min)
    )

    # Binary flags
    out["affected_gets_worse_after"] = (out["affected_post_worsening"] > 0).astype(int)
    out["affected_was_already_worsening_before"] = (out["affected_pre_worsening"] > 0).astype(int)
    out["post_stronger_than_pre"] = (out["incremental_worsening_signal"] > 0).astype(int)

    return out


def compute_causal_score(pairs: pd.DataFrame) -> pd.DataFrame:
    """
    Compute a heuristic causal score for each pair.

    Higher score means:
    - source was delayed
    - affected train got worse after the interaction
    - worsening after interaction is stronger than before
    - interaction was close in time
    """
    if pairs.empty:
        return pairs.copy()

    out = pairs.copy()

    out["causal_score"] = (
        0.6 * out["source_delay_strength"].fillna(0)
        + 1.8 * out["affected_post_worsening"].clip(lower=0).fillna(0)
        + 1.2 * out["incremental_worsening_signal"].clip(lower=0).fillna(0)
        + 0.4 * out["short_gap_bonus"].fillna(0)
        + 0.8 * out["delay_transfer_signal"].fillna(0)
        + 1.0 * out["post_stronger_than_pre"].fillna(0)
        - 0.6 * out["affected_pre_worsening"].clip(lower=0).fillna(0)
    )

    out["high_confidence_causal"] = (
        (out["candidate_propagation"] == 1)
        & (out["affected_gets_worse_after"] == 1)
        & (out["post_stronger_than_pre"] == 1)
        & (out["causal_score"] >= 8)
    ).astype(int)

    return out


def build_causal_edges(pairs: pd.DataFrame) -> pd.DataFrame:
    """
    Convert high-confidence pair rows into train-to-train causal-style edges.
    """
    if pairs.empty:
        return pd.DataFrame()

    work = pairs[pairs["high_confidence_causal"] == 1].copy()
    if work.empty:
        return pd.DataFrame()

    edge_summary = (
        work.groupby(["source_train_id", "affected_train_id"])
        .agg(
            n_causal_events=("location_name", "size"),
            n_locations=("location_name", "nunique"),
            mean_time_gap=("time_gap_minutes", "mean"),
            max_source_variation=("source_variation", "max"),
            mean_post_worsening=("affected_post_worsening", "mean"),
            max_post_worsening=("affected_post_worsening", "max"),
            mean_incremental_worsening=("incremental_worsening_signal", "mean"),
            max_causal_score=("causal_score", "max"),
            mean_causal_score=("causal_score", "mean"),
            sample_location=("location_name", "first"),
            first_interaction_time=("source_actual_time", "min"),
            last_interaction_time=("source_actual_time", "max"),
        )
        .reset_index()
    )

    edge_summary["edge_weight"] = (
        edge_summary["n_causal_events"]
        + 0.7 * edge_summary["n_locations"]
        + 1.0 * edge_summary["mean_post_worsening"].fillna(0)
        + 0.8 * edge_summary["mean_incremental_worsening"].fillna(0)
        + 0.2 * edge_summary["max_source_variation"].fillna(0)
        - 0.2 * edge_summary["mean_time_gap"].fillna(0)
    )

    return edge_summary.sort_values(
        by=["edge_weight", "mean_causal_score", "n_causal_events"],
        ascending=[False, False, False]
    ).reset_index(drop=True)


def build_node_summary(edges: pd.DataFrame) -> pd.DataFrame:
    """
    Build train-level network metrics from directed causal edges.
    """
    if edges.empty:
        return pd.DataFrame()

    out_strength = (
        edges.groupby("source_train_id")
        .agg(
            out_degree=("affected_train_id", "nunique"),
            out_weight=("edge_weight", "sum"),
            out_events=("n_causal_events", "sum"),
        )
        .reset_index()
        .rename(columns={"source_train_id": "train_id"})
    )

    in_strength = (
        edges.groupby("affected_train_id")
        .agg(
            in_degree=("source_train_id", "nunique"),
            in_weight=("edge_weight", "sum"),
            in_events=("n_causal_events", "sum"),
        )
        .reset_index()
        .rename(columns={"affected_train_id": "train_id"})
    )

    nodes = pd.merge(out_strength, in_strength, on="train_id", how="outer").fillna(0)

    nodes["total_degree"] = nodes["out_degree"] + nodes["in_degree"]
    nodes["total_weight"] = nodes["out_weight"] + nodes["in_weight"]
    nodes["node_role"] = nodes.apply(classify_node_role, axis=1)

    return nodes.sort_values(
        by=["total_weight", "total_degree"],
        ascending=[False, False]
    ).reset_index(drop=True)


def classify_node_role(row: pd.Series) -> str:
    out_w = row.get("out_weight", 0)
    in_w = row.get("in_weight", 0)

    if out_w > 0 and in_w == 0:
        return "SOURCE_ONLY"
    if in_w > 0 and out_w == 0:
        return "SINK_ONLY"
    if out_w > 0 and in_w > 0:
        return "INTERMEDIATE"
    return "ISOLATED"


def summarise_causal_locations(pairs: pd.DataFrame) -> pd.DataFrame:
    """
    Rank locations by strength of high-confidence causal-style propagation evidence.
    """
    if pairs.empty:
        return pd.DataFrame()

    work = pairs[pairs["high_confidence_causal"] == 1].copy()
    if work.empty:
        return pd.DataFrame()

    summary = (
        work.groupby(["loc_stanox", "location_name"])
        .agg(
            n_causal_pairs=("high_confidence_causal", "size"),
            n_sources=("source_train_id", "nunique"),
            n_affected=("affected_train_id", "nunique"),
            mean_post_worsening=("affected_post_worsening", "mean"),
            mean_incremental_worsening=("incremental_worsening_signal", "mean"),
            mean_time_gap=("time_gap_minutes", "mean"),
            max_causal_score=("causal_score", "max"),
        )
        .reset_index()
    )

    summary["location_causal_score"] = (
        summary["n_causal_pairs"]
        + 0.5 * summary["n_affected"]
        + 1.0 * summary["mean_post_worsening"].fillna(0)
        + 0.8 * summary["mean_incremental_worsening"].fillna(0)
        - 0.2 * summary["mean_time_gap"].fillna(0)
    )

    return summary.sort_values(
        by=["location_causal_score", "n_causal_pairs", "mean_post_worsening"],
        ascending=[False, False, False]
    ).reset_index(drop=True)


def trace_chain_from_source(
    edges: pd.DataFrame,
    start_train_id: str,
    max_depth: int = 4,
    min_edge_weight: float = 1.0,
) -> list[tuple[str, str, float]]:
    """
    Trace a simple breadth-first causal chain.
    """
    if edges.empty:
        return []

    eligible = edges[edges["edge_weight"] >= min_edge_weight].copy()

    adjacency = {}
    for _, row in eligible.iterrows():
        adjacency.setdefault(row["source_train_id"], []).append(
            (row["affected_train_id"], row["edge_weight"])
        )

    visited = {start_train_id}
    q = deque([(start_train_id, 0)])
    chain_edges = []

    while q:
        current, depth = q.popleft()
        if depth >= max_depth:
            continue

        for nxt, w in adjacency.get(current, []):
            chain_edges.append((current, nxt, w))
            if nxt not in visited:
                visited.add(nxt)
                q.append((nxt, depth + 1))

    return chain_edges


def save_causal_network_tables(
    pairs: pd.DataFrame,
    edges: pd.DataFrame,
    nodes: pd.DataFrame,
    locations: pd.DataFrame,
    conn: sqlite3.Connection,
) -> None:
    """
    Save causal-style outputs into SQLite for later querying.
    """
    conn.execute("DROP TABLE IF EXISTS causal_propagation_pairs;")
    conn.execute("DROP TABLE IF EXISTS causal_propagation_edges;")
    conn.execute("DROP TABLE IF EXISTS causal_propagation_nodes;")
    conn.execute("DROP TABLE IF EXISTS causal_propagation_locations;")

    pairs.to_sql("causal_propagation_pairs", conn, if_exists="replace", index=False)
    edges.to_sql("causal_propagation_edges", conn, if_exists="replace", index=False)
    nodes.to_sql("causal_propagation_nodes", conn, if_exists="replace", index=False)
    locations.to_sql("causal_propagation_locations", conn, if_exists="replace", index=False)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cpe_source
        ON causal_propagation_edges (source_train_id);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cpe_affected
        ON causal_propagation_edges (affected_train_id);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cpp_loc
        ON causal_propagation_pairs (loc_stanox);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cpp_source
        ON causal_propagation_pairs (source_train_id);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cpp_affected
        ON causal_propagation_pairs (affected_train_id);
    """)
    conn.commit()


def print_summary(pairs: pd.DataFrame, edges: pd.DataFrame, nodes: pd.DataFrame, locations: pd.DataFrame) -> None:
    total_pairs = len(pairs)
    candidate_pairs = int((pairs["candidate_propagation"] == 1).sum()) if not pairs.empty else 0
    high_conf_pairs = int((pairs["high_confidence_causal"] == 1).sum()) if not pairs.empty else 0

    print("\nCausal propagation summary:")
    print(f"Total evaluated same-location pairs: {total_pairs}")
    print(f"Candidate propagation pairs: {candidate_pairs}")
    print(f"High-confidence causal pairs: {high_conf_pairs}")

    print(f"\nNumber of directed causal edges: {len(edges)}")
    print(f"Number of trains (nodes): {len(nodes)}")

    if not nodes.empty:
        print("\nNode role counts:")
        print(nodes["node_role"].value_counts())

    if not edges.empty:
        print("\nTop 20 strongest causal edges:")
        print(
            edges[
                [
                    "source_train_id",
                    "affected_train_id",
                    "n_causal_events",
                    "n_locations",
                    "mean_time_gap",
                    "mean_post_worsening",
                    "mean_incremental_worsening",
                    "edge_weight",
                    "sample_location",
                ]
            ].head(20)
        )

    if not locations.empty:
        print("\nTop 20 causal propagation locations:")
        print(locations.head(20))

    if not nodes.empty:
        print("\nTop 20 trains by total causal network weight:")
        print(
            nodes[
                [
                    "train_id",
                    "node_role",
                    "out_degree",
                    "in_degree",
                    "out_weight",
                    "in_weight",
                    "total_weight",
                ]
            ].head(20)
        )


def print_example_chain(edges: pd.DataFrame, nodes: pd.DataFrame) -> None:
    if nodes.empty:
        print("No node summary available.")
        return

    source_candidates = nodes.sort_values(by="out_weight", ascending=False)
    source_candidates = source_candidates[source_candidates["out_weight"] > 0]

    if source_candidates.empty:
        print("No source candidates available for chain tracing.")
        return

    start_train_id = source_candidates.iloc[0]["train_id"]
    chain = trace_chain_from_source(edges, start_train_id, max_depth=4, min_edge_weight=1.0)

    print(f"\nExample causal propagation chain starting from top source train: {start_train_id}")
    if not chain:
        print("No downstream chain found.")
        return

    for src, dst, w in chain:
        print(f"{src} -> {dst}  (edge_weight={w:.2f})")


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
    pairs = attach_sequence_context(pairs, sequences)
    if pairs.empty:
        print("No sequence-context rows could be built.")
        return

    pairs = add_causal_features(pairs)
    pairs = compute_causal_score(pairs)

    edges = build_causal_edges(pairs)
    nodes = build_node_summary(edges)
    locations = summarise_causal_locations(pairs)

    conn = sqlite3.connect(DB_PATH)
    try:
        save_causal_network_tables(pairs, edges, nodes, locations, conn)
    finally:
        conn.close()

    print_summary(pairs, edges, nodes, locations)
    print_example_chain(edges, nodes)


if __name__ == "__main__":
    main()