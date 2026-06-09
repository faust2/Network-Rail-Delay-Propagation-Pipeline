import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = "data/railway.db"
OUTPUT_DIR = Path("outputs/tables")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TRAIN_OUTPUT = OUTPUT_DIR / "recovery_intervention_plan_by_train.csv"
LOCATION_OUTPUT = OUTPUT_DIR / "recovery_intervention_plan_by_location.csv"
SUMMARY_OUTPUT = OUTPUT_DIR / "recovery_intervention_summary.csv"


def load_causal_edges() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)

    query = """
    SELECT
        source_train_id,
        affected_train_id,
        n_causal_events,
        n_locations,
        mean_time_gap,
        max_source_variation,
        mean_post_worsening,
        max_post_worsening,
        mean_incremental_worsening,
        max_causal_score,
        mean_causal_score,
        sample_location,
        edge_weight,
        first_interaction_time,
        last_interaction_time
    FROM causal_propagation_edges;
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    if "first_interaction_time" in df.columns:
        df["first_interaction_time"] = pd.to_datetime(df["first_interaction_time"], errors="coerce")

    if "last_interaction_time" in df.columns:
        df["last_interaction_time"] = pd.to_datetime(df["last_interaction_time"], errors="coerce")

    return df


def optimise_train_interventions(
    edges: pd.DataFrame,
    intervention_budget: int = 5,
    assumed_effectiveness: float = 0.5,
) -> pd.DataFrame:
    """
    Rank source trains by estimated downstream propagation impact.

    Interpretation:
    If we intervene on a source train, we assume we reduce a fraction of its
    outgoing propagation impact. This is not a true operational control model;
    it is a network-based recovery prioritisation model.
    """
    if edges.empty:
        return pd.DataFrame()

    summary = (
        edges.groupby("source_train_id")
        .agg(
            n_affected_trains=("affected_train_id", "nunique"),
            n_edges=("affected_train_id", "size"),
            n_locations=("sample_location", "nunique"),
            total_downstream_edge_weight=("edge_weight", "sum"),
            mean_edge_weight=("edge_weight", "mean"),
            max_edge_weight=("edge_weight", "max"),
            mean_post_worsening=("mean_post_worsening", "mean"),
            max_post_worsening=("max_post_worsening", "max"),
            mean_causal_score=("mean_causal_score", "mean"),
            max_causal_score=("max_causal_score", "max"),
            first_interaction_time=("first_interaction_time", "min"),
            last_interaction_time=("last_interaction_time", "max"),
        )
        .reset_index()
    )

    summary["assumed_effectiveness"] = assumed_effectiveness

    summary["estimated_avoided_downstream_impact"] = (
        summary["total_downstream_edge_weight"] * assumed_effectiveness
    )

    summary["intervention_priority_score"] = (
        summary["estimated_avoided_downstream_impact"]
        + 0.5 * summary["n_affected_trains"]
        + 0.2 * summary["n_locations"]
    )

    summary = summary.sort_values(
        by=[
            "intervention_priority_score",
            "estimated_avoided_downstream_impact",
            "n_affected_trains",
        ],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    summary["selected_for_intervention"] = 0
    summary.loc[: intervention_budget - 1, "selected_for_intervention"] = 1
    summary["rank"] = range(1, len(summary) + 1)

    return summary


def optimise_location_interventions(
    edges: pd.DataFrame,
    intervention_budget: int = 5,
    assumed_effectiveness: float = 0.5,
) -> pd.DataFrame:
    """
    Rank locations by estimated downstream propagation impact.

    Interpretation:
    If we target a location operationally, we assume a fraction of propagation
    impact associated with that location can be reduced.
    """
    if edges.empty:
        return pd.DataFrame()

    summary = (
        edges.groupby("sample_location")
        .agg(
            n_source_trains=("source_train_id", "nunique"),
            n_affected_trains=("affected_train_id", "nunique"),
            n_edges=("affected_train_id", "size"),
            total_location_edge_weight=("edge_weight", "sum"),
            mean_edge_weight=("edge_weight", "mean"),
            max_edge_weight=("edge_weight", "max"),
            mean_post_worsening=("mean_post_worsening", "mean"),
            max_post_worsening=("max_post_worsening", "max"),
            mean_causal_score=("mean_causal_score", "mean"),
            max_causal_score=("max_causal_score", "max"),
            first_interaction_time=("first_interaction_time", "min"),
            last_interaction_time=("last_interaction_time", "max"),
        )
        .reset_index()
        .rename(columns={"sample_location": "location_name"})
    )

    summary["assumed_effectiveness"] = assumed_effectiveness

    summary["estimated_avoided_downstream_impact"] = (
        summary["total_location_edge_weight"] * assumed_effectiveness
    )

    summary["intervention_priority_score"] = (
        summary["estimated_avoided_downstream_impact"]
        + 0.4 * summary["n_source_trains"]
        + 0.4 * summary["n_affected_trains"]
    )

    summary = summary.sort_values(
        by=[
            "intervention_priority_score",
            "estimated_avoided_downstream_impact",
            "n_affected_trains",
        ],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    summary["selected_for_intervention"] = 0
    summary.loc[: intervention_budget - 1, "selected_for_intervention"] = 1
    summary["rank"] = range(1, len(summary) + 1)

    return summary


def build_summary_table(
    train_plan: pd.DataFrame,
    location_plan: pd.DataFrame,
    train_budget: int,
    location_budget: int,
    assumed_effectiveness: float,
) -> pd.DataFrame:
    rows = []

    if not train_plan.empty:
        selected_train = train_plan[train_plan["selected_for_intervention"] == 1]
        rows.append(
            {
                "intervention_type": "train",
                "budget": train_budget,
                "assumed_effectiveness": assumed_effectiveness,
                "n_selected": len(selected_train),
                "estimated_total_avoided_impact": selected_train[
                    "estimated_avoided_downstream_impact"
                ].sum(),
                "total_available_downstream_impact": train_plan[
                    "total_downstream_edge_weight"
                ].sum(),
            }
        )

    if not location_plan.empty:
        selected_location = location_plan[location_plan["selected_for_intervention"] == 1]
        rows.append(
            {
                "intervention_type": "location",
                "budget": location_budget,
                "assumed_effectiveness": assumed_effectiveness,
                "n_selected": len(selected_location),
                "estimated_total_avoided_impact": selected_location[
                    "estimated_avoided_downstream_impact"
                ].sum(),
                "total_available_downstream_impact": location_plan[
                    "total_location_edge_weight"
                ].sum(),
            }
        )

    return pd.DataFrame(rows)


def print_results(train_plan: pd.DataFrame, location_plan: pd.DataFrame, summary: pd.DataFrame) -> None:
    print("\nRecovery optimisation summary:")
    print(summary)

    if not train_plan.empty:
        print("\nTop train-level recovery interventions:")
        print(
            train_plan[
                [
                    "rank",
                    "source_train_id",
                    "selected_for_intervention",
                    "n_affected_trains",
                    "n_locations",
                    "total_downstream_edge_weight",
                    "estimated_avoided_downstream_impact",
                    "intervention_priority_score",
                ]
            ].head(20)
        )

    if not location_plan.empty:
        print("\nTop location-level recovery interventions:")
        print(
            location_plan[
                [
                    "rank",
                    "location_name",
                    "selected_for_intervention",
                    "n_source_trains",
                    "n_affected_trains",
                    "total_location_edge_weight",
                    "estimated_avoided_downstream_impact",
                    "intervention_priority_score",
                ]
            ].head(20)
        )


def main() -> None:
    train_intervention_budget = 5
    location_intervention_budget = 5

    # This is a modelling assumption.
    # 0.5 means: an intervention is assumed to remove 50% of the inferred downstream impact.
    # It is not measured from real control-room outcomes.
    assumed_effectiveness = 0.5

    edges = load_causal_edges()

    if edges.empty:
        print("No causal propagation edges found. Run build_causal_propagation_network.py first.")
        return

    train_plan = optimise_train_interventions(
        edges=edges,
        intervention_budget=train_intervention_budget,
        assumed_effectiveness=assumed_effectiveness,
    )

    location_plan = optimise_location_interventions(
        edges=edges,
        intervention_budget=location_intervention_budget,
        assumed_effectiveness=assumed_effectiveness,
    )

    summary = build_summary_table(
        train_plan=train_plan,
        location_plan=location_plan,
        train_budget=train_intervention_budget,
        location_budget=location_intervention_budget,
        assumed_effectiveness=assumed_effectiveness,
    )

    train_plan.to_csv(TRAIN_OUTPUT, index=False)
    location_plan.to_csv(LOCATION_OUTPUT, index=False)
    summary.to_csv(SUMMARY_OUTPUT, index=False)

    print_results(train_plan, location_plan, summary)

    print(f"\nSaved train intervention plan to: {TRAIN_OUTPUT}")
    print(f"Saved location intervention plan to: {LOCATION_OUTPUT}")
    print(f"Saved optimisation summary to: {SUMMARY_OUTPUT}")


if __name__ == "__main__":
    main()