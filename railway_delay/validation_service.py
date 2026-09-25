from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pandas as pd


ProgressCallback = Callable[[str], None]


def _load_inputs(database_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not database_path.exists():
        raise FileNotFoundError("Build railway.db before running validation")
    connection = sqlite3.connect(database_path)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        required = {"train_movements_enriched", "causal_propagation_edges"}
        if not required.issubset(tables):
            raise RuntimeError("Run propagation analysis before validation")
        events = pd.read_sql_query(
            """
            SELECT train_id, event_type, loc_stanox, location_name,
                   actual_time_utc, planned_time_utc, timetable_variation,
                   variation_status, platform, direction_ind, toc_id
            FROM train_movements_enriched
            WHERE train_id IS NOT NULL AND loc_stanox IS NOT NULL
              AND location_name IS NOT NULL AND actual_time_utc IS NOT NULL
              AND timetable_variation IS NOT NULL
            ORDER BY train_id, actual_time_utc
            """, connection,
        )
        edges = pd.read_sql_query(
            """
            SELECT source_train_id, affected_train_id, sample_location,
                   edge_weight, mean_time_gap, mean_post_worsening,
                   mean_incremental_worsening
            FROM causal_propagation_edges ORDER BY edge_weight DESC
            """, connection,
        )
    finally:
        connection.close()
    if edges.empty:
        raise RuntimeError("No propagation edges are available to validate")
    events["actual_time_utc"] = pd.to_datetime(events["actual_time_utc"], errors="coerce")
    events["planned_time_utc"] = pd.to_datetime(events["planned_time_utc"], errors="coerce")
    events = events.dropna(subset=["actual_time_utc"]).sort_values(
        ["train_id", "actual_time_utc"]
    ).copy()
    events["previous_variation"] = events.groupby("train_id")["timetable_variation"].shift(1)
    events["next_variation"] = events.groupby("train_id")["timetable_variation"].shift(-1)
    events["pre_change"] = events["timetable_variation"] - events["previous_variation"]
    events["post_change"] = events["next_variation"] - events["timetable_variation"]
    events["post_worsened"] = (events["post_change"] > 0).astype(int)
    return events, edges


def _interaction(events: pd.DataFrame, edge: pd.Series, max_gap: float) -> dict | None:
    source = events[(events.train_id == edge.source_train_id) &
                    (events.location_name == edge.sample_location)]
    affected = events[(events.train_id == edge.affected_train_id) &
                      (events.location_name == edge.sample_location)]
    best = None
    for _, src in source.iterrows():
        for _, aft in affected.iterrows():
            gap = (aft.actual_time_utc - src.actual_time_utc).total_seconds() / 60
            if gap <= 0 or gap > max_gap or src.direction_ind != aft.direction_ind:
                continue
            candidate = {
                "source_train_id": edge.source_train_id,
                "affected_train_id": edge.affected_train_id,
                "location_name": edge.sample_location,
                "loc_stanox": aft.loc_stanox,
                "direction_ind": aft.direction_ind,
                "source_time": src.actual_time_utc,
                "affected_time": aft.actual_time_utc,
                "time_gap_minutes": gap,
                "source_variation": src.timetable_variation,
                "affected_variation": aft.timetable_variation,
                "affected_pre_change": aft.pre_change,
                "affected_post_change": aft.post_change,
                "affected_post_worsened": aft.post_worsened,
            }
            if best is None or gap < best["time_gap_minutes"]:
                best = candidate
    return best


def _controls(events: pd.DataFrame, case: dict, window: float, gap: float) -> pd.DataFrame:
    start = case["affected_time"] - pd.Timedelta(minutes=window)
    end = case["affected_time"] + pd.Timedelta(minutes=window)
    local = events[
        (events.location_name == case["location_name"])
        & (events.direction_ind == case["direction_ind"])
        & events.actual_time_utc.between(start, end)
        & ~events.train_id.isin([case["source_train_id"], case["affected_train_id"]])
    ]
    neighbourhood = events[
        (events.location_name == case["location_name"])
        & (events.direction_ind == case["direction_ind"])
        & events.actual_time_utc.between(start - pd.Timedelta(minutes=gap), end)
    ].sort_values("actual_time_utc")
    keep = []
    for _, row in local.iterrows():
        prior = neighbourhood[
            (neighbourhood.actual_time_utc < row.actual_time_utc)
            & (neighbourhood.actual_time_utc >= row.actual_time_utc - pd.Timedelta(minutes=gap))
            & (neighbourhood.train_id != row.train_id)
            & (neighbourhood.timetable_variation >= 5)
        ]
        if prior.empty:
            keep.append(row)
    return pd.DataFrame(keep, columns=local.columns)


def _matched_controls(events: pd.DataFrame, edges: pd.DataFrame, max_cases: int,
                      max_gap: float, control_window: float) -> pd.DataFrame:
    rows = []
    for _, edge in edges.head(max_cases).iterrows():
        case = _interaction(events, edge, max_gap)
        if case is None:
            continue
        controls = _controls(events, case, control_window, max_gap)
        count = len(controls)
        control_rate = controls.post_worsened.mean() if count else None
        control_change = controls.post_change.mean() if count else None
        candidate_worsened = int(case["affected_post_worsened"])
        candidate_change = case["affected_post_change"]
        if not count:
            result = "NO_CONTROL_AVAILABLE"
        elif candidate_worsened and candidate_change > control_change:
            result = "SUPPORTS_CASE"
        elif candidate_worsened:
            result = "PARTIAL_SUPPORT"
        else:
            result = "DOES_NOT_SUPPORT"
        rows.append({**case, "candidate_post_worsening": candidate_worsened,
                     "n_controls": count, "control_post_worsening_rate": control_rate,
                     "control_mean_post_change": control_change, "control_result": result,
                     "edge_weight": edge.edge_weight,
                     "mean_edge_post_worsening": edge.mean_post_worsening,
                     "mean_edge_incremental_worsening": edge.mean_incremental_worsening})
    return pd.DataFrame(rows)


def _pairs(events: pd.DataFrame, max_gap: float, delay: float, mode: str) -> pd.DataFrame:
    rows = []
    for _, group in events.groupby("loc_stanox"):
        group = group.sort_values("actual_time_utc").reset_index(drop=True)
        for i in range(len(group) - 1):
            src = group.iloc[i]
            for j in range(i + 1, len(group)):
                aft = group.iloc[j]
                gap = (aft.actual_time_utc - src.actual_time_utc).total_seconds() / 60
                if gap <= 0:
                    continue
                if mode != "large_gap" and gap > max_gap:
                    break
                if mode == "large_gap" and (gap <= max_gap or gap > 3 * max_gap):
                    continue
                if src.train_id == aft.train_id:
                    continue
                same = src.direction_ind == aft.direction_ind
                if (mode == "same_direction" and not same) or (mode == "opposite_direction" and same):
                    continue
                if src.timetable_variation < delay or aft.timetable_variation < delay:
                    continue
                post, pre = aft.post_change, aft.pre_change
                rows.append({"mode": mode, "source_train_id": src.train_id,
                             "affected_train_id": aft.train_id,
                             "location_name": src.location_name,
                             "post_worsened": int(pd.notna(post) and post > 0),
                             "post_stronger_than_pre": int(pd.notna(post) and pd.notna(pre) and post > pre),
                             "affected_post_change": post})
    return pd.DataFrame(rows)


def _summary(pairs: pd.DataFrame, gap: float, delay: float, mode: str) -> dict:
    if pairs.empty:
        return {"mode": mode, "max_gap_minutes": gap, "delay_threshold": delay,
                "n_pairs": 0, "n_post_worsened": 0, "pct_post_worsened": 0.0,
                "n_post_stronger_than_pre": 0, "pct_post_stronger_than_pre": 0.0,
                "mean_post_change": None, "median_post_change": None,
                "n_unique_locations": 0, "n_unique_source_trains": 0,
                "n_unique_affected_trains": 0, "top_locations": ""}
    return {"mode": mode, "max_gap_minutes": gap, "delay_threshold": delay,
            "n_pairs": len(pairs), "n_post_worsened": int(pairs.post_worsened.sum()),
            "pct_post_worsened": float(pairs.post_worsened.mean()),
            "n_post_stronger_than_pre": int(pairs.post_stronger_than_pre.sum()),
            "pct_post_stronger_than_pre": float(pairs.post_stronger_than_pre.mean()),
            "mean_post_change": float(pairs.affected_post_change.mean()),
            "median_post_change": float(pairs.affected_post_change.median()),
            "n_unique_locations": pairs.location_name.nunique(),
            "n_unique_source_trains": pairs.source_train_id.nunique(),
            "n_unique_affected_trains": pairs.affected_train_id.nunique(),
            "top_locations": "; ".join(pairs.location_name.value_counts().head(5).index)}


def run_validation(database_path: Path, output_directory: Path, max_cases: int = 50,
                   max_gap_minutes: float = 10, control_window_minutes: float = 30,
                   progress: ProgressCallback = lambda _: None) -> None:
    progress("Loading propagation cases and enriched movement events")
    events, edges = _load_inputs(database_path)
    progress(f"Loaded {len(events):,} events and {len(edges):,} propagation edges")
    controls = _matched_controls(events, edges, max_cases, max_gap_minutes, control_window_minutes)
    if controls.empty:
        raise RuntimeError("No propagation cases could be matched to interaction events")
    progress(f"Matched {len(controls):,} cases to local controls")
    sensitivity = pd.DataFrame([
        _summary(_pairs(events, gap, delay, "same_direction"), gap, delay, "same_direction")
        for gap in (5, 10, 15) for delay in (3, 5, 10)
    ])
    progress("Completed the 3 × 3 threshold sensitivity grid")
    negative = pd.DataFrame([
        _summary(_pairs(events, 10, 5, mode), 10, 5, mode)
        for mode in ("same_direction", "opposite_direction", "large_gap")
    ])
    progress("Completed same-direction, opposite-direction and large-gap tests")
    connection = sqlite3.connect(database_path)
    try:
        controls.to_sql("validation_control_results", connection, if_exists="replace", index=False)
        sensitivity.to_sql("validation_threshold_sensitivity", connection, if_exists="replace", index=False)
        negative.to_sql("validation_negative_tests", connection, if_exists="replace", index=False)
        connection.commit()
    finally:
        connection.close()
    output_directory.mkdir(parents=True, exist_ok=True)
    controls.to_csv(output_directory / "validation_control_comparison.csv", index=False)
    sensitivity.to_csv(output_directory / "validation_threshold_sensitivity.csv", index=False)
    negative.to_csv(output_directory / "validation_negative_tests.csv", index=False)
    progress("Saved validation tables to railway.db and outputs/tables")


def load_validation_results(database_path: Path) -> dict[str, object]:
    if not database_path.exists():
        raise FileNotFoundError("railway.db does not exist")
    connection = sqlite3.connect(database_path)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        required = {"validation_control_results", "validation_threshold_sensitivity",
                    "validation_negative_tests"}
        if not required.issubset(tables):
            raise RuntimeError("Run validation to create the result tables")
        controls = pd.read_sql_query("SELECT * FROM validation_control_results ORDER BY edge_weight DESC", connection)
        sensitivity = pd.read_sql_query("SELECT * FROM validation_threshold_sensitivity", connection)
        negative = pd.read_sql_query("SELECT * FROM validation_negative_tests", connection)
    finally:
        connection.close()
    counts = controls.control_result.value_counts()
    return {"controls": controls, "sensitivity": sensitivity, "negative": negative,
            "metrics": {"evaluated": len(controls), "supports": int(counts.get("SUPPORTS_CASE", 0)),
                        "partial": int(counts.get("PARTIAL_SUPPORT", 0)),
                        "not_support": int(counts.get("DOES_NOT_SUPPORT", 0)),
                        "no_control": int(counts.get("NO_CONTROL_AVAILABLE", 0))}}
