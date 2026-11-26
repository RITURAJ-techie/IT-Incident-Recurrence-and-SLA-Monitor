"""SQLite storage: schema, seeding from cleaned data, and CRUD for incidents.

Uses only the standard-library ``sqlite3`` module so there is nothing extra to
install and the whole database is one file (``data/incident_monitor.db``).

Only *facts* are stored (the cleaned source data). Derived values such as SLA
status or recurrence are calculated by :mod:`incident_monitor.analysis` every
time they are needed, so they can never drift out of date after an edit.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pandas as pd

from . import config
from .cleaning import (CleanedData, parse_datetime, standardize_asset_id,
                       standardize_category, standardize_priority, standardize_status)

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id     TEXT PRIMARY KEY,
    asset_name   TEXT NOT NULL,
    asset_type   TEXT NOT NULL,
    service      TEXT NOT NULL,
    location     TEXT,
    criticality  TEXT CHECK (criticality IN ('High', 'Medium', 'Low')),
    owner_team   TEXT
);

CREATE TABLE IF NOT EXISTS sla_targets (
    priority                 TEXT PRIMARY KEY CHECK (priority IN ('P1','P2','P3','P4')),
    resolution_target_hours  REAL NOT NULL CHECK (resolution_target_hours > 0),
    description              TEXT
);

CREATE TABLE IF NOT EXISTS changes (
    change_id       TEXT PRIMARY KEY,
    asset_id        TEXT,
    change_type     TEXT,
    summary         TEXT,
    implemented_at  TEXT,
    state           TEXT
);

CREATE TABLE IF NOT EXISTS incidents (
    ticket_id           TEXT PRIMARY KEY,
    opened_at           TEXT NOT NULL,
    resolved_at         TEXT,
    status              TEXT NOT NULL CHECK (status IN ('Open','In Progress','Resolved','Closed')),
    priority            TEXT NOT NULL REFERENCES sla_targets(priority),
    category            TEXT NOT NULL,
    category_raw        TEXT,
    category_method     TEXT,
    short_description   TEXT NOT NULL,
    asset_id            TEXT,
    assignment_group    TEXT,
    assigned_to         TEXT,
    reassignment_count  INTEGER CHECK (reassignment_count IS NULL OR reassignment_count >= 0),
    data_quality_flags  TEXT DEFAULT '',
    updated_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_incidents_asset_category ON incidents(asset_id, category);
"""

INCIDENT_FIELDS = ["ticket_id", "opened_at", "resolved_at", "status", "priority", "category",
                   "category_raw", "category_method", "short_description", "asset_id",
                   "assignment_group", "assigned_to", "reassignment_count",
                   "data_quality_flags", "updated_at"]


class ValidationError(ValueError):
    """Raised when user input for an incident is not acceptable."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@contextmanager
def connect(db_path: Path | str = config.DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _fmt_ts(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).strftime(config.DATETIME_FORMAT)


def _none_if_na(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value.item() if hasattr(value, "item") else value


# ============================================================================ setup


def database_exists(db_path: Path | str = config.DB_PATH) -> bool:
    path = Path(db_path)
    if not path.exists():
        return False
    with connect(path) as conn:
        row = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='incidents'").fetchone()
        return bool(row[0])


def seed_database(data: CleanedData, db_path: Path | str = config.DB_PATH) -> None:
    """(Re)create all tables and load the cleaned data."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO assets VALUES (?,?,?,?,?,?,?)",
                         data.assets[["asset_id", "asset_name", "asset_type", "service",
                                      "location", "criticality", "owner_team"]]
                         .itertuples(index=False, name=None))
        conn.executemany("INSERT INTO sla_targets VALUES (?,?,?)",
                         [(p, float(h), d) for p, h, d in data.sla_targets[
                             ["priority", "resolution_target_hours", "description"]]
                          .itertuples(index=False, name=None)])
        conn.executemany("INSERT INTO changes VALUES (?,?,?,?,?,?)",
                         [(c, a, t, s, _fmt_ts(i), st) for c, a, t, s, i, st in data.changes[
                             ["change_id", "asset_id", "change_type", "summary",
                              "implemented_at", "state"]].itertuples(index=False, name=None)])
        now = datetime.now().strftime(config.DATETIME_FORMAT)
        rows = []
        for rec in data.incidents.to_dict("records"):
            rec["opened_at"] = _fmt_ts(rec["opened_at"])
            rec["resolved_at"] = _fmt_ts(rec["resolved_at"])
            rec["updated_at"] = now
            rows.append(tuple(_none_if_na(rec.get(f)) for f in INCIDENT_FIELDS))
        conn.executemany(
            f"INSERT INTO incidents ({','.join(INCIDENT_FIELDS)}) "
            f"VALUES ({','.join('?' * len(INCIDENT_FIELDS))})", rows)


# ============================================================================ read


def read_table(name: str, db_path: Path | str = config.DB_PATH) -> pd.DataFrame:
    if name not in {"incidents", "assets", "sla_targets", "changes"}:
        raise ValueError(f"Unknown table {name}")
    with connect(db_path) as conn:
        df = pd.read_sql_query(f"SELECT * FROM {name}", conn)
    for col in ("opened_at", "resolved_at", "implemented_at"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format=config.DATETIME_FORMAT, errors="coerce")
    if "reassignment_count" in df.columns:
        df["reassignment_count"] = df["reassignment_count"].astype("Int64")
    return df


def load_all(db_path: Path | str = config.DB_PATH) -> dict[str, pd.DataFrame]:
    return {t: read_table(t, db_path) for t in ("incidents", "assets", "sla_targets", "changes")}


def get_incident(ticket_id: str, db_path: Path | str = config.DB_PATH) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM incidents WHERE ticket_id = ?", (ticket_id,)).fetchone()
    return dict(row) if row else None


def list_assets(db_path: Path | str = config.DB_PATH) -> list[dict]:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT asset_id, asset_name, service FROM assets ORDER BY asset_id")]


def get_change(change_id: str, db_path: Path | str = config.DB_PATH) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM changes WHERE change_id = ?", (change_id,)).fetchone()
    return dict(row) if row else None


# ============================================================================ validation


def validate_incident(form: dict, db_path: Path | str = config.DB_PATH) -> tuple[dict, list[str]]:
    """Validate and standardize user input.

    Returns ``(clean_record, notes)``. ``notes`` tells the user what was
    standardized (e.g. "Category 'E-mail' standardized to 'Email'").
    Raises :class:`ValidationError` listing every problem found.
    """
    errors, notes, flags = [], [], []

    opened = parse_datetime(form.get("opened_at"))
    if opened is None:
        errors.append("Opened at is required and must be a real date (YYYY-MM-DD HH:MM).")
    resolved = parse_datetime(form.get("resolved_at"))
    if (form.get("resolved_at") or "").strip() and resolved is None:
        errors.append("Resolved at is not a valid date.")
    if opened is not None and resolved is not None and resolved < opened:
        errors.append("Resolved at cannot be earlier than Opened at.")

    status = standardize_status(form.get("status"))
    if status is None:
        errors.append(f"Status must be one of: {', '.join(config.VALID_STATUSES)}.")
    elif status in config.CLOSED_STATUSES and resolved is None:
        notes.append("Ticket is resolved but has no resolution time - SLA will show 'Unknown'.")
        flags.append("resolution_missing")
    elif status not in config.CLOSED_STATUSES and resolved is not None:
        errors.append("An open ticket cannot have a resolution time. "
                      "Set status to Resolved/Closed or clear Resolved at.")

    priority = standardize_priority(form.get("priority"))
    if priority is None:
        errors.append("Priority must be P1, P2, P3 or P4.")

    description = (form.get("short_description") or "").strip()
    if len(description) < 5:
        errors.append("Short description must be at least 5 characters.")

    raw_category = (form.get("category") or "").strip()
    category, method = standardize_category(raw_category, description)
    if method == "synonym":
        notes.append(f"Category '{raw_category}' standardized to '{category}'.")
    elif method == "inferred":
        notes.append(f"Category inferred as '{category}' from the description.")
        flags.append("category_inferred")
    elif method == "unmatched":
        notes.append(f"Category '{raw_category}' not recognised - stored as '{category}'.")
        flags.append("uncategorized")

    asset_id = standardize_asset_id(form.get("asset_id"))
    if asset_id:
        with connect(db_path) as conn:
            if not conn.execute("SELECT 1 FROM assets WHERE asset_id=?", (asset_id,)).fetchone():
                errors.append(f"Asset '{asset_id}' is not in the asset inventory.")

    count_raw = (str(form.get("reassignment_count") or "")).strip()
    reassignment = None
    if count_raw:
        if not count_raw.isdigit():
            errors.append("Reassignment count must be a whole number of 0 or more.")
        else:
            reassignment = int(count_raw)

    if errors:
        raise ValidationError(errors)

    record = {
        "opened_at": _fmt_ts(opened),
        "resolved_at": _fmt_ts(resolved),
        "status": status,
        "priority": priority,
        "category": category,
        "category_raw": raw_category or None,
        "category_method": method,
        "short_description": description,
        "asset_id": asset_id,
        "assignment_group": (form.get("assignment_group") or "").strip() or None,
        "assigned_to": (form.get("assigned_to") or "").strip() or None,
        "reassignment_count": reassignment,
        "data_quality_flags": ";".join(flags),
        "updated_at": datetime.now().strftime(config.DATETIME_FORMAT),
    }
    return record, notes


# ============================================================================ write


def next_ticket_id(db_path: Path | str = config.DB_PATH) -> str:
    with connect(db_path) as conn:
        ids = [r[0] for r in conn.execute("SELECT ticket_id FROM incidents")]
    numbers = [int(i[3:]) for i in ids if i.startswith("INC") and i[3:].isdigit()]
    return f"INC{(max(numbers, default=1000) + 1):05d}"


def create_incident(form: dict, db_path: Path | str = config.DB_PATH) -> tuple[str, list[str]]:
    record, notes = validate_incident(form, db_path)
    record["ticket_id"] = next_ticket_id(db_path)
    with connect(db_path) as conn:
        conn.execute(f"INSERT INTO incidents ({','.join(record)}) "
                     f"VALUES ({','.join('?' * len(record))})", list(record.values()))
    return record["ticket_id"], notes


def update_incident(ticket_id: str, form: dict,
                    db_path: Path | str = config.DB_PATH) -> list[str]:
    if get_incident(ticket_id, db_path) is None:
        raise KeyError(ticket_id)
    record, notes = validate_incident(form, db_path)
    assignments = ", ".join(f"{k} = ?" for k in record)
    with connect(db_path) as conn:
        conn.execute(f"UPDATE incidents SET {assignments} WHERE ticket_id = ?",
                     [*record.values(), ticket_id])
    return notes


def delete_incident(ticket_id: str, db_path: Path | str = config.DB_PATH) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute("DELETE FROM incidents WHERE ticket_id = ?", (ticket_id,))
    return cur.rowcount > 0
