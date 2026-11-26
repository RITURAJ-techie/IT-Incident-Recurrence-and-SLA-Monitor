"""End-to-end pipeline: raw files -> checks -> cleaned/combined files -> database -> report.

Run on its own with ``python -m incident_monitor.pipeline`` (``run.py`` calls it
for you before starting the web app).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from . import analysis, cleaning, config, database, reporting


def save_cleaned(data: cleaning.CleanedData, enriched: pd.DataFrame,
                 out_dir: Path = config.PROCESSED_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "cleaned_incidents.csv": data.incidents,
        "cleaned_assets.csv": data.assets,
        "cleaned_sla_targets.csv": data.sla_targets,
        "cleaned_changes.csv": data.changes,
        # one row per ticket with asset, service, SLA, recurrence and change columns
        "incidents_combined.csv": enriched,
    }
    paths = []
    for name, df in files.items():
        p = out_dir / name
        df.to_csv(p, index=False, date_format=config.DATETIME_FORMAT)
        paths.append(p)
    return paths


def generate_outputs(quality_log: pd.DataFrame | None = None, raw_counts: dict | None = None,
                     clean_counts: dict | None = None,
                     db_path: Path | str = config.DB_PATH) -> dict:
    """Build charts, summary CSVs and the report from the current database."""
    if quality_log is None:
        log_path = config.OUTPUT_DIR / "data_quality_log.csv"
        quality_log = pd.read_csv(log_path) if log_path.exists() else None
    tables = database.load_all(db_path)
    enriched = analysis.enrich_incidents(tables["incidents"], tables["assets"],
                                         tables["sla_targets"], tables["changes"])
    charts = reporting.save_charts(enriched)
    summaries = reporting.save_summary_tables(enriched)
    report = reporting.write_report(enriched, quality_log, raw_counts=raw_counts,
                                    clean_counts=clean_counts)
    return {"kpis": analysis.kpis(enriched), "charts": charts, "summaries": summaries,
            "report": report}


def run(reset_db: bool = False, db_path: Path | str = config.DB_PATH, verbose: bool = True) -> dict:
    say = print if verbose else (lambda *a, **k: None)

    say("[1/4] Loading raw files and running data-quality checks ...")
    data = cleaning.clean_all()
    qlog = data.log.to_frame()
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    qlog.to_csv(config.OUTPUT_DIR / "data_quality_log.csv", index=False)
    for _, row in data.log.summary().iterrows():
        say(f"      {row['dataset']:<12} {row['check']:<16} {row['count']:>3}")

    say("[2/4] Combining and saving cleaned data to data/processed/ ...")
    combined = analysis.enrich_incidents(data.incidents, data.assets, data.sla_targets,
                                         data.changes)
    save_cleaned(data, combined)

    clean_counts = {"incidents": len(data.incidents), "assets": len(data.assets),
                    "sla_targets": len(data.sla_targets), "changes": len(data.changes)}
    if reset_db or not database.database_exists(db_path):
        say(f"[3/4] Creating database {Path(db_path).name} from cleaned data ...")
        database.seed_database(data, db_path)
    else:
        say(f"[3/4] Keeping existing database {Path(db_path).name} "
            "(your edits are preserved; use --reset to rebuild).")

    say("[4/4] Calculating summaries, drawing charts and writing outputs/report.md ...")
    result = generate_outputs(qlog, data.raw_counts, clean_counts, db_path)
    k = result["kpis"]
    say(f"      tickets={k['total_tickets']}  SLA compliance={k['sla_compliance_pct']}%  "
        f"recurrence={k['recurrence_rate_pct']}%  change-related={k['change_related_tickets']}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the data pipeline only.")
    parser.add_argument("--reset", action="store_true", help="rebuild the database from raw data")
    args = parser.parse_args()
    run(reset_db=args.reset)


if __name__ == "__main__":
    main()
