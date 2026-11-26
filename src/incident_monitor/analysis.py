"""SLA compliance, recurrence detection and asset/service summaries.

The functions take the *cleaned* tables (straight from the database) and
derive everything else, so after any add/update/delete in the app the numbers
are recalculated from scratch - there is no stale derived data.

Definitions
-----------
SLA status (per ticket)
    Met / Breached        resolved, resolution time <= / > the priority target
    Unknown               resolved/closed but the resolution time is missing or invalid
    Open - within SLA     still open, elapsed time <= target
    Open - breached       still open, elapsed time  > target
SLA compliance %        Met / (Met + Breached)   (tickets we can actually measure)
Recurring ticket        an earlier ticket exists on the *same asset* with the *same
                        standardized category* opened within RECURRENCE_WINDOW_DAYS
Recurrence rate %       recurring tickets / all tickets
Change-related ticket   opened within CHANGE_LINK_WINDOW_HOURS after an implemented
                        change on the same asset
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from . import config

SLA_MET = "Met"
SLA_BREACHED = "Breached"
SLA_UNKNOWN = "Unknown"
SLA_OPEN_OK = "Open - within SLA"
SLA_OPEN_BREACHED = "Open - breached"
SLA_STATUSES = [SLA_MET, SLA_BREACHED, SLA_UNKNOWN, SLA_OPEN_OK, SLA_OPEN_BREACHED]


def _pct(numerator: float, denominator: float) -> float | None:
    return round(100.0 * float(numerator) / float(denominator), 1) if denominator else None


# ============================================================================ enrichment


def enrich_incidents(incidents: pd.DataFrame, assets: pd.DataFrame, sla: pd.DataFrame,
                     changes: pd.DataFrame, as_of: datetime | None = None) -> pd.DataFrame:
    """Join the four sources and add SLA, recurrence and change-link columns."""
    as_of = pd.Timestamp(as_of or datetime.now())
    df = incidents.copy()
    df["opened_at"] = pd.to_datetime(df["opened_at"])
    df["resolved_at"] = pd.to_datetime(df["resolved_at"])

    # --- join asset inventory and SLA targets --------------------------------
    df = df.merge(assets[["asset_id", "asset_name", "asset_type", "service", "criticality"]],
                  on="asset_id", how="left")
    for col in ["asset_name", "asset_type", "service", "criticality"]:
        df[col] = df[col].fillna("Unknown")
    df = df.merge(sla[["priority", "resolution_target_hours"]], on="priority", how="left")

    # --- SLA ------------------------------------------------------------------
    df["resolution_hours"] = ((df["resolved_at"] - df["opened_at"]).dt.total_seconds()
                              / 3600).round(2)
    elapsed_open = (as_of - df["opened_at"]).dt.total_seconds() / 3600
    closed = df["status"].isin(config.CLOSED_STATUSES)

    def _status(i):
        target = df.at[i, "resolution_target_hours"]
        if closed[i]:
            hours = df.at[i, "resolution_hours"]
            if pd.isna(hours) or pd.isna(target):
                return SLA_UNKNOWN
            return SLA_MET if hours <= target else SLA_BREACHED
        if pd.isna(target):
            return SLA_UNKNOWN
        return SLA_OPEN_BREACHED if elapsed_open[i] > target else SLA_OPEN_OK

    df["sla_status"] = [_status(i) for i in df.index]

    # --- recurrence -------------------------------------------------------------
    df = df.sort_values("opened_at").reset_index(drop=True)
    df["recurrence_key"] = None
    df["previous_ticket_id"] = None
    df["days_since_previous"] = pd.NA
    has_asset = df["asset_id"].notna() & (df["asset_name"] != "Unknown")
    df.loc[has_asset, "recurrence_key"] = df.loc[has_asset, "asset_id"] + " | " + \
        df.loc[has_asset, "category"]
    for _, group in df[has_asset].groupby("recurrence_key"):
        prev_id, prev_time = None, None
        for idx, row in group.iterrows():
            if prev_id is not None:
                df.at[idx, "previous_ticket_id"] = prev_id
                df.at[idx, "days_since_previous"] = round(
                    (row["opened_at"] - prev_time).total_seconds() / 86400, 1)
            prev_id, prev_time = row["ticket_id"], row["opened_at"]
    days = pd.to_numeric(df["days_since_previous"], errors="coerce")
    df["days_since_previous"] = days
    df["is_recurring"] = days.le(config.RECURRENCE_WINDOW_DAYS).fillna(False).astype(bool)
    group_sizes = df.groupby("recurrence_key")["ticket_id"].transform("count")
    df["recurrence_group_size"] = group_sizes.fillna(1).astype(int)

    # --- link to change records -------------------------------------------------
    df["linked_change_id"] = None
    df["hours_after_change"] = pd.NA
    usable = changes.copy()
    usable["implemented_at"] = pd.to_datetime(usable["implemented_at"])
    usable = usable[usable["implemented_at"].notna()
                    & usable["state"].fillna("").str.lower().eq("implemented")]
    window = pd.Timedelta(hours=config.CHANGE_LINK_WINDOW_HOURS)
    for idx, row in df[df["asset_id"].notna()].iterrows():
        cands = usable[(usable["asset_id"] == row["asset_id"])
                       & (usable["implemented_at"] <= row["opened_at"])
                       & (row["opened_at"] - usable["implemented_at"] <= window)]
        if not cands.empty:
            latest = cands.sort_values("implemented_at").iloc[-1]
            df.at[idx, "linked_change_id"] = latest["change_id"]
            df.at[idx, "hours_after_change"] = round(
                (row["opened_at"] - latest["implemented_at"]).total_seconds() / 3600, 1)
    df["hours_after_change"] = pd.to_numeric(df["hours_after_change"], errors="coerce")
    return df


# ============================================================================ summaries


def kpis(df: pd.DataFrame) -> dict:
    """Headline numbers shown in the dashboard tiles and the report."""
    counts = df["sla_status"].value_counts()
    met, breached = int(counts.get(SLA_MET, 0)), int(counts.get(SLA_BREACHED, 0))
    resolved = df[df["sla_status"].isin([SLA_MET, SLA_BREACHED])]
    return {
        "total_tickets": int(len(df)),
        "open_tickets": int((~df["status"].isin(config.CLOSED_STATUSES)).sum()),
        "sla_met": met,
        "sla_breached": breached,
        "sla_unknown": int(counts.get(SLA_UNKNOWN, 0)),
        "open_breached": int(counts.get(SLA_OPEN_BREACHED, 0)),
        "sla_compliance_pct": _pct(met, met + breached),
        "recurring_tickets": int(df["is_recurring"].sum()),
        "recurrence_rate_pct": _pct(df["is_recurring"].sum(), len(df)),
        "recurring_groups": int(df.loc[df["is_recurring"], "recurrence_key"].nunique()),
        "change_related_tickets": int(df["linked_change_id"].notna().sum()),
        "avg_resolution_hours": (round(float(resolved["resolution_hours"].mean()), 1)
                                 if not resolved.empty else None),
        "raw_category_labels": int(df["category_raw"].fillna("(blank)").nunique()),
        "standard_categories": int(df["category"].nunique()),
    }


def sla_summary(df: pd.DataFrame, by: str) -> pd.DataFrame:
    """SLA performance grouped by ``by`` (e.g. category, priority, service)."""
    rows = []
    for key, g in df.groupby(by, dropna=False):
        met = int((g["sla_status"] == SLA_MET).sum())
        breached = int((g["sla_status"] == SLA_BREACHED).sum())
        measured = g[g["sla_status"].isin([SLA_MET, SLA_BREACHED])]
        rows.append({
            by: key if pd.notna(key) else "Unknown",
            "tickets": len(g),
            "met": met,
            "breached": breached,
            "unknown": int((g["sla_status"] == SLA_UNKNOWN).sum()),
            "open": int(g["sla_status"].isin([SLA_OPEN_OK, SLA_OPEN_BREACHED]).sum()),
            "compliance_pct": _pct(met, met + breached),
            "avg_resolution_hours": (round(float(measured["resolution_hours"].mean()), 1)
                                     if not measured.empty else None),
            "recurring": int(g["is_recurring"].sum()),
        })
    out = pd.DataFrame(rows)
    return out.sort_values("tickets", ascending=False).reset_index(drop=True) if not out.empty else out


def recurrence_hotspots(df: pd.DataFrame, min_tickets: int = 2) -> pd.DataFrame:
    """Asset + category combinations with repeated incidents (problem candidates)."""
    rows = []
    for key, g in df[df["recurrence_key"].notna()].groupby("recurrence_key"):
        if len(g) < min_tickets or not g["is_recurring"].any():
            continue
        g = g.sort_values("opened_at")
        rows.append({
            "recurrence_key": key,
            "asset_id": g["asset_id"].iloc[0],
            "asset_name": g["asset_name"].iloc[0],
            "service": g["service"].iloc[0],
            "category": g["category"].iloc[0],
            "tickets": len(g),
            "recurring": int(g["is_recurring"].sum()),
            "breached": int(g["sla_status"].isin([SLA_BREACHED, SLA_OPEN_BREACHED]).sum()),
            "first_seen": g["opened_at"].min(),
            "last_seen": g["opened_at"].max(),
            "linked_changes": ", ".join(sorted(g["linked_change_id"].dropna().unique())),
            "ticket_ids": ", ".join(g["ticket_id"]),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["recurring", "tickets"], ascending=False).reset_index(drop=True)


def asset_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """Per-asset comparison: volume, recurrence, SLA and reassignment."""
    known = df[df["asset_name"] != "Unknown"]
    rows = []
    for (asset_id, name, a_type, service), g in known.groupby(
            ["asset_id", "asset_name", "asset_type", "service"]):
        met = int((g["sla_status"] == SLA_MET).sum())
        breached = int((g["sla_status"] == SLA_BREACHED).sum())
        rows.append({
            "asset_id": asset_id, "asset_name": name, "asset_type": a_type, "service": service,
            "tickets": len(g), "recurring": int(g["is_recurring"].sum()),
            "compliance_pct": _pct(met, met + breached),
            "avg_reassignments": round(float(g["reassignment_count"].astype("Float64").mean()), 2)
            if g["reassignment_count"].notna().any() else None,
            "change_related": int(g["linked_change_id"].notna().sum()),
        })
    out = pd.DataFrame(rows)
    return out.sort_values("tickets", ascending=False).reset_index(drop=True) if not out.empty else out


def category_mapping(df: pd.DataFrame) -> pd.DataFrame:
    """Which raw labels were folded into which standard category."""
    tmp = df.assign(category_raw=df["category_raw"].fillna("(blank)"))
    out = (tmp.groupby(["category", "category_raw", "category_method"]).size()
              .reset_index(name="tickets"))
    return out.sort_values(["category", "tickets"], ascending=[True, False]).reset_index(drop=True)


def change_related(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["ticket_id", "opened_at", "asset_id", "asset_name", "category",
            "linked_change_id", "hours_after_change", "sla_status"]
    return df.loc[df["linked_change_id"].notna(), cols].reset_index(drop=True)
