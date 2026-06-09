from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

TABLE_DIR = Path("outputs/tables")
FIGURE_DIR = Path("outputs/figures")
FIGURE_DIR.mkdir(parents=True, exist_ok=True)

TRAIN_PLAN_PATH = TABLE_DIR / "recovery_intervention_plan_by_train.csv"
LOCATION_PLAN_PATH = TABLE_DIR / "recovery_intervention_plan_by_location.csv"

TRAIN_FIGURE_PATH = FIGURE_DIR / "recovery_interventions_by_train.png"
LOCATION_FIGURE_PATH = FIGURE_DIR / "recovery_interventions_by_location.png"


def load_train_plan() -> pd.DataFrame:
    if not TRAIN_PLAN_PATH.exists():
        raise FileNotFoundError(
            f"Could not find {TRAIN_PLAN_PATH}. "
            "Run optimise_recovery_interventions.py first."
        )

    return pd.read_csv(TRAIN_PLAN_PATH)


def load_location_plan() -> pd.DataFrame:
    if not LOCATION_PLAN_PATH.exists():
        raise FileNotFoundError(
            f"Could not find {LOCATION_PLAN_PATH}. "
            "Run optimise_recovery_interventions.py first."
        )

    return pd.read_csv(LOCATION_PLAN_PATH)


def plot_train_interventions(df: pd.DataFrame, top_n: int = 10) -> None:
    """
    Plot the top train-level recovery interventions by estimated avoided impact.
    """
    if df.empty:
        print("Train intervention plan is empty.")
        return

    top = df.sort_values(
        "estimated_avoided_downstream_impact",
        ascending=False,
    ).head(top_n).copy()

    top = top.sort_values("estimated_avoided_downstream_impact", ascending=True)

    labels = [
        f"{row.source_train_id}"
        + (" *" if row.selected_for_intervention == 1 else "")
        for _, row in top.iterrows()
    ]

    plt.figure(figsize=(11, 7))
    plt.barh(labels, top["estimated_avoided_downstream_impact"])
    plt.xlabel("Estimated avoided downstream impact")
    plt.ylabel("Source train")
    plt.title("Top Train-Level Recovery Interventions")
    plt.tight_layout()
    plt.savefig(TRAIN_FIGURE_PATH, dpi=300, bbox_inches="tight")
    plt.close()

    print(f"Saved train intervention visualisation to: {TRAIN_FIGURE_PATH}")


def plot_location_interventions(df: pd.DataFrame, top_n: int = 10) -> None:
    """
    Plot the top location-level recovery interventions by estimated avoided impact.
    """
    if df.empty:
        print("Location intervention plan is empty.")
        return

    top = df.sort_values(
        "estimated_avoided_downstream_impact",
        ascending=False,
    ).head(top_n).copy()

    top = top.sort_values("estimated_avoided_downstream_impact", ascending=True)

    labels = [
        f"{row.location_name}"
        + (" *" if row.selected_for_intervention == 1 else "")
        for _, row in top.iterrows()
    ]

    plt.figure(figsize=(11, 7))
    plt.barh(labels, top["estimated_avoided_downstream_impact"])
    plt.xlabel("Estimated avoided downstream impact")
    plt.ylabel("Location")
    plt.title("Top Location-Level Recovery Interventions")
    plt.tight_layout()
    plt.savefig(LOCATION_FIGURE_PATH, dpi=300, bbox_inches="tight")
    plt.close()

    print(f"Saved location intervention visualisation to: {LOCATION_FIGURE_PATH}")


def print_summary(train_df: pd.DataFrame, location_df: pd.DataFrame) -> None:
    print("\nRecovery intervention visualisation summary:")

    if not train_df.empty:
        selected_trains = train_df[train_df["selected_for_intervention"] == 1]
        print(f"Selected train-level interventions: {len(selected_trains)}")
        print(
            selected_trains[
                [
                    "rank",
                    "source_train_id",
                    "n_affected_trains",
                    "estimated_avoided_downstream_impact",
                    "intervention_priority_score",
                ]
            ]
        )

    if not location_df.empty:
        selected_locations = location_df[location_df["selected_for_intervention"] == 1]
        print(f"\nSelected location-level interventions: {len(selected_locations)}")
        print(
            selected_locations[
                [
                    "rank",
                    "location_name",
                    "n_source_trains",
                    "n_affected_trains",
                    "estimated_avoided_downstream_impact",
                    "intervention_priority_score",
                ]
            ]
        )


def main() -> None:
    top_n = 10

    train_df = load_train_plan()
    location_df = load_location_plan()

    plot_train_interventions(train_df, top_n=top_n)
    plot_location_interventions(location_df, top_n=top_n)

    print_summary(train_df, location_df)

    print("\nNote:")
    print("Rows marked with '*' are selected under the current intervention budget.")


if __name__ == "__main__":
    main()