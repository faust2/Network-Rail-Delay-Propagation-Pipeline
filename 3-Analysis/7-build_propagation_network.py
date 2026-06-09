import sqlite3
import pandas as pd
from collections import deque

DB_PATH = "data/railway.db"


def load_enriched_events() -> pd.DataFrame:
    """
    Load enriched movement events with readable locations and usable timing fields.
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


def classify_candidate_pairs(
    pairs: pd.DataFrame,
    source_delay_threshold: int = 5,
    affected_delay_threshold: int = 5,
) -> pd.DataFrame:
    """
    Simple same-location delay co-occurrence filter.
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
    Build per-train ordered event sequences with next-event variation.
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
    For each candidate pair, inspect the affected train's next recorded event.
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
        out["affected_next_actual_time"] = row.get("next_actual_time")
        out["affected_next_location_name"] = row.get("next_location_name")
        out["affected_next_variation"] = row.get("next_variation")
        out["affected_post_increase"] = row.get("post_interaction_increase")

        post_inc = row.get("post_interaction_increase")
        if pd.isna(post_inc):
            out["affected_delay_increases_after_interaction"] = 0
        else:
            out["affected_delay_increases_after_interaction"] = int(post_inc > 0)

        rows.append(out)

    return pd.DataFrame(rows)


def build_propagation_edges(post_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert post-interaction candidate rows into train-to-train network edges.

    Edge meaning:
    source_train_id -> affected_train_id
    """
    if post_df.empty:
        return pd.DataFrame()

    work = post_df[
        (post_df["candidate_propagation"] == 1)
        & (post_df["affected_delay_increases_after_interaction"] == 1)
    ].copy()

    if work.empty:
        return pd.DataFrame()

    # Edge strength rewards:
    # - repeated evidence across multiple locations/times
    # - larger source delay
    # - larger affected delay
    # - actual worsening after the interaction
    # - shorter time gap
    work["edge_evidence_score"] = (
        work["source_variation"].clip(lower=0)
        + work["affected_variation_at_interaction"].clip(lower=0)
        + work["affected_post_increase"].clip(lower=0)
        - 0.5 * work["time_gap_minutes"]
    )

    edge_summary = (
        work.groupby(["source_train_id", "affected_train_id"])
        .agg(
            n_interactions=("location_name", "size"),
            n_locations=("location_name", "nunique"),
            mean_time_gap=("time_gap_minutes", "mean"),
            max_source_variation=("source_variation", "max"),
            max_affected_variation=("affected_variation_at_interaction", "max"),
            mean_post_increase=("affected_post_increase", "mean"),
            max_post_increase=("affected_post_increase", "max"),
            max_edge_evidence_score=("edge_evidence_score", "max"),
            sample_location=("location_name", "first"),
            first_interaction_time=("source_actual_time", "min"),
            last_interaction_time=("source_actual_time", "max"),
        )
        .reset_index()
    )

    edge_summary["edge_weight"] = (
        edge_summary["n_interactions"]
        + 0.5 * edge_summary["n_locations"]
        + edge_summary["mean_post_increase"].fillna(0)
        + 0.1 * edge_summary["max_source_variation"].fillna(0)
        - 0.2 * edge_summary["mean_time_gap"].fillna(0)
    )

    return edge_summary.sort_values(
        by=["edge_weight", "n_interactions", "mean_post_increase"],
        ascending=[False, False, False]
    ).reset_index(drop=True)


def build_node_summary(edges: pd.DataFrame) -> pd.DataFrame:
    """
    Build train-level network metrics from directed edges.
    """
    if edges.empty:
        return pd.DataFrame()

    out_strength = (
        edges.groupby("source_train_id")
        .agg(
            out_degree=("affected_train_id", "nunique"),
            out_weight=("edge_weight", "sum"),
            out_interactions=("n_interactions", "sum"),
        )
        .reset_index()
        .rename(columns={"source_train_id": "train_id"})
    )

    in_strength = (
        edges.groupby("affected_train_id")
        .agg(
            in_degree=("source_train_id", "nunique"),
            in_weight=("edge_weight", "sum"),
            in_interactions=("n_interactions", "sum"),
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
    """
    Simple interpretation of a train's role in the propagation network.
    """
    out_w = row.get("out_weight", 0)
    in_w = row.get("in_weight", 0)

    if out_w > 0 and in_w == 0:
        return "SOURCE_ONLY"
    if in_w > 0 and out_w == 0:
        return "SINK_ONLY"
    if out_w > 0 and in_w > 0:
        return "INTERMEDIATE"
    return "ISOLATED"


def trace_chain_from_source(
    edges: pd.DataFrame,
    start_train_id: str,
    max_depth: int = 4,
    min_edge_weight: float = 1.0,
) -> list[tuple[str, str, float]]:
    """
    Trace a simple breadth-first propagation chain from one source train.

    Returns a list of directed edges:
    (source_train_id, affected_train_id, edge_weight)
    """
    if edges.empty:
        return []

    eligible = edges[edges["edge_weight"] >= min_edge_weight].copy()

    adjacency = {}
    for _, row in eligible.iterrows():
        adjacency.setdefault(row["source_train_id"], []).append(
            (
                row["affected_train_id"],
                row["edge_weight"],
            )
        )

    visited = set([start_train_id])
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


def save_network_tables(
    edges: pd.DataFrame,
    nodes: pd.DataFrame,
    conn: sqlite3.Connection,
) -> None:
    """
    Save network tables into SQLite for later querying.
    """
    conn.execute("DROP TABLE IF EXISTS propagation_network_edges;")
    conn.execute("DROP TABLE IF EXISTS propagation_network_nodes;")

    edges.to_sql("propagation_network_edges", conn, if_exists="replace", index=False)
    nodes.to_sql("propagation_network_nodes", conn, if_exists="replace", index=False)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pne_source
        ON propagation_network_edges (source_train_id);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pne_affected
        ON propagation_network_edges (affected_train_id);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pnn_train
        ON propagation_network_nodes (train_id);
    """)
    conn.commit()


def print_network_summary(edges: pd.DataFrame, nodes: pd.DataFrame) -> None:
    print("\nPropagation network summary:")
    print(f"Number of directed edges: {len(edges)}")
    print(f"Number of trains (nodes): {len(nodes)}")

    if not nodes.empty:
        print("\nNode role counts:")
        print(nodes["node_role"].value_counts())

    if not edges.empty:
        print("\nTop 20 strongest propagation edges:")
        print(
            edges[
                [
                    "source_train_id",
                    "affected_train_id",
                    "n_interactions",
                    "n_locations",
                    "mean_time_gap",
                    "mean_post_increase",
                    "edge_weight",
                    "sample_location",
                ]
            ].head(20)
        )

    if not nodes.empty:
        print("\nTop 20 trains by total network weight:")
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

    print(f"\nExample propagation chain starting from top source train: {start_train_id}")
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
    post_df = attach_post_interaction_effect(pairs, sequences)
    if post_df.empty:
        print("No post-interaction rows could be built.")
        return

    edges = build_propagation_edges(post_df)
    nodes = build_node_summary(edges)

    conn = sqlite3.connect(DB_PATH)
    try:
        save_network_tables(edges, nodes, conn)
    finally:
        conn.close()

    print_network_summary(edges, nodes)
    print_example_chain(edges, nodes)


if __name__ == "__main__":
    main()