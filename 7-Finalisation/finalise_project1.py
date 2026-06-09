import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd


DB_PATH = "data/railway.db"

OUTPUTS_DIR = Path("outputs")
TABLES_SOURCE_DIR = OUTPUTS_DIR / "tables"
FIGURES_SOURCE_DIR = OUTPUTS_DIR / "figures"

FINAL_DIR = OUTPUTS_DIR / "final"
FINAL_TABLES_DIR = FINAL_DIR / "tables"
FINAL_CASE_DIR = FINAL_DIR / "case_studies"
FINAL_SUMMARY_DIR = FINAL_DIR / "summaries"

for folder in [FINAL_DIR, FINAL_TABLES_DIR, FINAL_CASE_DIR, FINAL_SUMMARY_DIR]:
    folder.mkdir(parents=True, exist_ok=True)


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    query = """
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
      AND name = ?;
    """
    result = pd.read_sql_query(query, conn, params=(table_name,))
    return not result.empty


def query_df(conn: sqlite3.Connection, query: str, params=None) -> pd.DataFrame:
    if params is None:
        params = ()
    return pd.read_sql_query(query, conn, params=params)


def safe_query(conn: sqlite3.Connection, table_name: str, query: str) -> pd.DataFrame:
    if not table_exists(conn, table_name):
        print(f"Skipping missing database table: {table_name}")
        return pd.DataFrame()

    return query_df(conn, query)


def read_csv_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"Skipping missing CSV file: {path}")
        return pd.DataFrame()
    return pd.read_csv(path)


def export_csv(df: pd.DataFrame, filename: str) -> Optional[Path]:
    if df.empty:
        return None

    path = FINAL_TABLES_DIR / filename
    df.to_csv(path, index=False)
    return path


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def ensure_case_summary_table(conn: sqlite3.Connection) -> None:
    """
    Rebuilds propagation_case_summary using train_movements.

    This keeps the finalisation script self-contained for the older
    train-level case summaries.
    """
    if not table_exists(conn, "train_movements"):
        print("Cannot build propagation_case_summary because train_movements is missing.")
        return

    conn.execute("DROP TABLE IF EXISTS propagation_case_summary;")

    conn.execute(
        """
        CREATE TABLE propagation_case_summary AS
        WITH base AS (
            SELECT
                train_id,
                actual_time_utc,
                timetable_variation,
                toc_id,
                train_service_code
            FROM train_movements
            WHERE train_id IS NOT NULL
              AND actual_time_utc IS NOT NULL
              AND timetable_variation IS NOT NULL
        ),
        ordered AS (
            SELECT
                train_id,
                actual_time_utc,
                timetable_variation,
                toc_id,
                train_service_code,
                LAG(timetable_variation) OVER (
                    PARTITION BY train_id
                    ORDER BY actual_time_utc
                ) AS previous_variation
            FROM base
        ),
        changes AS (
            SELECT
                train_id,
                actual_time_utc,
                timetable_variation,
                toc_id,
                train_service_code,
                previous_variation,
                timetable_variation - previous_variation AS variation_change
            FROM ordered
        ),
        summary AS (
            SELECT
                train_id,
                COUNT(*) AS n_events,
                MIN(actual_time_utc) AS first_actual_time,
                MAX(actual_time_utc) AS last_actual_time,
                MIN(timetable_variation) AS min_variation,
                MAX(timetable_variation) AS max_variation,
                MIN(toc_id) AS toc_id,
                MIN(train_service_code) AS train_service_code
            FROM changes
            GROUP BY train_id
        ),
        starts AS (
            SELECT
                c.train_id,
                c.timetable_variation AS start_variation
            FROM changes c
            JOIN (
                SELECT train_id, MIN(actual_time_utc) AS first_time
                FROM changes
                GROUP BY train_id
            ) f
              ON c.train_id = f.train_id
             AND c.actual_time_utc = f.first_time
        ),
        ends AS (
            SELECT
                c.train_id,
                c.timetable_variation AS end_variation
            FROM changes c
            JOIN (
                SELECT train_id, MAX(actual_time_utc) AS last_time
                FROM changes
                GROUP BY train_id
            ) f
              ON c.train_id = f.train_id
             AND c.actual_time_utc = f.last_time
        ),
        change_stats AS (
            SELECT
                train_id,
                MAX(variation_change) AS max_single_increase,
                MIN(variation_change) AS max_single_decrease,
                SUM(CASE WHEN variation_change > 0 THEN 1 ELSE 0 END) AS n_increases,
                SUM(CASE WHEN variation_change < 0 THEN 1 ELSE 0 END) AS n_decreases,
                SUM(CASE WHEN variation_change = 0 THEN 1 ELSE 0 END) AS n_same
            FROM changes
            GROUP BY train_id
        )
        SELECT
            s.train_id,
            s.n_events,
            st.start_variation,
            en.end_variation,
            s.max_variation,
            s.min_variation,
            s.first_actual_time,
            s.last_actual_time,
            s.toc_id,
            s.train_service_code,
            cs.max_single_increase,
            cs.max_single_decrease,
            cs.n_increases,
            cs.n_decreases,
            cs.n_same,
            (en.end_variation - st.start_variation) AS net_change,
            (s.max_variation - s.min_variation) AS variation_range,
            CASE
                WHEN COALESCE(cs.max_single_increase, 0) >= 10
                     AND COALESCE(cs.n_increases, 0) <= 2
                    THEN 'SUDDEN_DISRUPTION'
                WHEN COALESCE(cs.n_increases, 0) >= 2
                     AND (en.end_variation - st.start_variation) > 0
                     AND COALESCE(cs.max_single_increase, 0) < 10
                    THEN 'PROPAGATION'
                WHEN COALESCE(cs.n_increases, 0) >= 2
                     AND (en.end_variation - st.start_variation) > 0
                     AND COALESCE(cs.max_single_increase, 0) >= 10
                    THEN 'MIXED'
                ELSE 'STABLE_OR_RECOVERING'
            END AS case_type,
            (
                COALESCE(cs.max_single_increase, 0)
                + MAX((en.end_variation - st.start_variation), 0)
            ) AS sudden_score,
            (
                MAX((en.end_variation - st.start_variation), 0)
                + 0.5 * COALESCE(cs.n_increases, 0)
                - 0.2 * COALESCE(cs.max_single_increase, 0)
            ) AS propagation_score
        FROM summary s
        JOIN starts st ON s.train_id = st.train_id
        JOIN ends en ON s.train_id = en.train_id
        JOIN change_stats cs ON s.train_id = cs.train_id
        WHERE s.n_events >= 5;
        """
    )
    conn.commit()


def load_database_outputs(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    outputs = {}

    outputs["top_causal_edges"] = safe_query(
        conn,
        "causal_propagation_edges",
        """
        SELECT *
        FROM causal_propagation_edges
        ORDER BY edge_weight DESC
        LIMIT 50;
        """
    )

    outputs["top_causal_locations"] = safe_query(
        conn,
        "causal_propagation_locations",
        """
        SELECT *
        FROM causal_propagation_locations
        ORDER BY location_causal_score DESC
        LIMIT 50;
        """
    )

    outputs["top_causal_nodes"] = safe_query(
        conn,
        "causal_propagation_nodes",
        """
        SELECT *
        FROM causal_propagation_nodes
        ORDER BY total_weight DESC
        LIMIT 50;
        """
    )

    outputs["top_propagation_cases"] = safe_query(
        conn,
        "propagation_case_summary",
        """
        SELECT *
        FROM propagation_case_summary
        WHERE case_type = 'PROPAGATION'
        ORDER BY propagation_score DESC
        LIMIT 20;
        """
    )

    outputs["top_sudden_disruption_cases"] = safe_query(
        conn,
        "propagation_case_summary",
        """
        SELECT *
        FROM propagation_case_summary
        WHERE case_type = 'SUDDEN_DISRUPTION'
        ORDER BY sudden_score DESC
        LIMIT 20;
        """
    )

    return outputs


def load_file_outputs() -> dict[str, pd.DataFrame]:
    files = {
        "validation_control_comparison": TABLES_SOURCE_DIR / "validation_control_comparison.csv",
        "validation_threshold_sensitivity": TABLES_SOURCE_DIR / "validation_threshold_sensitivity.csv",
        "validation_negative_tests": TABLES_SOURCE_DIR / "validation_negative_tests.csv",
        "recovery_intervention_plan_by_train": TABLES_SOURCE_DIR / "recovery_intervention_plan_by_train.csv",
        "recovery_intervention_plan_by_location": TABLES_SOURCE_DIR / "recovery_intervention_plan_by_location.csv",
        "recovery_intervention_summary": TABLES_SOURCE_DIR / "recovery_intervention_summary.csv",
        "recovery_intervention_bip_solution": TABLES_SOURCE_DIR / "recovery_intervention_bip_solution.csv",
        "recovery_intervention_bip_summary": TABLES_SOURCE_DIR / "recovery_intervention_bip_summary.csv",
    }

    return {name: read_csv_if_exists(path) for name, path in files.items()}


def export_all_tables(outputs: dict[str, pd.DataFrame]) -> dict[str, Path]:
    exported = {}

    for name, df in outputs.items():
        path = export_csv(df, f"{name}.csv")
        if path is not None:
            exported[name] = path

    return exported


def safe_count(conn: sqlite3.Connection, table_name: str) -> Optional[int]:
    if not table_exists(conn, table_name):
        return None
    return int(query_df(conn, f"SELECT COUNT(*) AS n FROM {table_name};")["n"].iloc[0])


def build_project_summary(
    conn: sqlite3.Connection,
    outputs: dict[str, pd.DataFrame],
) -> str:
    n_movements = safe_count(conn, "train_movements")
    n_causal_edges = safe_count(conn, "causal_propagation_edges")
    n_causal_nodes = safe_count(conn, "causal_propagation_nodes")
    n_causal_locations = safe_count(conn, "causal_propagation_locations")

    top_edge = outputs.get("top_causal_edges", pd.DataFrame())
    top_location = outputs.get("top_causal_locations", pd.DataFrame())
    top_node = outputs.get("top_causal_nodes", pd.DataFrame())

    lines = [
        "# Project 1 Final Summary: Railway Delay Propagation and Recovery Optimisation",
        "",
        "## Overview",
        "",
        "This project builds an end-to-end railway delay propagation pipeline using Network Rail timetable and train movement data.",
        "",
        "The completed project includes:",
        "",
        "- timetable and movement-data ingestion,",
        "- SQLite database construction,",
        "- train-level delay analysis,",
        "- location-level delay hotspot analysis,",
        "- candidate train-to-train propagation detection,",
        "- causal-style propagation network construction,",
        "- validation using local controls and robustness checks,",
        "- visualisation of propagation patterns,",
        "- recovery intervention prioritisation,",
        "- and a small binary optimisation layer for constrained recovery decision support.",
        "",
        "## Key project metrics",
        "",
    ]

    if n_movements is not None:
        lines.append(f"- Movement events loaded: **{n_movements}**")
    if n_causal_edges is not None:
        lines.append(f"- Directed causal propagation edges: **{n_causal_edges}**")
    if n_causal_nodes is not None:
        lines.append(f"- Trains in causal propagation network: **{n_causal_nodes}**")
    if n_causal_locations is not None:
        lines.append(f"- Causal propagation locations: **{n_causal_locations}**")

    lines.append("")

    if not top_edge.empty:
        row = top_edge.iloc[0]
        lines.extend(
            [
                "## Strongest causal propagation edge",
                "",
                f"- Source train: **{row.get('source_train_id', 'N/A')}**",
                f"- Affected train: **{row.get('affected_train_id', 'N/A')}**",
                f"- Sample location: **{row.get('sample_location', 'N/A')}**",
                f"- Edge weight: **{float(row.get('edge_weight', 0)):.2f}**",
                "",
            ]
        )

    if not top_location.empty:
        row = top_location.iloc[0]
        lines.extend(
            [
                "## Strongest causal propagation location",
                "",
                f"- Location: **{row.get('location_name', 'N/A')}**",
                f"- Location causal score: **{float(row.get('location_causal_score', 0)):.2f}**",
                "",
            ]
        )

    if not top_node.empty:
        row = top_node.iloc[0]
        lines.extend(
            [
                "## Most central train in causal network",
                "",
                f"- Train ID: **{row.get('train_id', 'N/A')}**",
                f"- Node role: **{row.get('node_role', 'N/A')}**",
                f"- Total network weight: **{float(row.get('total_weight', 0)):.2f}**",
                "",
            ]
        )

    lines.extend(
        [
            "## Interpretation",
            "",
            "The project infers likely knock-on delay pathways by combining temporal ordering, shared locations, delay severity, post-interaction worsening, and network structure.",
            "",
            "The final optimisation layer uses this inferred propagation network to prioritise recovery interventions under limited resources.",
            "",
            "## Limitations",
            "",
            "The project uses publicly available movement and timetable data. It does not directly observe signalling block occupation, route setting, dispatcher decisions, crew diagrams, rolling-stock diagrams, or exact platform conflicts.",
            "",
            "Therefore, the causal and optimisation outputs should be interpreted as decision-support hypotheses rather than verified operational instructions.",
            "",
        ]
    )

    return "\n".join(lines)


def build_validation_summary(outputs: dict[str, pd.DataFrame]) -> str:
    controls = outputs.get("validation_control_comparison", pd.DataFrame())
    sensitivity = outputs.get("validation_threshold_sensitivity", pd.DataFrame())
    negative = outputs.get("validation_negative_tests", pd.DataFrame())

    lines = [
        "# Validation Summary",
        "",
        "## Purpose",
        "",
        "The validation layer tests whether inferred delay-propagation relationships are stronger than nearby local alternatives and whether the findings are robust to threshold choices.",
        "",
    ]

    if not controls.empty and "control_result" in controls.columns:
        lines.extend(
            [
                "## Control comparison results",
                "",
                f"- Cases evaluated: **{len(controls)}**",
                "",
            ]
        )

        counts = controls["control_result"].value_counts()
        for label, value in counts.items():
            lines.append(f"- {label}: **{value}**")

        lines.append("")

    if not sensitivity.empty:
        lines.extend(
            [
                "## Threshold sensitivity",
                "",
                "The threshold-sensitivity table records how propagation outputs change under alternative time-gap and delay-threshold assumptions.",
                "",
                f"- Sensitivity settings tested: **{len(sensitivity)}**",
                "",
            ]
        )

    if not negative.empty:
        lines.extend(
            [
                "## Negative/control stress tests",
                "",
                "The negative-control tests compare the normal same-direction propagation logic against less plausible cases such as opposite-direction or larger-gap interactions.",
                "",
                f"- Negative/control settings tested: **{len(negative)}**",
                "",
            ]
        )

    lines.extend(
        [
            "## Interpretation",
            "",
            "The validation outputs do not prove operational causality, but they provide evidence that the inferred propagation cases are stronger than simple co-location or random local delay patterns.",
            "",
        ]
    )

    return "\n".join(lines)


def build_recovery_summary(outputs: dict[str, pd.DataFrame]) -> str:
    train_plan = outputs.get("recovery_intervention_plan_by_train", pd.DataFrame())
    location_plan = outputs.get("recovery_intervention_plan_by_location", pd.DataFrame())
    recovery_summary = outputs.get("recovery_intervention_summary", pd.DataFrame())

    lines = [
        "# Recovery Optimisation Summary",
        "",
        "## Purpose",
        "",
        "The recovery optimisation layer uses the causal propagation network to prioritise trains and locations for intervention under limited resources.",
        "",
        "This is a prototype network-based decision-support model, not a full operational control-room optimiser.",
        "",
    ]

    if not recovery_summary.empty:
        lines.extend(["## Recovery model summary", ""])
        for _, row in recovery_summary.iterrows():
            lines.append(f"- Intervention type: **{row.get('intervention_type', 'N/A')}**")
            lines.append(f"  - Budget: **{row.get('budget', 'N/A')}**")
            lines.append(f"  - Selected interventions: **{row.get('n_selected', 'N/A')}**")
            lines.append(
                f"  - Estimated avoided impact: **{float(row.get('estimated_total_avoided_impact', 0)):.2f}**"
            )
        lines.append("")

    if not train_plan.empty:
        selected = train_plan[train_plan.get("selected_for_intervention", 0) == 1]
        lines.extend(
            [
                "## Selected train-level interventions",
                "",
            ]
        )

        for _, row in selected.head(10).iterrows():
            lines.append(
                f"- {row.get('source_train_id', 'N/A')} "
                f"(estimated avoided impact: {float(row.get('estimated_avoided_downstream_impact', 0)):.2f})"
            )

        lines.append("")

    if not location_plan.empty:
        selected = location_plan[location_plan.get("selected_for_intervention", 0) == 1]
        lines.extend(
            [
                "## Selected location-level interventions",
                "",
            ]
        )

        for _, row in selected.head(10).iterrows():
            lines.append(
                f"- {row.get('location_name', 'N/A')} "
                f"(estimated avoided impact: {float(row.get('estimated_avoided_downstream_impact', 0)):.2f})"
            )

        lines.append("")

    lines.extend(
        [
            "## Interpretation",
            "",
            "This layer moves the project from descriptive analysis toward operations-research decision support by asking which trains or locations should be prioritised to reduce expected downstream delay.",
            "",
        ]
    )

    return "\n".join(lines)


def build_bip_summary(outputs: dict[str, pd.DataFrame]) -> str:
    solution = outputs.get("recovery_intervention_bip_solution", pd.DataFrame())
    summary = outputs.get("recovery_intervention_bip_summary", pd.DataFrame())

    lines = [
        "# Binary Optimisation Summary",
        "",
        "## Purpose",
        "",
        "The binary optimisation layer selects a subset of candidate recovery interventions subject to budget and maximum-intervention constraints.",
        "",
        "Each candidate intervention is represented by a binary decision variable:",
        "",
        "```text",
        "x_i = 1 if intervention i is selected",
        "x_i = 0 otherwise",
        "```",
        "",
        "The objective is to maximise estimated avoided downstream delay.",
        "",
    ]

    if not summary.empty:
        row = summary.iloc[0]
        lines.extend(
            [
                "## Optimisation result",
                "",
                f"- Solver used: **{row.get('solver_used', 'N/A')}**",
                f"- Total candidates: **{row.get('total_candidates', 'N/A')}**",
                f"- Selected interventions: **{row.get('selected_interventions', 'N/A')}**",
                f"- Total budget: **{row.get('total_budget', 'N/A')}**",
                f"- Used budget: **{float(row.get('used_budget', 0)):.2f}**",
                f"- Estimated total avoided impact: **{float(row.get('estimated_total_avoided_impact', 0)):.2f}**",
                "",
            ]
        )

    if not solution.empty and "selected_by_bip" in solution.columns:
        selected = solution[solution["selected_by_bip"] == 1]
        lines.extend(["## Selected BIP/MILP interventions", ""])

        for _, row in selected.iterrows():
            lines.append(
                f"- {row.get('intervention_type', 'N/A')}: {row.get('target', 'N/A')} "
                f"(benefit: {float(row.get('benefit', 0)):.2f}, "
                f"cost: {float(row.get('intervention_cost', 0)):.2f})"
            )

        lines.append("")

    lines.extend(
        [
            "## Interpretation",
            "",
            "For small candidate sets the script can solve the binary optimisation by exact enumeration. For larger candidate sets it can switch to a MILP approach if the required solver is available.",
            "",
            "The optimisation is conceptually meaningful as a recovery-prioritisation model, but its costs are heuristic and should not be interpreted as real Network Rail intervention costs.",
            "",
        ]
    )

    return "\n".join(lines)


def build_case_study_top_edge(outputs: dict[str, pd.DataFrame]) -> str:
    df = outputs.get("top_causal_edges", pd.DataFrame())
    if df.empty:
        return "# Top causal edge case study\n\nNo data available.\n"

    row = df.iloc[0]

    return f"""# Case Study: Strongest Causal Propagation Edge

## Summary

This case study describes the strongest detected causal-style propagation edge in the project output.

## Edge details

- Source train: **{row.get('source_train_id', 'N/A')}**
- Affected train: **{row.get('affected_train_id', 'N/A')}**
- Sample location: **{row.get('sample_location', 'N/A')}**
- Edge weight: **{float(row.get('edge_weight', 0)):.2f}**
- Number of causal events: **{row.get('n_causal_events', 'N/A')}**
- Number of locations involved: **{row.get('n_locations', 'N/A')}**
- Mean post-worsening: **{float(row.get('mean_post_worsening', 0)):.2f}**

## Interpretation

This edge represents the strongest train-to-train delay transmission relationship found by the causal propagation model. It is a useful candidate for an interview explanation, project report, or presentation slide.
"""


def build_case_study_top_propagation(outputs: dict[str, pd.DataFrame]) -> str:
    df = outputs.get("top_propagation_cases", pd.DataFrame())
    if df.empty:
        return "# Top propagation train case study\n\nNo data available.\n"

    row = df.iloc[0]

    return f"""# Case Study: Strongest Propagation Train

## Summary

This case study describes the highest-ranked train classified as a propagation case.

## Train details

- Train ID: **{row.get('train_id', 'N/A')}**
- Number of events: **{row.get('n_events', 'N/A')}**
- Start variation: **{row.get('start_variation', 'N/A')}**
- End variation: **{row.get('end_variation', 'N/A')}**
- Net change: **{row.get('net_change', 'N/A')}**
- Max single increase: **{row.get('max_single_increase', 'N/A')}**
- Number of increases: **{row.get('n_increases', 'N/A')}**
- Propagation score: **{float(row.get('propagation_score', 0)):.2f}**

## Interpretation

This train shows repeated delay growth across its observed event sequence, making it a strong example of gradual delay accumulation rather than a single sudden disruption.
"""


def build_case_study_top_disruption(outputs: dict[str, pd.DataFrame]) -> str:
    df = outputs.get("top_sudden_disruption_cases", pd.DataFrame())
    if df.empty:
        return "# Top sudden disruption train case study\n\nNo data available.\n"

    row = df.iloc[0]

    return f"""# Case Study: Strongest Sudden Disruption Train

## Summary

This case study describes the highest-ranked sudden disruption case.

## Train details

- Train ID: **{row.get('train_id', 'N/A')}**
- Number of events: **{row.get('n_events', 'N/A')}**
- Start variation: **{row.get('start_variation', 'N/A')}**
- End variation: **{row.get('end_variation', 'N/A')}**
- Net change: **{row.get('net_change', 'N/A')}**
- Max single increase: **{row.get('max_single_increase', 'N/A')}**
- Sudden score: **{float(row.get('sudden_score', 0)):.2f}**

## Interpretation

This train appears to experience a large one-off increase in delay rather than repeated gradual worsening. It is a useful contrast case against gradual propagation examples.
"""


def build_manifest(exported: dict[str, Path]) -> str:
    lines = [
        "# Final Output Manifest",
        "",
        "This file lists the final artefacts generated by `finalise_project1.py`.",
        "",
        "## Exported tables",
        "",
    ]

    if exported:
        for name, path in exported.items():
            lines.append(f"- **{name}**: `{path}`")
    else:
        lines.append("- No tables exported.")

    lines.extend(
        [
            "",
            "## Summary documents",
            "",
            f"- `{FINAL_DIR / 'project1_summary.md'}`",
            f"- `{FINAL_SUMMARY_DIR / 'validation_summary.md'}`",
            f"- `{FINAL_SUMMARY_DIR / 'recovery_optimisation_summary.md'}`",
            f"- `{FINAL_SUMMARY_DIR / 'binary_optimisation_summary.md'}`",
            "",
            "## Case studies",
            "",
            f"- `{FINAL_CASE_DIR / 'top_causal_edge.md'}`",
            f"- `{FINAL_CASE_DIR / 'top_propagation_train.md'}`",
            f"- `{FINAL_CASE_DIR / 'top_sudden_disruption_train.md'}`",
            "",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)

    try:
        ensure_case_summary_table(conn)

        db_outputs = load_database_outputs(conn)
        file_outputs = load_file_outputs()

        outputs = {**db_outputs, **file_outputs}

        exported = export_all_tables(outputs)

        write_markdown(
            FINAL_DIR / "project1_summary.md",
            build_project_summary(conn, outputs),
        )

        write_markdown(
            FINAL_SUMMARY_DIR / "validation_summary.md",
            build_validation_summary(outputs),
        )

        write_markdown(
            FINAL_SUMMARY_DIR / "recovery_optimisation_summary.md",
            build_recovery_summary(outputs),
        )

        write_markdown(
            FINAL_SUMMARY_DIR / "binary_optimisation_summary.md",
            build_bip_summary(outputs),
        )

        write_markdown(
            FINAL_CASE_DIR / "top_causal_edge.md",
            build_case_study_top_edge(outputs),
        )

        write_markdown(
            FINAL_CASE_DIR / "top_propagation_train.md",
            build_case_study_top_propagation(outputs),
        )

        write_markdown(
            FINAL_CASE_DIR / "top_sudden_disruption_train.md",
            build_case_study_top_disruption(outputs),
        )

        write_markdown(
            FINAL_DIR / "final_output_manifest.md",
            build_manifest(exported),
        )

    finally:
        conn.close()

    print("Project 1 finalisation outputs written.")
    print(f"Final directory: {FINAL_DIR}")
    print(f"Final tables directory: {FINAL_TABLES_DIR}")
    print(f"Final summaries directory: {FINAL_SUMMARY_DIR}")
    print(f"Case study directory: {FINAL_CASE_DIR}")

    print("\nCSV exports:")
    for csv_file in sorted(FINAL_TABLES_DIR.glob("*.csv")):
        print(f" - {csv_file}")

    print("\nMarkdown outputs:")
    for md_file in sorted(FINAL_DIR.rglob("*.md")):
        print(f" - {md_file}")


if __name__ == "__main__":
    main()