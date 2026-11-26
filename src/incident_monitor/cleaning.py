"""Data-quality checks and standardization.

Every function here is *pure*: it takes a raw DataFrame and returns a cleaned
copy, writing one line into a :class:`QualityLog` for every problem found and
the action taken. That log is saved next to the cleaned data, so every change
made to the source data is traceable.

Checks performed
----------------
* missing values      - blank categories, priorities, asset ids, timestamps
* duplicates          - exact duplicate rows, duplicate ids with conflicting data
* invalid entries     - impossible dates, resolution before open, unknown codes,
                        asset ids not in the inventory, negative counts
* standardization     - categories, priorities, statuses, asset ids, criticality
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from . import config

# ============================================================================ helpers


def clean_text(value: Any) -> str | None:
    """Return a stripped string, or ``None`` for blanks / NaN."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def parse_datetime(value: Any) -> pd.Timestamp | None:
    """Parse ``YYYY-MM-DD HH:MM`` (seconds/ISO 'T' also accepted).

    Returns ``None`` for blanks and for impossible dates such as 31 September.
    """
    text = clean_text(value)
    if text is None:
        return None
    ts = pd.to_datetime(text.replace("T", " "), errors="coerce")
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:  # store everything as naive local time
        ts = ts.tz_localize(None)
    return ts.floor("min")


_CATEGORY_LOOKUP = {
    syn.lower(): canonical
    for canonical, synonyms in config.CATEGORY_SYNONYMS.items()
    for syn in [canonical, *synonyms]
}


def standardize_category(raw: Any, description: Any = None) -> tuple[str, str]:
    """Map a free-text category to one of :data:`config.CANONICAL_CATEGORIES`.

    Returns ``(category, method)`` where *method* explains how it was decided:
    ``"exact"``, ``"synonym"``, ``"inferred"`` (from the description) or
    ``"unmatched"`` (falls back to ``Uncategorized``).
    """
    text = clean_text(raw)
    if text:
        if text in config.CANONICAL_CATEGORIES:
            return text, "exact"
        hit = _CATEGORY_LOOKUP.get(" ".join(text.lower().split()))
        if hit:
            return hit, "synonym"
    desc = (clean_text(description) or "").lower()
    for keyword, canonical in config.DESCRIPTION_KEYWORDS:
        if keyword in desc:
            return canonical, "inferred"
    return config.UNCATEGORIZED, "unmatched"


def standardize_priority(raw: Any) -> str | None:
    text = clean_text(raw)
    return config.PRIORITY_SYNONYMS.get(text.lower()) if text else None


def standardize_status(raw: Any) -> str | None:
    text = clean_text(raw)
    return config.STATUS_SYNONYMS.get(" ".join(text.lower().split())) if text else None


def standardize_asset_id(raw: Any) -> str | None:
    text = clean_text(raw)
    return text.upper().replace(" ", "") if text else None


# ============================================================================ quality log


@dataclass
class QualityLog:
    """Collects one row per data-quality finding."""

    rows: list[dict] = field(default_factory=list)

    def add(self, dataset: str, record_id: Any, check: str, issue: str, action: str) -> None:
        self.rows.append({"dataset": dataset, "record_id": record_id, "check": check,
                          "issue": issue, "action": action})

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=["dataset", "record_id", "check", "issue", "action"])

    def summary(self) -> pd.DataFrame:
        df = self.to_frame()
        if df.empty:
            return pd.DataFrame(columns=["dataset", "check", "count"])
        return (df.groupby(["dataset", "check"]).size().reset_index(name="count")
                  .sort_values(["dataset", "count"], ascending=[True, False]))


# ============================================================================ loaders


def load_raw_csv(path: Path) -> pd.DataFrame:
    """Read every column as text so cleaning decides the types, not pandas."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_mock_api(path: Path) -> pd.DataFrame:
    """Read the saved API response and return its ``result`` list as a table."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return pd.DataFrame(payload.get("result", [])).astype(str)


def _drop_exact_duplicates(df: pd.DataFrame, dataset: str, id_col: str,
                           log: QualityLog) -> pd.DataFrame:
    dupes = df[df.duplicated(keep="first")]
    for rid in dupes[id_col]:
        log.add(dataset, rid, "duplicate", "Exact duplicate row", "Removed duplicate copy")
    return df.drop_duplicates(keep="first").reset_index(drop=True)


# ============================================================================ assets


def clean_assets(raw: pd.DataFrame, log: QualityLog) -> pd.DataFrame:
    df = raw.map(clean_text)
    df["asset_id"] = df["asset_id"].map(standardize_asset_id)

    def _crit(row):
        value = config.CRITICALITY_VALUES.get((row["criticality"] or "").lower())
        if value is None:
            log.add("assets", row["asset_id"], "invalid value",
                    f"Unknown criticality '{row['criticality']}'", "Set to 'Medium'")
            return "Medium"
        if value != row["criticality"]:
            log.add("assets", row["asset_id"], "standardization",
                    f"Criticality '{row['criticality']}'", f"Standardized to '{value}'")
        return value

    df["criticality"] = df.apply(_crit, axis=1)

    for col in ["asset_name", "asset_type", "service", "location", "owner_team"]:
        for rid in df.loc[df[col].isna(), "asset_id"]:
            log.add("assets", rid, "missing value", f"Missing {col}", "Filled with 'Unknown'")
        df[col] = df[col].fillna("Unknown")

    df = _drop_exact_duplicates(df, "assets", "asset_id", log)
    repeated = df[df.duplicated("asset_id", keep="first")]
    for rid in repeated["asset_id"]:
        log.add("assets", rid, "duplicate", "Asset id appears twice with different data",
                "Kept first record")
    return df.drop_duplicates("asset_id", keep="first").reset_index(drop=True)


# ============================================================================ SLA targets


def clean_sla_targets(raw: pd.DataFrame, log: QualityLog) -> pd.DataFrame:
    df = raw.map(clean_text)
    df = _drop_exact_duplicates(df, "sla_targets", "priority", log)
    df["priority"] = df["priority"].map(standardize_priority)
    df["resolution_target_hours"] = pd.to_numeric(df["resolution_target_hours"], errors="coerce")
    bad = df["priority"].isna() | ~(df["resolution_target_hours"] > 0)
    for _, row in df[bad].iterrows():
        log.add("sla_targets", row["priority"], "invalid value",
                "Unknown priority or non-positive target", "Row removed")
    df = df[~bad].drop_duplicates("priority", keep="first")
    return df.sort_values("priority").reset_index(drop=True)


# ============================================================================ changes (mock API)


def clean_changes(raw: pd.DataFrame, valid_assets: set[str], log: QualityLog) -> pd.DataFrame:
    df = raw.map(clean_text)
    df = _drop_exact_duplicates(df, "changes", "change_id", log)
    df = df.drop_duplicates("change_id", keep="first")
    df["asset_id"] = df["asset_id"].map(standardize_asset_id)
    df["implemented_at"] = df["implemented_at"].map(parse_datetime)
    for _, row in df.iterrows():
        if row["implemented_at"] is None or pd.isna(row["implemented_at"]):
            log.add("changes", row["change_id"], "missing value", "Missing implemented_at",
                    "Kept, but cannot be linked to incidents")
        if row["asset_id"] not in valid_assets:
            log.add("changes", row["change_id"], "invalid value",
                    f"Asset '{row['asset_id']}' not in inventory", "Kept, flagged")
    df["implemented_at"] = pd.to_datetime(df["implemented_at"])
    return df.reset_index(drop=True)


# ============================================================================ incidents


def _completeness(row: pd.Series) -> int:
    return int(row.notna().sum())


def clean_incidents(raw: pd.DataFrame, valid_assets: set[str], log: QualityLog) -> pd.DataFrame:
    """Clean the incident ticket export. See module docstring for the checks."""
    df = raw.map(clean_text)
    ds = "incidents"

    # 1. exact duplicates -------------------------------------------------------
    df = _drop_exact_duplicates(df, ds, "ticket_id", log)

    # 2. timestamps ---------------------------------------------------------------
    df["opened_raw"] = df["opened_at"]
    df["opened_at"] = df["opened_at"].map(parse_datetime)
    df["resolved_raw"] = df["resolved_at"]
    df["resolved_at"] = df["resolved_at"].map(parse_datetime)
    bad_open = df["opened_at"].isna()
    for _, row in df[bad_open].iterrows():
        log.add(ds, row["ticket_id"], "invalid value",
                f"Unparseable opened_at '{row['opened_raw']}'",
                "Row removed (cannot measure SLA without an open time)")
    df = df[~bad_open].copy()

    bad_resolved = df["resolved_raw"].notna() & df["resolved_at"].isna()
    for rid, raw_val in df.loc[bad_resolved, ["ticket_id", "resolved_raw"]].itertuples(index=False):
        log.add(ds, rid, "invalid value", f"Unparseable resolved_at '{raw_val}'", "Set to blank")

    # 3. duplicate ticket ids: keep the most complete record --------------------
    df["_complete"] = df.apply(_completeness, axis=1)
    df = df.sort_values(["ticket_id", "_complete"], ascending=[True, False])
    for rid in df.loc[df.duplicated("ticket_id", keep="first"), "ticket_id"]:
        log.add(ds, rid, "duplicate", "Ticket id appears more than once with different values",
                "Kept the most complete record")
    df = df.drop_duplicates("ticket_id", keep="first")

    flags: dict[str, list[str]] = {rid: [] for rid in df["ticket_id"]}

    # 4. status -----------------------------------------------------------------
    def _status(row):
        value = standardize_status(row["status"])
        if value is None:
            fallback = "Resolved" if pd.notna(row["resolved_at"]) else "Open"
            log.add(ds, row["ticket_id"], "invalid value", f"Unknown status '{row['status']}'",
                    f"Set to '{fallback}' from resolution time")
            flags[row["ticket_id"]].append("status_inferred")
            return fallback
        if value != row["status"]:
            log.add(ds, row["ticket_id"], "standardization", f"Status '{row['status']}'",
                    f"Standardized to '{value}'")
        return value

    df["status"] = df.apply(_status, axis=1)

    # 5. priority -----------------------------------------------------------------
    def _priority(row):
        value = standardize_priority(row["priority"])
        if value is None:
            log.add(ds, row["ticket_id"], "missing value",
                    f"Missing/unknown priority '{clean_text(row['priority']) or ''}'",
                    f"Defaulted to {config.DEFAULT_PRIORITY}")
            flags[row["ticket_id"]].append("priority_defaulted")
            return config.DEFAULT_PRIORITY
        if value != row["priority"]:
            log.add(ds, row["ticket_id"], "standardization", f"Priority '{row['priority']}'",
                    f"Standardized to '{value}'")
        return value

    df["priority"] = df.apply(_priority, axis=1)

    # 6. category -----------------------------------------------------------------
    df["category_raw"] = df["category"]
    results = df.apply(lambda r: standardize_category(r["category"], r["short_description"]),
                       axis=1)
    df["category"] = [c for c, _ in results]
    df["category_method"] = [m for _, m in results]
    for _, row in df.iterrows():
        if row["category_method"] == "synonym":
            log.add(ds, row["ticket_id"], "standardization",
                    f"Category '{row['category_raw']}'", f"Mapped to '{row['category']}'")
        elif row["category_method"] == "inferred":
            log.add(ds, row["ticket_id"], "missing value",
                    f"Blank/unknown category '{clean_text(row['category_raw']) or ''}'",
                    f"Inferred '{row['category']}' from description")
            flags[row["ticket_id"]].append("category_inferred")
        elif row["category_method"] == "unmatched":
            log.add(ds, row["ticket_id"], "missing value",
                    f"Category '{clean_text(row['category_raw']) or ''}' could not be mapped",
                    f"Set to '{config.UNCATEGORIZED}'")
            flags[row["ticket_id"]].append("uncategorized")

    # 7. asset id ---------------------------------------------------------------
    df["asset_raw"] = df["asset_id"]
    df["asset_id"] = df["asset_id"].map(standardize_asset_id)
    for _, row in df.iterrows():
        rid = row["ticket_id"]
        if pd.isna(row["asset_id"]):
            log.add(ds, rid, "missing value", "Missing asset_id", "Kept; service shown as 'Unknown'")
            flags[rid].append("asset_missing")
        elif row["asset_id"] not in valid_assets:
            log.add(ds, rid, "invalid value", f"Asset '{row['asset_id']}' not in inventory",
                    "Kept; service shown as 'Unknown'")
            flags[rid].append("asset_unknown")
        elif row["asset_id"] != row["asset_raw"]:
            log.add(ds, rid, "standardization", f"Asset id '{row['asset_raw']}'",
                    f"Normalized to '{row['asset_id']}'")

    # 8. resolution timestamp logic -------------------------------------------
    for idx, row in df.iterrows():
        rid = row["ticket_id"]
        closed = row["status"] in config.CLOSED_STATUSES
        if pd.notna(row["resolved_at"]) and row["resolved_at"] < row["opened_at"]:
            log.add(ds, rid, "invalid value", "resolved_at is earlier than opened_at",
                    "Resolution time cleared; SLA marked 'Unknown'")
            df.at[idx, "resolved_at"] = None
            flags[rid].append("resolution_before_open")
        elif closed and pd.isna(row["resolved_at"]):
            log.add(ds, rid, "missing value", f"Status '{row['status']}' but no resolved_at",
                    "Kept; SLA marked 'Unknown'")
            flags[rid].append("resolution_missing")
        elif not closed and pd.notna(row["resolved_at"]):
            log.add(ds, rid, "invalid value", f"Status '{row['status']}' but has resolved_at",
                    "Status set to 'Resolved'")
            df.at[idx, "status"] = "Resolved"

    # 9. reassignment count -----------------------------------------------------
    counts = pd.to_numeric(df["reassignment_count"], errors="coerce")
    for rid, raw_val, num in zip(df["ticket_id"], df["reassignment_count"], counts):
        if pd.isna(num) or num < 0:
            log.add(ds, rid, "invalid value", f"reassignment_count '{raw_val}'", "Set to blank")
    df["reassignment_count"] = counts.where(counts >= 0).astype("Int64")

    # 10. remaining blanks in descriptive columns -----------------------------
    for col in ["short_description", "assignment_group", "assigned_to"]:
        for rid in df.loc[df[col].isna(), "ticket_id"]:
            log.add(ds, rid, "missing value", f"Missing {col}", "Left blank")

    df["data_quality_flags"] = df["ticket_id"].map(lambda r: ";".join(flags[r]))
    df["opened_at"] = pd.to_datetime(df["opened_at"])
    df["resolved_at"] = pd.to_datetime(df["resolved_at"])

    columns = ["ticket_id", "opened_at", "resolved_at", "status", "priority", "category",
               "category_raw", "category_method", "short_description", "asset_id",
               "assignment_group", "assigned_to", "reassignment_count", "data_quality_flags"]
    return df[columns].sort_values("opened_at").reset_index(drop=True)


# ============================================================================ all together


@dataclass
class CleanedData:
    incidents: pd.DataFrame
    assets: pd.DataFrame
    sla_targets: pd.DataFrame
    changes: pd.DataFrame
    log: QualityLog
    raw_counts: dict[str, int]


def clean_all(incidents_path: Path = config.RAW_INCIDENTS,
              assets_path: Path = config.RAW_ASSETS,
              sla_path: Path = config.RAW_SLA,
              changes_path: Path = config.RAW_CHANGES_API) -> CleanedData:
    log = QualityLog()
    raw_inc = load_raw_csv(incidents_path)
    raw_assets = load_raw_csv(assets_path)
    raw_sla = load_raw_csv(sla_path)
    raw_changes = load_mock_api(changes_path)

    assets = clean_assets(raw_assets, log)
    valid_assets = set(assets["asset_id"])
    return CleanedData(
        incidents=clean_incidents(raw_inc, valid_assets, log),
        assets=assets,
        sla_targets=clean_sla_targets(raw_sla, log),
        changes=clean_changes(raw_changes, valid_assets, log),
        log=log,
        raw_counts={"incidents": len(raw_inc), "assets": len(raw_assets),
                    "sla_targets": len(raw_sla), "changes": len(raw_changes)},
    )
