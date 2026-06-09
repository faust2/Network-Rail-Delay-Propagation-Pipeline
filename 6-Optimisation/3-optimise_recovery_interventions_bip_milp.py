from pathlib import Path
from itertools import combinations

import numpy as np
import pandas as pd

TABLE_DIR = Path("outputs/tables")
TABLE_DIR.mkdir(parents=True, exist_ok=True)

TRAIN_PLAN_PATH = TABLE_DIR / "recovery_intervention_plan_by_train.csv"
LOCATION_PLAN_PATH = TABLE_DIR / "recovery_intervention_plan_by_location.csv"

OUTPUT_PATH = TABLE_DIR / "recovery_intervention_bip_solution.csv"
SUMMARY_PATH = TABLE_DIR / "recovery_intervention_bip_summary.csv"


def load_candidates(max_train_candidates: int = 100, max_location_candidates: int = 100) -> pd.DataFrame:
    if not TRAIN_PLAN_PATH.exists():
        raise FileNotFoundError(
            f"Missing {TRAIN_PLAN_PATH}. Run optimise_recovery_interventions.py first."
        )

    if not LOCATION_PLAN_PATH.exists():
        raise FileNotFoundError(
            f"Missing {LOCATION_PLAN_PATH}. Run optimise_recovery_interventions.py first."
        )

    train_df = pd.read_csv(TRAIN_PLAN_PATH).head(max_train_candidates).copy()
    location_df = pd.read_csv(LOCATION_PLAN_PATH).head(max_location_candidates).copy()

    train_candidates = pd.DataFrame(
        {
            "intervention_id": "TRAIN_" + train_df["source_train_id"].astype(str),
            "intervention_type": "train",
            "target": train_df["source_train_id"].astype(str),
            "benefit": train_df["estimated_avoided_downstream_impact"],
            "affected_count": train_df["n_affected_trains"],
            "priority_score": train_df["intervention_priority_score"],
        }
    )

    location_candidates = pd.DataFrame(
        {
            "intervention_id": "LOCATION_" + location_df["location_name"].astype(str),
            "intervention_type": "location",
            "target": location_df["location_name"].astype(str),
            "benefit": location_df["estimated_avoided_downstream_impact"],
            "affected_count": location_df["n_affected_trains"],
            "priority_score": location_df["intervention_priority_score"],
        }
    )

    candidates = pd.concat([train_candidates, location_candidates], ignore_index=True)

    candidates["benefit"] = pd.to_numeric(candidates["benefit"], errors="coerce").fillna(0)
    candidates["affected_count"] = pd.to_numeric(candidates["affected_count"], errors="coerce").fillna(0)

    # Heuristic cost model.
    # This is NOT real operational intervention cost.
    candidates["intervention_cost"] = 1.0 + 0.25 * candidates["affected_count"]

    candidates = candidates[candidates["benefit"] > 0].copy()
    candidates = candidates.reset_index(drop=True)

    return candidates


def solve_by_enumeration(
    candidates: pd.DataFrame,
    total_budget: float,
    max_interventions: int,
) -> tuple[pd.DataFrame, str]:
    n = len(candidates)

    best_value = -1
    best_subset = []

    indices = list(range(n))

    for r in range(1, min(max_interventions, n) + 1):
        for subset in combinations(indices, r):
            subset_df = candidates.iloc[list(subset)]

            total_cost = subset_df["intervention_cost"].sum()
            if total_cost > total_budget:
                continue

            total_benefit = subset_df["benefit"].sum()

            if total_benefit > best_value:
                best_value = total_benefit
                best_subset = list(subset)

    solution = candidates.copy()
    solution["selected_by_bip"] = 0

    if best_subset:
        solution.loc[best_subset, "selected_by_bip"] = 1

    return solution, "exact_enumeration"


def solve_by_scipy_milp(
    candidates: pd.DataFrame,
    total_budget: float,
    max_interventions: int,
) -> tuple[pd.DataFrame, str]:
    """
    Solves the binary optimisation using scipy.optimize.milp if available.

    Model:
        maximise benefit'x

    scipy minimises, so we minimise:
        -benefit'x

    Constraints:
        cost'x <= total_budget
        sum(x) <= max_interventions
        x_i in {0,1}
    """
    try:
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ImportError as exc:
        raise ImportError(
            "SciPy MILP is unavailable. Install or upgrade scipy, e.g. "
            "`pip install --upgrade scipy`, or reduce candidate count so enumeration is used."
        ) from exc

    benefits = candidates["benefit"].to_numpy(dtype=float)
    costs = candidates["intervention_cost"].to_numpy(dtype=float)

    c = -benefits

    A = np.vstack(
        [
            costs,
            np.ones(len(candidates)),
        ]
    )

    constraints = LinearConstraint(
        A,
        lb=[0, 0],
        ub=[total_budget, max_interventions],
    )

    bounds = Bounds(
        lb=np.zeros(len(candidates)),
        ub=np.ones(len(candidates)),
    )

    integrality = np.ones(len(candidates))

    result = milp(
        c=c,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={"time_limit": 60},
    )

    if not result.success:
        raise RuntimeError(f"SciPy MILP failed: {result.message}")

    selected = np.rint(result.x).astype(int)

    solution = candidates.copy()
    solution["selected_by_bip"] = selected

    return solution, "scipy_milp"


def solve_binary_program(
    candidates: pd.DataFrame,
    total_budget: float = 8.0,
    max_interventions: int = 5,
    enumeration_threshold: int = 30,
) -> tuple[pd.DataFrame, str]:
    """
    Uses exact enumeration for small problems.

    If the candidate set exceeds enumeration_threshold, switches to SciPy MILP.
    """
    n = len(candidates)

    if n == 0:
        solution = candidates.copy()
        solution["selected_by_bip"] = []
        return solution, "empty"

    if n <= enumeration_threshold:
        return solve_by_enumeration(
            candidates=candidates,
            total_budget=total_budget,
            max_interventions=max_interventions,
        )

    return solve_by_scipy_milp(
        candidates=candidates,
        total_budget=total_budget,
        max_interventions=max_interventions,
    )


def build_summary(
    solution: pd.DataFrame,
    total_budget: float,
    max_interventions: int,
    solver_used: str,
    enumeration_threshold: int,
) -> pd.DataFrame:
    selected = solution[solution["selected_by_bip"] == 1].copy()

    return pd.DataFrame(
        [
            {
                "solver_used": solver_used,
                "enumeration_threshold": enumeration_threshold,
                "total_candidates": len(solution),
                "selected_interventions": len(selected),
                "total_budget": total_budget,
                "used_budget": selected["intervention_cost"].sum(),
                "max_interventions": max_interventions,
                "estimated_total_avoided_impact": selected["benefit"].sum(),
                "selected_train_interventions": int((selected["intervention_type"] == "train").sum()),
                "selected_location_interventions": int((selected["intervention_type"] == "location").sum()),
            }
        ]
    )


def print_solution(solution: pd.DataFrame, summary: pd.DataFrame) -> None:
    print("\nBinary intervention optimisation summary:")
    print(summary)

    selected = solution[solution["selected_by_bip"] == 1].copy()

    if selected.empty:
        print("\nNo interventions selected under the current budget.")
        return

    print("\nSelected interventions:")
    print(
        selected[
            [
                "intervention_type",
                "target",
                "benefit",
                "intervention_cost",
                "affected_count",
                "priority_score",
            ]
        ].sort_values("benefit", ascending=False)
    )


def main() -> None:
    total_budget = 8.0
    max_interventions = 5

    # Exact enumeration is used at or below this candidate count.
    # Above this count, the script switches to SciPy MILP.
    enumeration_threshold = 30

    # These can now be larger because the script can switch to MILP.
    max_train_candidates = 100
    max_location_candidates = 100

    candidates = load_candidates(
        max_train_candidates=max_train_candidates,
        max_location_candidates=max_location_candidates,
    )

    if candidates.empty:
        print("No candidate interventions found.")
        return

    solution, solver_used = solve_binary_program(
        candidates=candidates,
        total_budget=total_budget,
        max_interventions=max_interventions,
        enumeration_threshold=enumeration_threshold,
    )

    summary = build_summary(
        solution=solution,
        total_budget=total_budget,
        max_interventions=max_interventions,
        solver_used=solver_used,
        enumeration_threshold=enumeration_threshold,
    )

    solution = solution.sort_values(
        by=["selected_by_bip", "benefit"],
        ascending=[False, False],
    ).reset_index(drop=True)

    solution.to_csv(OUTPUT_PATH, index=False)
    summary.to_csv(SUMMARY_PATH, index=False)

    print_solution(solution, summary)

    print(f"\nSaved BIP/MILP solution to: {OUTPUT_PATH}")
    print(f"Saved optimisation summary to: {SUMMARY_PATH}")

    print("\nImportant note:")
    print(
        "This is a prototype binary optimisation model. Benefits are derived from "
        "the inferred causal propagation network, and costs are heuristic rather "
        "than real Network Rail operational intervention costs."
    )


if __name__ == "__main__":
    main()