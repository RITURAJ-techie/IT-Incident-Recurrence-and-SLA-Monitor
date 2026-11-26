"""Central place for paths and business rules.

Everything a reviewer might want to tweak (file locations, recurrence window,
category dictionary) lives here so the rest of the code stays free of
"magic numbers".
"""
from __future__ import annotations

import os
from pathlib import Path

# ----------------------------------------------------------------------------- paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
CHART_DIR = OUTPUT_DIR / "charts"

RAW_INCIDENTS = RAW_DIR / "incidents.csv"
RAW_ASSETS = RAW_DIR / "assets.csv"
RAW_SLA = RAW_DIR / "sla_targets.csv"
RAW_CHANGES_API = RAW_DIR / "mock_api" / "change_records_response.json"

DB_PATH = Path(os.environ.get("INCIDENT_DB_PATH", DATA_DIR / "incident_monitor.db"))

# ----------------------------------------------------------------------------- rules
DATETIME_FORMAT = "%Y-%m-%d %H:%M"

#: Two incidents on the same asset with the same (standardized) category that
#: open within this many days of each other count as a recurrence.
RECURRENCE_WINDOW_DAYS = 7

#: An incident opened within this many hours after a change was implemented on
#: the same asset is flagged as "possibly change-related".
CHANGE_LINK_WINDOW_HOURS = 72

VALID_PRIORITIES = ["P1", "P2", "P3", "P4"]
DEFAULT_PRIORITY = "P3"
VALID_STATUSES = ["Open", "In Progress", "Resolved", "Closed"]
CLOSED_STATUSES = {"Resolved", "Closed"}

#: Canonical categories and every spelling we have seen for them.
#: Keys are compared lower-cased with surrounding whitespace removed.
CATEGORY_SYNONYMS: dict[str, list[str]] = {
    "Network": ["network", "networking", "nw", "lan/wan", "lan", "wan", "connectivity", "wifi"],
    "Remote Access": ["remote access", "vpn", "vpn issue"],
    "Email": ["email", "e-mail", "mail", "outlook", "exchange"],
    "Hardware": ["hardware", "hw", "laptop hardware", "laptop", "desktop"],
    "Application": ["application", "software", "app", "sw", "erp", "hr portal"],
    "Access": ["access", "password reset", "login / access", "login", "account access",
               "iam", "identity"],
    "Database": ["database", "db", "sql"],
    "Printing": ["printing", "printer", "print"],
}
CANONICAL_CATEGORIES = list(CATEGORY_SYNONYMS)
UNCATEGORIZED = "Uncategorized"

#: When the category is blank/unknown, look for these words in the description.
#: Order matters - the first match wins (e.g. "VPN" before generic network words).
DESCRIPTION_KEYWORDS: list[tuple[str, str]] = [
    ("vpn", "Remote Access"),
    ("outlook", "Email"), ("mailbox", "Email"), ("email", "Email"), ("mail", "Email"),
    ("printer", "Printing"), ("print", "Printing"),
    ("database", "Database"), ("sql", "Database"),
    ("erp", "Application"), ("portal", "Application"),
    ("password", "Access"), ("locked out", "Access"), ("log in", "Access"),
    ("login", "Access"), ("mfa", "Access"),
    ("laptop", "Hardware"), ("docking", "Hardware"), ("battery", "Hardware"),
    ("network", "Network"), ("wifi", "Network"), ("packet", "Network"),
    ("connectivity", "Network"),
]

PRIORITY_SYNONYMS = {
    "p1": "P1", "1": "P1", "critical": "P1",
    "p2": "P2", "2": "P2", "high": "P2",
    "p3": "P3", "3": "P3", "medium": "P3", "moderate": "P3",
    "p4": "P4", "4": "P4", "low": "P4",
}

STATUS_SYNONYMS = {
    "open": "Open", "new": "Open",
    "in progress": "In Progress", "in-progress": "In Progress", "wip": "In Progress",
    "resolved": "Resolved", "closed": "Closed",
}

CRITICALITY_VALUES = {"high": "High", "medium": "Medium", "low": "Low"}
