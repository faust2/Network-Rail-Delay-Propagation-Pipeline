from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pandas as pd


ProgressCallback = Callable[[str], None]


def _load_edges(database_path: Path) -> pd.DataFrame:
    if not database_path.exists():
        raise FileNotFoundError("Build railway.db before running recovery optimisation")
    connection = sqlite3.connect(database_path)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "causal_propagation_edges" not in tables:
            raise RuntimeError("Run propagation analysis before recovery optimisation")
        edges = pd.read_sql_query(
            "SELECT * FROM causal_propagation_edges ORDER BY edge_weight DESC", connection
        )
    finally:
        connection.close()
    if edges.empty:
        raise RuntimeError("No high-confidence propagation edges are available")
    for column in ("first_interaction_time", "last_interaction_time"):
        if column in edges:
            edges[column] = pd.to_datetime(edges[column], errors="coerce")
    return edges


def _rank_trains(edges: pd.DataFrame, effectiveness: float) -> pd.DataFrame:
    frame = edges.groupby("source_train_id").agg(
        n_affected_trains=("affected_train_id", "nunique"),
        n_edges=("affected_train_id", "size"), n_locations=("sample_location", "nunique"),
        total_downstream_edge_weight=("edge_weight", "sum"),
        mean_edge_weight=("edge_weight", "mean"), max_edge_weight=("edge_weight", "max"),
        mean_post_worsening=("mean_post_worsening", "mean"),
        max_post_worsening=("max_post_worsening", "max"),
        mean_causal_score=("mean_causal_score", "mean"),
        max_causal_score=("max_causal_score", "max"),
        first_interaction_time=("first_interaction_time", "min"),
        last_interaction_time=("last_interaction_time", "max"),
    ).reset_index()
    frame["assumed_effectiveness"] = effectiveness
    frame["estimated_avoided_downstream_impact"] = frame.total_downstream_edge_weight * effectiveness
    frame["intervention_priority_score"] = (
        frame.estimated_avoided_downstream_impact + .5 * frame.n_affected_trains + .2 * frame.n_locations
    )
    frame = frame.sort_values(["intervention_priority_score", "n_affected_trains"], ascending=False).reset_index(drop=True)
    frame["rank"] = range(1, len(frame) + 1)
    return frame


def _rank_locations(edges: pd.DataFrame, effectiveness: float) -> pd.DataFrame:
    frame = edges.groupby("sample_location").agg(
        n_source_trains=("source_train_id", "nunique"),
        n_affected_trains=("affected_train_id", "nunique"),
        n_edges=("affected_train_id", "size"),
        total_location_edge_weight=("edge_weight", "sum"),
        mean_edge_weight=("edge_weight", "mean"), max_edge_weight=("edge_weight", "max"),
        mean_post_worsening=("mean_post_worsening", "mean"),
        max_post_worsening=("max_post_worsening", "max"),
        mean_causal_score=("mean_causal_score", "mean"),
        max_causal_score=("max_causal_score", "max"),
        first_interaction_time=("first_interaction_time", "min"),
        last_interaction_time=("last_interaction_time", "max"),
    ).reset_index().rename(columns={"sample_location": "location_name"})
    frame["assumed_effectiveness"] = effectiveness
    frame["estimated_avoided_downstream_impact"] = frame.total_location_edge_weight * effectiveness
    frame["intervention_priority_score"] = (
        frame.estimated_avoided_downstream_impact + .4 * frame.n_source_trains + .4 * frame.n_affected_trains
    )
    frame = frame.sort_values(["intervention_priority_score", "n_affected_trains"], ascending=False).reset_index(drop=True)
    frame["rank"] = range(1, len(frame) + 1)
    return frame


def _candidates(trains: pd.DataFrame, locations: pd.DataFrame, limit: int) -> pd.DataFrame:
    train = pd.DataFrame({"intervention_id": "TRAIN_" + trains.source_train_id.astype(str),
        "intervention_type": "train", "target": trains.source_train_id.astype(str),
        "benefit": trains.estimated_avoided_downstream_impact,
        "affected_count": trains.n_affected_trains, "priority_score": trains.intervention_priority_score})
    location = pd.DataFrame({"intervention_id": "LOCATION_" + locations.location_name.astype(str),
        "intervention_type": "location", "target": locations.location_name.astype(str),
        "benefit": locations.estimated_avoided_downstream_impact,
        "affected_count": locations.n_affected_trains, "priority_score": locations.intervention_priority_score})
    frame = pd.concat([train.head(limit), location.head(limit)], ignore_index=True)
    frame["intervention_cost"] = 1.0 + .25 * frame.affected_count
    return frame[frame.benefit > 0].reset_index(drop=True)


def _solve(candidates: pd.DataFrame, budget: float, maximum: int) -> pd.DataFrame:
    """Exact 0/1 optimisation with quarter-unit integerised costs."""
    budget_units = int(round(budget * 4))
    states: dict[tuple[int, int], tuple[float, tuple[int, ...]]] = {(0, 0): (0.0, ())}
    for index, row in candidates.iterrows():
        cost = int(round(float(row.intervention_cost) * 4))
        updated = dict(states)
        for (used, count), (benefit, chosen) in states.items():
            key = (used + cost, count + 1)
            value = benefit + float(row.benefit)
            if key[0] <= budget_units and key[1] <= maximum and value > updated.get(key, (-1, ()))[0]:
                updated[key] = (value, chosen + (index,))
        states = updated
    selected = max(states.values(), key=lambda item: item[0])[1]
    solution = candidates.copy(); solution["selected_by_bip"] = 0
    if selected: solution.loc[list(selected), "selected_by_bip"] = 1
    return solution.sort_values(["selected_by_bip", "benefit"], ascending=False).reset_index(drop=True)


def run_recovery(database_path: Path, output_directory: Path, total_budget: float = 8,
                 max_interventions: int = 5, assumed_effectiveness: float = .5,
                 candidate_limit: int = 100,
                 progress: ProgressCallback = lambda _: None) -> None:
    progress("Loading high-confidence propagation edges")
    edges = _load_edges(database_path)
    trains, locations = _rank_trains(edges, assumed_effectiveness), _rank_locations(edges, assumed_effectiveness)
    progress(f"Ranked {len(trains):,} train and {len(locations):,} location candidates")
    candidates = _candidates(trains, locations, candidate_limit)
    solution = _solve(candidates, total_budget, max_interventions)
    selected = solution[solution.selected_by_bip == 1]
    trains["selected_for_intervention"] = trains.source_train_id.astype(str).isin(
        selected[selected.intervention_type == "train"].target.astype(str)).astype(int)
    locations["selected_for_intervention"] = locations.location_name.astype(str).isin(
        selected[selected.intervention_type == "location"].target.astype(str)).astype(int)
    summary = pd.DataFrame([{"solver_used": "exact_dynamic_programming",
        "total_candidates": len(solution), "selected_interventions": len(selected),
        "total_budget": total_budget, "used_budget": selected.intervention_cost.sum(),
        "max_interventions": max_interventions,
        "estimated_total_avoided_impact": selected.benefit.sum(),
        "selected_train_interventions": int((selected.intervention_type == "train").sum()),
        "selected_location_interventions": int((selected.intervention_type == "location").sum()),
        "assumed_effectiveness": assumed_effectiveness}])
    connection = sqlite3.connect(database_path)
    try:
        trains.to_sql("recovery_intervention_plan_by_train", connection, if_exists="replace", index=False)
        locations.to_sql("recovery_intervention_plan_by_location", connection, if_exists="replace", index=False)
        solution.to_sql("recovery_intervention_solution", connection, if_exists="replace", index=False)
        summary.to_sql("recovery_intervention_summary", connection, if_exists="replace", index=False)
        connection.commit()
    finally:
        connection.close()
    output_directory.mkdir(parents=True, exist_ok=True)
    trains.to_csv(output_directory / "recovery_intervention_plan_by_train.csv", index=False)
    locations.to_csv(output_directory / "recovery_intervention_plan_by_location.csv", index=False)
    solution.to_csv(output_directory / "recovery_intervention_bip_solution.csv", index=False)
    summary.to_csv(output_directory / "recovery_intervention_bip_summary.csv", index=False)
    progress(f"Selected {len(selected)} interventions using {selected.intervention_cost.sum():.2f} budget units")
    progress("Saved recovery tables to railway.db and outputs/tables")


def load_recovery_results(database_path: Path) -> dict[str, object]:
    connection = sqlite3.connect(database_path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"recovery_intervention_plan_by_train", "recovery_intervention_plan_by_location",
                    "recovery_intervention_solution", "recovery_intervention_summary"}
        if not required.issubset(tables): raise RuntimeError("Run recovery optimisation to create the result tables")
        trains = pd.read_sql_query("SELECT * FROM recovery_intervention_plan_by_train ORDER BY rank", connection)
        locations = pd.read_sql_query("SELECT * FROM recovery_intervention_plan_by_location ORDER BY rank", connection)
        solution = pd.read_sql_query("SELECT * FROM recovery_intervention_solution ORDER BY selected_by_bip DESC, benefit DESC", connection)
        summary = pd.read_sql_query("SELECT * FROM recovery_intervention_summary", connection)
    finally: connection.close()
    row = summary.iloc[0]
    return {"trains": trains, "locations": locations, "solution": solution, "summary": summary,
            "metrics": {"candidates": int(row.total_candidates), "selected": int(row.selected_interventions),
                        "used_budget": float(row.used_budget), "benefit": float(row.estimated_total_avoided_impact)}}
