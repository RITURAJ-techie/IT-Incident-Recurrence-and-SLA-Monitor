import pandas as pd
import pytest

from incident_monitor import config
from incident_monitor.cleaning import (parse_datetime, standardize_category,
                                       standardize_priority, standardize_status)


@pytest.mark.parametrize("raw, expected", [
    ("Network", "Network"), ("NW", "Network"), ("  networking ", "Network"),
    ("E-mail", "Email"), ("Outlook", "Email"), ("vpn issue", "Remote Access"),
    ("HW", "Hardware"), ("SW", "Application"), ("Password Reset", "Access"),
    ("DB", "Database"), ("print", "Printing"),
])
def test_category_synonyms(raw, expected):
    assert standardize_category(raw)[0] == expected


def test_category_inferred_from_description():
    assert standardize_category("", "VPN tunnel drops") == ("Remote Access", "inferred")
    assert standardize_category("Misc", "Printer offline") == ("Printing", "inferred")
    assert standardize_category("Other", "nothing useful") == (config.UNCATEGORIZED, "unmatched")


def test_priority_and_status():
    assert standardize_priority("critical") == "P1"
    assert standardize_priority("3") == "P3"
    assert standardize_priority("") is None
    assert standardize_status("in progress") == "In Progress"
    assert standardize_status("resolved") == "Resolved"


def test_parse_datetime_rejects_impossible_dates():
    assert parse_datetime("2026-09-31 10:00") is None
    assert parse_datetime("") is None
    assert parse_datetime("2026-09-30 10:00") == pd.Timestamp("2026-09-30 10:00")


def test_cleaned_incidents_are_consistent(cleaned):
    inc = cleaned.incidents
    assert inc["ticket_id"].is_unique
    assert set(inc["category"]) <= set(config.CANONICAL_CATEGORIES) | {config.UNCATEGORIZED}
    assert set(inc["priority"]) <= set(config.VALID_PRIORITIES)
    assert set(inc["status"]) <= set(config.VALID_STATUSES)
    resolved = inc.dropna(subset=["resolved_at"])
    assert (resolved["resolved_at"] >= resolved["opened_at"]).all()
    assert (inc["reassignment_count"].dropna() >= 0).all()
    assert len(inc) < cleaned.raw_counts["incidents"]  # duplicates/invalid rows removed


def test_reference_tables_deduplicated(cleaned):
    assert cleaned.assets["asset_id"].is_unique
    assert cleaned.sla_targets["priority"].tolist() == ["P1", "P2", "P3", "P4"]
    assert cleaned.changes["change_id"].is_unique


def test_quality_log_records_every_check_type(cleaned):
    checks = set(cleaned.log.to_frame()["check"])
    assert {"duplicate", "missing value", "invalid value", "standardization"} <= checks
