from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pandas as pd


ProgressCallback = Callable[[str], None]

EDGE_COLUMNS = [
    "source_train_id", "affected_train_id", "n_causal_events", "n_locations",
    "mean_time_gap", "max_source_variation", "mean_post_worsening",
    "max_post_worsening", "mean_incremental_worsening", "max_causal_score",
    "mean_causal_score", "sample_location", "first_interaction_time",
    "last_interaction_time", "edge_weight",
]
NODE_COLUMNS = [
    "train_id", "out_degree", "out_weight", "out_events", "in_degree",
    "in_weight", "in_events", "total_degree", "total_weight", "node_role",
]
LOCATION_COLUMNS = [
    "loc_stanox", "location_name", "n_causal_pairs", "n_sources",
    "n_affected", "mean_post_worsening", "mean_incremental_worsening",
    "mean_time_gap", "max_causal_score", "location_causal_score",
]


def _require_source_tables(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = {"tiploc", "train_movements"} - tables
    if missing:
        raise RuntimeError(
            "Missing required database tables: " + ", ".join(sorted(missing))
        )


def build_enriched_movements(
    database_path: Path, progress: ProgressCallback
) -> dict[str, int]:
    if not database_path.exists():
        raise FileNotFoundError("Build railway.db before running propagation analysis")
    connection = sqlite3.connect(database_path)
    try:
        _require_source_tables(connection)
        progress("Building STANOX location lookup")
        connection.executescript(
            """
            DROP TABLE IF EXISTS stanox_lookup;
            CREATE TABLE stanox_lookup AS
            SELECT
                TRIM(stanox) AS stanox,
                MIN(tps_description) AS location_name,
                COUNT(*) AS stanox_match_count
            FROM tiploc
            WHERE stanox IS NOT NULL
              AND TRIM(stanox) <> ''
              AND tps_description IS NOT NULL
              AND TRIM(tps_description) <> ''
            GROUP BY TRIM(stanox);
            CREATE INDEX IF NOT EXISTS idx_sl_stanox ON stanox_lookup (stanox);

            DROP TABLE IF EXISTS train_movements_enriched;
            CREATE TABLE train_movements_enriched AS
            SELECT tm.*, sl.location_name, sl.stanox_match_count
            FROM train_movements tm
            LEFT JOIN stanox_lookup sl ON TRIM(tm.loc_stanox) = sl.stanox;
            CREATE INDEX IF NOT EXISTS idx_tme_train_id
                ON train_movements_enriched (train_id);
            CREATE INDEX IF NOT EXISTS idx_tme_loc_stanox
                ON train_movements_enriched (loc_stanox);
            CREATE INDEX IF NOT EXISTS idx_tme_location_name
                ON train_movements_enriched (location_name);
            """
        )
        connection.commit()
        total = connection.execute(
            "SELECT COUNT(*) FROM train_movements_enriched"
        ).fetchone()[0]
        matched = connection.execute(
            "SELECT COUNT(*) FROM train_movements_enriched "
            "WHERE location_name IS NOT NULL"
        ).fetchone()[0]
        progress(f"Enriched {total:,} movements; {matched:,} matched to locations")
        return {"total": total, "matched": matched}
    finally:
        connection.close()


def _load_events(database_path: Path) -> pd.DataFrame:
    connection = sqlite3.connect(database_path)
    try:
        events = pd.read_sql_query(
            """
            SELECT train_id, event_type, loc_stanox, location_name,
                   actual_time_utc, planned_time_utc, timetable_variation,
                   variation_status, platform, direction_ind, toc_id
            FROM train_movements_enriched
            WHERE train_id IS NOT NULL
              AND location_name IS NOT NULL
              AND actual_time_utc IS NOT NULL
              AND timetable_variation IS NOT NULL
            ORDER BY train_id, actual_time_utc
            """,
            connection,
        )
    finally:
        connection.close()
    if not events.empty:
        events["actual_time_utc"] = pd.to_datetime(
            events["actual_time_utc"], errors="coerce"
        )
        events["planned_time_utc"] = pd.to_datetime(
            events["planned_time_utc"], errors="coerce"
        )
        events = events.dropna(subset=["actual_time_utc"]).reset_index(drop=True)
    return events


def _same_location_pairs(
    events: pd.DataFrame,
    max_gap_minutes: float,
    require_same_direction: bool,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for _, group in events.groupby("loc_stanox"):
        group = group.sort_values("actual_time_utc").reset_index(drop=True)
        for source_index in range(len(group) - 1):
            source = group.iloc[source_index]
            for affected_index in range(source_index + 1, len(group)):
                affected = group.iloc[affected_index]
                gap = (
                    affected["actual_time_utc"] - source["actual_time_utc"]
                ).total_seconds() / 60
                if gap < 0:
                    continue
                if gap > max_gap_minutes:
                    break
                if source["train_id"] == affected["train_id"]:
                    continue
                if (
                    require_same_direction
                    and source["direction_ind"] != affected["direction_ind"]
                ):
                    continue
                rows.append(
                    {
                        "location_name": source["location_name"],
                        "loc_stanox": source["loc_stanox"],
                        "direction_ind": source["direction_ind"],
                        "source_train_id": source["train_id"],
                        "source_event_type": source["event_type"],
                        "source_actual_time": source["actual_time_utc"],
                        "source_variation": source["timetable_variation"],
                        "affected_train_id": affected["train_id"],
                        "affected_event_type": affected["event_type"],
                        "affected_actual_time": affected["actual_time_utc"],
                        "affected_variation_at_interaction": affected[
                            "timetable_variation"
                        ],
                        "time_gap_minutes": gap,
                    }
                )
    return pd.DataFrame(rows)


def _attach_sequence_context(
    pairs: pd.DataFrame, events: pd.DataFrame
) -> pd.DataFrame:
    sequences: dict[str, pd.DataFrame] = {}
    for train_id, group in events.groupby("train_id"):
        sequence = group.sort_values("actual_time_utc").reset_index(drop=True).copy()
        sequence["prev_variation"] = sequence["timetable_variation"].shift(1)
        sequence["prev_location_name"] = sequence["location_name"].shift(1)
        sequence["prev_actual_time"] = sequence["actual_time_utc"].shift(1)
        sequence["next_variation"] = sequence["timetable_variation"].shift(-1)
        sequence["next_location_name"] = sequence["location_name"].shift(-1)
        sequence["next_actual_time"] = sequence["actual_time_utc"].shift(-1)
        sequence["pre_interaction_change"] = (
            sequence["timetable_variation"] - sequence["prev_variation"]
        )
        sequence["post_interaction_change"] = (
            sequence["next_variation"] - sequence["timetable_variation"]
        )
        sequences[str(train_id)] = sequence

    rows: list[dict[str, object]] = []
    for _, pair in pairs.iterrows():
        sequence = sequences.get(str(pair["affected_train_id"]))
        if sequence is None:
            continue
        matches = sequence[
            (sequence["actual_time_utc"] == pair["affected_actual_time"])
            & (sequence["loc_stanox"] == pair["loc_stanox"])
        ]
        if matches.empty:
            continue
        match = matches.iloc[0]
        output = pair.to_dict()
        output.update(
            {
                "affected_prev_actual_time": match.get("prev_actual_time"),
                "affected_prev_location_name": match.get("prev_location_name"),
                "affected_prev_variation": match.get("prev_variation"),
                "affected_pre_interaction_change": match.get(
                    "pre_interaction_change"
                ),
                "affected_next_actual_time": match.get("next_actual_time"),
                "affected_next_location_name": match.get("next_location_name"),
                "affected_next_variation": match.get("next_variation"),
                "affected_post_interaction_change": match.get(
                    "post_interaction_change"
                ),
            }
        )
        rows.append(output)
    return pd.DataFrame(rows)


def _score_pairs(
    pairs: pd.DataFrame,
    source_delay_threshold: float,
    affected_delay_threshold: float,
    causal_score_threshold: float,
) -> pd.DataFrame:
    output = pairs.copy()
    output["candidate_propagation"] = (
        (output["source_variation"] >= source_delay_threshold)
        & (
            output["affected_variation_at_interaction"]
            >= affected_delay_threshold
        )
    ).astype(int)
    output["source_delay_strength"] = output["source_variation"].clip(lower=0)
    output["affected_post_worsening"] = output[
        "affected_post_interaction_change"
    ].fillna(0)
    output["affected_pre_worsening"] = output[
        "affected_pre_interaction_change"
    ].fillna(0)
    output["incremental_worsening_signal"] = (
        output["affected_post_worsening"] - output["affected_pre_worsening"]
    )
    output["short_gap_bonus"] = (10 - output["time_gap_minutes"]).clip(lower=0)
    output["delay_transfer_signal"] = output["source_variation"].clip(
        lower=0
    ).combine(output["affected_post_worsening"].clip(lower=0), min)
    output["affected_gets_worse_after"] = (
        output["affected_post_worsening"] > 0
    ).astype(int)
    output["affected_pre_worsening_flag"] = (
        output["affected_pre_worsening"] > 0
    ).astype(int)
    output["post_stronger_than_pre"] = (
        output["incremental_worsening_signal"] > 0
    ).astype(int)
    output["causal_score"] = (
        0.6 * output["source_delay_strength"].fillna(0)
        + 1.8 * output["affected_post_worsening"].clip(lower=0).fillna(0)
        + 1.2
        * output["incremental_worsening_signal"].clip(lower=0).fillna(0)
        + 0.4 * output["short_gap_bonus"].fillna(0)
        + 0.8 * output["delay_transfer_signal"].fillna(0)
        + output["post_stronger_than_pre"].fillna(0)
        - 0.6 * output["affected_pre_worsening"].clip(lower=0).fillna(0)
    )
    output["high_confidence_causal"] = (
        (output["candidate_propagation"] == 1)
        & (output["affected_gets_worse_after"] == 1)
        & (output["post_stronger_than_pre"] == 1)
        & (output["causal_score"] >= causal_score_threshold)
    ).astype(int)
    return output


def _build_edges(pairs: pd.DataFrame) -> pd.DataFrame:
    work = pairs[pairs["high_confidence_causal"] == 1].copy()
    if work.empty:
        return pd.DataFrame(columns=EDGE_COLUMNS)
    edges = (
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
    edges["edge_weight"] = (
        edges["n_causal_events"]
        + 0.7 * edges["n_locations"]
        + edges["mean_post_worsening"].fillna(0)
        + 0.8 * edges["mean_incremental_worsening"].fillna(0)
        + 0.2 * edges["max_source_variation"].fillna(0)
        - 0.2 * edges["mean_time_gap"].fillna(0)
    )
    return edges.sort_values(
        ["edge_weight", "mean_causal_score", "n_causal_events"],
        ascending=False,
    ).reset_index(drop=True)


def _build_nodes(edges: pd.DataFrame) -> pd.DataFrame:
    if edges.empty:
        return pd.DataFrame(columns=NODE_COLUMNS)
    outgoing = (
        edges.groupby("source_train_id")
        .agg(
            out_degree=("affected_train_id", "nunique"),
            out_weight=("edge_weight", "sum"),
            out_events=("n_causal_events", "sum"),
        )
        .reset_index()
        .rename(columns={"source_train_id": "train_id"})
    )
    incoming = (
        edges.groupby("affected_train_id")
        .agg(
            in_degree=("source_train_id", "nunique"),
            in_weight=("edge_weight", "sum"),
            in_events=("n_causal_events", "sum"),
        )
        .reset_index()
        .rename(columns={"affected_train_id": "train_id"})
    )
    nodes = outgoing.merge(incoming, on="train_id", how="outer").fillna(0)
    nodes["total_degree"] = nodes["out_degree"] + nodes["in_degree"]
    nodes["total_weight"] = nodes["out_weight"] + nodes["in_weight"]
    nodes["node_role"] = nodes.apply(
        lambda row: (
            "SOURCE_ONLY"
            if row["out_weight"] > 0 and row["in_weight"] == 0
            else "SINK_ONLY"
            if row["in_weight"] > 0 and row["out_weight"] == 0
            else "INTERMEDIATE"
            if row["out_weight"] > 0 and row["in_weight"] > 0
            else "ISOLATED"
        ),
        axis=1,
    )
    return nodes.sort_values(
        ["total_weight", "total_degree"], ascending=False
    ).reset_index(drop=True)


def _build_locations(pairs: pd.DataFrame) -> pd.DataFrame:
    work = pairs[pairs["high_confidence_causal"] == 1].copy()
    if work.empty:
        return pd.DataFrame(columns=LOCATION_COLUMNS)
    locations = (
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
    locations["location_causal_score"] = (
        locations["n_causal_pairs"]
        + 0.5 * locations["n_affected"]
        + locations["mean_post_worsening"].fillna(0)
        + 0.8 * locations["mean_incremental_worsening"].fillna(0)
        - 0.2 * locations["mean_time_gap"].fillna(0)
    )
    return locations.sort_values(
        ["location_causal_score", "n_causal_pairs", "mean_post_worsening"],
        ascending=False,
    ).reset_index(drop=True)


def _save_results(
    database_path: Path,
    pairs: pd.DataFrame,
    edges: pd.DataFrame,
    nodes: pd.DataFrame,
    locations: pd.DataFrame,
) -> None:
    connection = sqlite3.connect(database_path)
    try:
        for table in (
            "causal_propagation_pairs",
            "causal_propagation_edges",
            "causal_propagation_nodes",
            "causal_propagation_locations",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        pairs.to_sql("causal_propagation_pairs", connection, index=False)
        edges.to_sql("causal_propagation_edges", connection, index=False)
        nodes.to_sql("causal_propagation_nodes", connection, index=False)
        locations.to_sql("causal_propagation_locations", connection, index=False)
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_cpe_source
                ON causal_propagation_edges (source_train_id);
            CREATE INDEX IF NOT EXISTS idx_cpe_affected
                ON causal_propagation_edges (affected_train_id);
            CREATE INDEX IF NOT EXISTS idx_cpp_loc
                ON causal_propagation_pairs (loc_stanox);
            CREATE INDEX IF NOT EXISTS idx_cpp_source
                ON causal_propagation_pairs (source_train_id);
            CREATE INDEX IF NOT EXISTS idx_cpp_affected
                ON causal_propagation_pairs (affected_train_id);
            """
        )
        connection.commit()
    finally:
        connection.close()


def run_propagation_pipeline(
    database_path: Path,
    max_gap_minutes: float = 10,
    source_delay_threshold: float = 5,
    affected_delay_threshold: float = 5,
    causal_score_threshold: float = 8,
    require_same_direction: bool = True,
    progress: ProgressCallback = lambda _: None,
) -> dict[str, int]:
    build_enriched_movements(database_path, progress)
    progress("Loading enriched movement events")
    events = _load_events(database_path)
    if events.empty:
        raise RuntimeError("No usable enriched movement events were found")
    progress(f"Loaded {len(events):,} usable movement events")
    pairs = _same_location_pairs(events, max_gap_minutes, require_same_direction)
    if pairs.empty:
        raise RuntimeError("No same-location train pairs met the time-gap rule")
    progress(f"Constructed {len(pairs):,} same-location close-time pairs")
    pairs = _attach_sequence_context(pairs, events)
    if pairs.empty:
        raise RuntimeError("No pairs could be linked to affected-train sequences")
    progress(f"Attached before-and-after context to {len(pairs):,} pairs")
    pairs = _score_pairs(
        pairs,
        source_delay_threshold,
        affected_delay_threshold,
        causal_score_threshold,
    )
    edges = _build_edges(pairs)
    nodes = _build_nodes(edges)
    locations = _build_locations(pairs)
    progress(f"Identified {len(edges):,} directed high-confidence edges")
    _save_results(database_path, pairs, edges, nodes, locations)
    progress("Saved propagation tables to railway.db")
    return {
        "pairs": len(pairs),
        "candidate_pairs": int(pairs["candidate_propagation"].sum()),
        "high_confidence_pairs": int(pairs["high_confidence_causal"].sum()),
        "edges": len(edges),
        "nodes": len(nodes),
        "locations": len(locations),
    }


def load_propagation_results(database_path: Path) -> dict[str, object]:
    if not database_path.exists():
        raise FileNotFoundError("railway.db does not exist")
    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        required = {
            "causal_propagation_pairs",
            "causal_propagation_edges",
            "causal_propagation_nodes",
            "causal_propagation_locations",
        }
        if not required.issubset(tables):
            raise RuntimeError("Run propagation analysis to create the result tables")
        edges = pd.read_sql_query(
            "SELECT * FROM causal_propagation_edges ORDER BY edge_weight DESC",
            connection,
        )
        nodes = pd.read_sql_query(
            "SELECT * FROM causal_propagation_nodes ORDER BY total_weight DESC",
            connection,
        )
        locations = pd.read_sql_query(
            "SELECT * FROM causal_propagation_locations "
            "ORDER BY location_causal_score DESC",
            connection,
        )
        pair_counts = connection.execute(
            """
            SELECT COUNT(*),
                   COALESCE(SUM(candidate_propagation), 0),
                   COALESCE(SUM(high_confidence_causal), 0)
            FROM causal_propagation_pairs
            """
        ).fetchone()
    finally:
        connection.close()
    return {
        "edges": edges,
        "nodes": nodes,
        "locations": locations,
        "metrics": {
            "pairs": pair_counts[0],
            "candidate_pairs": pair_counts[1],
            "high_confidence_pairs": pair_counts[2],
            "edges": len(edges),
            "nodes": len(nodes),
            "locations": len(locations),
        },
    }
