from __future__ import annotations

import html
import sqlite3
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pandas as pd


ProgressCallback = Callable[[str], None]
REPORT_TABLES = {
    "Propagation edges": "causal_propagation_edges",
    "Propagation locations": "causal_propagation_locations",
    "Validation controls": "validation_control_results",
    "Validation sensitivity": "validation_threshold_sensitivity",
    "Validation negative tests": "validation_negative_tests",
    "Recovery train plan": "recovery_intervention_plan_by_train",
    "Recovery location plan": "recovery_intervention_plan_by_location",
    "Recovery solution": "recovery_intervention_solution",
    "Recovery summary": "recovery_intervention_summary",
}


def export_report_archive(report_path: Path, downloads_directory: Path) -> Path:
    """Copy an HTML report and its CSV folder into one portable ZIP archive."""
    if not report_path.exists():
        raise FileNotFoundError("The latest generated report could not be found")
    report_directory = report_path.parent
    downloads_directory.mkdir(parents=True, exist_ok=True)
    destination = downloads_directory / f"{report_directory.name}.zip"
    copy_number = 2
    while destination.exists():
        destination = downloads_directory / (
            f"{report_directory.name}-copy-{copy_number}.zip"
        )
        copy_number += 1
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for file_path in report_directory.rglob("*"):
            if file_path.is_file():
                archive.write(
                    file_path,
                    arcname=Path(report_directory.name)
                    / file_path.relative_to(report_directory),
                )
    return destination


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def inspect_report_sources(database_path: Path) -> dict[str, object]:
    if not database_path.exists():
        return {"available": [], "missing": list(REPORT_TABLES), "metrics": {}}
    connection = sqlite3.connect(database_path)
    try:
        existing = _tables(connection)
        available = [label for label, table in REPORT_TABLES.items() if table in existing]
        missing = [label for label, table in REPORT_TABLES.items() if table not in existing]
        metrics = {}
        if "train_movements" in existing:
            metrics["movement_events"] = connection.execute("SELECT COUNT(*) FROM train_movements").fetchone()[0]
            metrics["trains"] = connection.execute("SELECT COUNT(DISTINCT train_id) FROM train_movements").fetchone()[0]
        if "causal_propagation_edges" in existing:
            metrics["edges"] = connection.execute("SELECT COUNT(*) FROM causal_propagation_edges").fetchone()[0]
        if "validation_control_results" in existing:
            metrics["validated"] = connection.execute("SELECT COUNT(*) FROM validation_control_results").fetchone()[0]
        if "recovery_intervention_solution" in existing:
            metrics["selected"] = connection.execute(
                "SELECT COALESCE(SUM(selected_by_bip),0) FROM recovery_intervention_solution"
            ).fetchone()[0]
    finally:
        connection.close()
    return {"available": available, "missing": missing, "metrics": metrics}


def generate_report(database_path: Path, reports_root: Path,
                    progress: ProgressCallback = lambda _: None) -> Path:
    if not database_path.exists(): raise FileNotFoundError("railway.db does not exist")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = reports_root / f"railconnect-report-{stamp}"
    tables_dir = report_dir / "tables"; tables_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        existing = _tables(connection)
        progress("Collecting available database outputs")
        frames: dict[str, pd.DataFrame] = {}
        for label, table in REPORT_TABLES.items():
            if table in existing:
                frame = pd.read_sql_query(f'SELECT * FROM "{table}"', connection)
                frames[label] = frame
                frame.to_csv(tables_dir / f"{table}.csv", index=False)
                progress(f"Exported {label} ({len(frame):,} rows)")
        source = inspect_report_sources(database_path)
    finally:
        connection.close()
    css = """
    *{box-sizing:border-box}html,body{max-width:100%;overflow-x:hidden}
    body{font-family:Segoe UI,Arial,sans-serif;background:#dce7f4;color:#1b2d50;margin:0}
    header{background:linear-gradient(135deg,#173c8e,#587edc);color:white;padding:34px 7%}
    main{max-width:1100px;margin:24px auto;padding:0 22px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
    .card,section{background:#f7faff;border:1px solid #8798b5;border-radius:12px;padding:18px;margin-bottom:18px;min-width:0}
    .metric{font:700 28px Consolas;color:#214ba8}.label{color:#61708b;font-size:13px}
    .table-scroll{width:100%;max-width:100%;overflow-x:auto;background:#f7faff;border:1px solid #aab8cc;border-radius:7px}
    table{border-collapse:collapse;width:max-content;min-width:100%;font-size:12px;background:#f7faff}
    th{background:#405d97;color:white;white-space:nowrap;position:sticky;top:0}
    th,td{padding:8px;border:1px solid #bec9da;text-align:left;white-space:nowrap}
    tbody tr:nth-child(even){background:#e9f0f8}tbody tr:nth-child(odd){background:#f7faff}
    .note{background:#f2e5b9;border-color:#b79a55}footer{padding:20px;text-align:center;color:#68778e}@media(max-width:750px){.grid{grid-template-columns:1fr 1fr}}
    """
    cards = "".join(
        f'<div class="card"><div class="label">{html.escape(key.replace("_", " ").title())}</div><div class="metric">{int(value):,}</div></div>'
        for key, value in source["metrics"].items()
    ) or '<div class="card">No summary metrics available.</div>'
    sections = []
    for label, frame in frames.items():
        display = frame.head(20).copy()
        sections.append(
            f"<section><h2>{html.escape(label)}</h2>"
            f"<p>{len(frame):,} rows exported to CSV. Scroll within the table to view additional columns.</p>"
            f'<div class="table-scroll">'
            f"{display.to_html(index=False, border=0, escape=True)}"
            f"</div></section>"
        )
    caveat = ("Propagation, validation and recovery outputs are analytical decision-support hypotheses. "
              "They do not establish operational causation, and recovery costs and effectiveness are modelling assumptions.")
    document = f"""<!doctype html><html><head><meta charset="utf-8"><title>RailConnect 2000 Report</title><style>{css}</style></head>
    <body><header><h1>RailConnect 2000</h1><p>Network Rail Delay Propagation System · Analysis Report</p>
    <p>Generated {datetime.now().strftime('%d %B %Y at %H:%M')}</p></header><main>
    <div class="grid">{cards}</div><section class="note"><strong>Interpretation notice.</strong> {html.escape(caveat)}</section>
    {''.join(sections)}<section><h2>Export manifest</h2><p>{len(frames)} database tables are included in the accompanying <code>tables</code> folder.</p></section>
    </main><footer>Generated locally by RailConnect 2000</footer></body></html>"""
    report_path = report_dir / "railconnect_report.html"
    report_path.write_text(document, encoding="utf-8")
    progress(f"Generated report at {report_path}")
    return report_path
