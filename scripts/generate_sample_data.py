"""Generate the small, self-created raw dataset used by the project.

The files written to ``data/raw/`` are committed to the repository, so you never
*need* to run this script. It exists so the dataset is reproducible and so it is
obvious which data-quality problems were planted on purpose:

* incidents.csv        - incident tickets (with assignment info)
* assets.csv           - IT asset inventory
* sla_targets.csv      - resolution targets per priority
* mock_api/change_records_response.json - a *saved* response from a
  (pretend) online change-management API, so the project runs offline.

Deliberate problems (the cleaning step must catch all of them):
  - the same category written many ways ("E-mail", "Outlook", "NW", ...)
  - blank / unknown categories that can only be inferred from the description
  - resolution timestamps missing on resolved tickets
  - resolution timestamps earlier than the open timestamp
  - an impossible date (31 September)
  - exact duplicate rows and a duplicate ticket id with different content
  - priority written as "P2", "2", "High", "critical", blank ...
  - asset ids in the wrong case / with spaces, and an id not in the inventory
  - negative reassignment counts
  - duplicate asset and SLA rows, a missing asset location

Run:  python scripts/generate_sample_data.py
"""
from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SEED = 42

# --------------------------------------------------------------------------- #
# Assets
# --------------------------------------------------------------------------- #
ASSETS = [
    # asset_id, asset_name, asset_type, service, location, criticality, owner_team
    ("AST-001", "Core Switch A", "Network Switch", "Corporate Network", "HQ-DC1", "High", "Network Ops"),
    ("AST-002", "Core Switch B", "Network Switch", "Corporate Network", "HQ-DC1", "High", "Network Ops"),
    ("AST-003", "VPN Gateway", "Firewall/VPN", "Remote Access", "HQ-DC1", "High", "Network Ops"),
    ("AST-004", "Exchange Server 01", "Server", "Email", "HQ-DC1", "High", "Messaging"),
    ("AST-005", "SQL Cluster Node 1", "Database Server", "ERP", "DC2", "High", "Database Team"),
    ("AST-006", "ERP App Server", "Server", "ERP", "DC2", "Medium", "Applications"),
    ("AST-007", "HR Portal Web Server", "Server", "HR Portal", "DC2", "Medium", "Applications"),
    ("AST-008", "Active Directory DC1", "Server", "Identity & Access", "HQ-DC1", "High", "Identity Team"),
    ("AST-009", "Laptop Pool - Finance", "Laptop", "End User Computing", "Floor 3", "Low", "Service Desk"),
    ("AST-010", "Laptop Pool - Sales", "Laptop", "End User Computing", "Floor 2", "Low", "Service Desk"),
    ("AST-011", "Printer Floor 2", "Printer", "Printing", "Floor 2", "Low", "Service Desk"),
    ("AST-012", "WiFi Controller", "Network Controller", "Corporate Network", "HQ-DC1", "Medium", "Network Ops"),
]

# --------------------------------------------------------------------------- #
# Incident templates: canonical category -> (raw spellings, descriptions, assets, group)
# --------------------------------------------------------------------------- #
TEMPLATES = {
    "Network": (
        ["Network", "network", "NW", "Networking", "LAN/WAN", "Connectivity"],
        ["Users report slow network on floor 2", "Packet loss on core uplink",
         "Intermittent connectivity drops", "WiFi disconnects every few minutes"],
        ["AST-001", "AST-002", "AST-012"], "Network Ops"),
    "Remote Access": (
        ["VPN", "Remote Access", "vpn issue", "Remote access"],
        ["VPN tunnel drops after 10 minutes", "Cannot connect to VPN from home",
         "VPN authentication timeout"],
        ["AST-003"], "Network Ops"),
    "Email": (
        ["Email", "E-mail", "email ", "Outlook", "Mail"],
        ["Outlook not syncing mailbox", "Emails stuck in outbox",
         "Shared mailbox not accessible", "Delayed delivery of external mail"],
        ["AST-004"], "Messaging"),
    "Hardware": (
        ["Hardware", "HW", "Laptop Hardware", "hardware"],
        ["Laptop battery not charging", "Laptop screen flickering",
         "Docking station not detected", "Laptop overheating and shutting down"],
        ["AST-009", "AST-010"], "Service Desk"),
    "Application": (
        ["Software", "Application", "App", "SW"],
        {"AST-006": ["ERP report generation fails", "ERP screen freezes when saving order"],
         "AST-007": ["HR portal page returns error 500", "HR portal login page slow"]},
        ["AST-006", "AST-007"], "Applications"),
    "Access": (
        ["Access", "Password Reset", "Login / Access", "Account Access", "IAM"],
        ["User locked out of account", "Password reset request",
         "New starter cannot log in", "MFA prompt not received"],
        ["AST-008"], "Identity Team"),
    "Database": (
        ["Database", "DB", "SQL"],
        ["ERP database deadlocks during month end", "SQL node high CPU",
         "Database backup job failed"],
        ["AST-005"], "Database Team"),
    "Printing": (
        ["Printer", "Printing", "print"],
        ["Printer paper jam on floor 2", "Printer offline for all users",
         "Print jobs stuck in queue"],
        ["AST-011"], "Service Desk"),
}

# Relative weights -> how often each category appears in the random part.
WEIGHTS = {"Network": 9, "Remote Access": 2, "Email": 4, "Hardware": 9,
           "Application": 8, "Access": 5, "Database": 3, "Printing": 2}

PRIORITY_RAW = {"P1": ["P1", "1", "Critical", "critical"],
                "P2": ["P2", "2", "High", "high"],
                "P3": ["P3", "3", "Medium", "p3"],
                "P4": ["P4", "4", "Low"]}
# typical resolution hours per priority (mean) - some will exceed the SLA
TYPICAL_HOURS = {"P1": 4.5, "P2": 8.0, "P3": 20.0, "P4": 55.0}
SLA_HOURS = {"P1": 4, "P2": 8, "P3": 24, "P4": 72}

START = datetime(2026, 9, 1, 8, 0)
FMT = "%Y-%m-%d %H:%M"


def _fmt(dt: datetime | None) -> str:
    return dt.strftime(FMT) if dt else ""


def build_incidents(rng: random.Random) -> list[dict]:
    rows: list[dict] = []

    def add(category: str, opened: datetime, priority: str, asset: str | None = None,
            desc: str | None = None, hours: float | None = None, status: str | None = None):
        raw_labels, descs, assets, group = TEMPLATES[category]  # descs: list, or {asset: list}
        asset = asset or rng.choice(assets)
        hours = hours if hours is not None else max(
            0.3, rng.gauss(TYPICAL_HOURS[priority], TYPICAL_HOURS[priority] * 0.45))
        status = status or "Resolved"
        resolved = opened + timedelta(hours=hours) if status in ("Resolved", "Closed") else None
        rows.append({
            "opened_at": opened,
            "resolved_at": resolved,
            "status": status,
            "priority": rng.choice(PRIORITY_RAW[priority]),
            "category": rng.choice(raw_labels),
            "short_description": desc or rng.choice(
                descs[asset] if isinstance(descs, dict) else descs),
            "asset_id": asset,
            "assignment_group": group,
            "assigned_to": f"{group.split()[0].lower()}.analyst{rng.randint(1, 3)}",
            "reassignment_count": rng.choices([0, 1, 2, 3], weights=[6, 3, 1, 1])[0],
        })

    # 1) Background noise: random incidents across two months
    cats = list(WEIGHTS)
    for _ in range(44):
        cat = rng.choices(cats, weights=[WEIGHTS[c] for c in cats])[0]
        opened = START + timedelta(days=rng.randint(0, 60), hours=rng.randint(0, 9),
                                   minutes=rng.choice([0, 15, 30, 45]))
        prio = rng.choices(["P1", "P2", "P3", "P4"], weights=[1, 3, 6, 3])[0]
        add(cat, opened, prio)

    # 2) Planted recurrence: VPN gateway keeps failing after a firmware change
    for day, hrs in [(14, 9.5), (15, 3.0), (19, 11.0), (27, 6.5), (40, 10.0)]:
        add("Remote Access", START + timedelta(days=day, hours=1), "P2",
            asset="AST-003", desc="VPN tunnel drops after 10 minutes", hours=hrs)
    # 3) Planted recurrence: Exchange mailbox sync
    for day in (5, 9, 12, 33):
        add("Email", START + timedelta(days=day, hours=2), "P3", asset="AST-004",
            desc="Outlook not syncing mailbox")
    # 4) Planted recurrence: printer floor 2
    for day in (8, 12, 17):
        add("Printing", START + timedelta(days=day, hours=4), "P4", asset="AST-011",
            desc="Printer offline for all users")
    # 5) A few still-open tickets
    add("Application", datetime(2026, 10, 29, 10, 0), "P3", status="In Progress")
    add("Hardware", datetime(2026, 10, 30, 14, 30), "P4", status="Open")
    add("Database", datetime(2026, 10, 30, 9, 0), "P2", status="Open")

    rows.sort(key=lambda r: r["opened_at"])
    for i, r in enumerate(rows, start=1):
        r["ticket_id"] = f"INC{1000 + i:05d}"

    # ---------------- plant data-quality problems ---------------- #
    # status written inconsistently
    for i in (3, 17, 29):
        rows[i]["status"] = rows[i]["status"].lower()
    for i in (6, 21, 40, 52):
        if rows[i]["status"] == "Resolved":
            rows[i]["status"] = "Closed"
    # missing resolution timestamps on resolved tickets
    for i in (4, 11, 26, 37, 49):
        rows[i]["resolved_at"] = None
    # resolution before open (invalid)
    for i in (9, 44):
        rows[i]["resolved_at"] = rows[i]["opened_at"] - timedelta(hours=3)
    # unknown / blank categories (description still tells us what it is)
    rows[15]["category"] = ""
    rows[33]["category"] = "Misc"
    rows[45]["category"] = "Other"
    # priority missing
    rows[20]["priority"] = ""
    # asset id formatting problems and an unknown asset
    rows[7]["asset_id"] = rows[7]["asset_id"].lower() + " "
    rows[30]["asset_id"] = "AST-099"
    rows[50]["asset_id"] = ""
    # negative reassignment count
    rows[13]["reassignment_count"] = -1

    out = [{k: (_fmt(v) if isinstance(v, datetime) or (v is None and k == "resolved_at") else v)
            for k, v in r.items()} for r in rows]
    # impossible calendar date
    out[24]["opened_at"] = "2026-09-31 10:00"
    # exact duplicates (exported twice)
    out.extend([dict(out[2]), dict(out[18]), dict(out[41])])
    # same ticket id, conflicting content (the re-export lost the assignee)
    dup = dict(out[11])
    dup["resolved_at"] = ""
    dup["assigned_to"] = ""
    out.append(dup)
    return out


def write_csv(path: Path, header: list[str], rows: list[dict | tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow([r[h] for h in header] if isinstance(r, dict) else r)


def main() -> None:
    rng = random.Random(SEED)

    incidents = build_incidents(rng)
    write_csv(RAW / "incidents.csv",
              ["ticket_id", "opened_at", "resolved_at", "status", "priority", "category",
               "short_description", "asset_id", "assignment_group", "assigned_to",
               "reassignment_count"], incidents)

    assets = [list(a) for a in ASSETS]
    assets[11][4] = ""          # missing location
    assets[9][5] = "low "        # stray whitespace/case
    assets[3][5] = "HIGH"
    assets.append(list(ASSETS[3]))  # duplicate asset row
    write_csv(RAW / "assets.csv",
              ["asset_id", "asset_name", "asset_type", "service", "location",
               "criticality", "owner_team"], [tuple(a) for a in assets])

    sla = [("P1", 4, "Critical - business stopped"),
           ("P2", 8, "High - major function degraded"),
           ("P3", 24, "Medium - single user / workaround exists"),
           ("P4", 72, "Low - request or cosmetic issue"),
           ("P3", 24, "Medium - single user / workaround exists")]  # duplicate
    write_csv(RAW / "sla_targets.csv",
              ["priority", "resolution_target_hours", "description"], sla)

    changes = [
        ("CHG2001", "AST-003", "Normal", "VPN gateway firmware upgrade 9.2 -> 9.4", "2026-09-15 06:00", "Implemented"),
        ("CHG2002", "AST-004", "Standard", "Exchange cumulative update CU14", "2026-09-05 22:00", "Implemented"),
        ("CHG2003", "AST-001", "Normal", "Core switch A VLAN re-numbering", "2026-09-20 21:00", "Implemented"),
        ("CHG2004", "AST-006", "Normal", "ERP release 4.7 deployment", "2026-10-03 20:00", "Implemented"),
        ("CHG2005", "AST-008", "Standard", "Password policy GPO update", "2026-09-28 19:00", "Implemented"),
        ("CHG2006", "AST-005", "Emergency", "SQL node patch for CVE fix", "2026-10-10 23:00", "Implemented"),
        ("CHG2007", "AST-012", "Standard", "WiFi controller certificate renewal", "", "Implemented"),
        ("CHG2008", "AST-003", "Emergency", "VPN gateway rollback to 9.2", "2026-10-11 05:00", "Implemented"),
        ("CHG2009", "AST-011", "Standard", "Printer driver update", "2026-10-21 18:00", "Cancelled"),
    ]
    records = [dict(zip(["change_id", "asset_id", "change_type", "summary",
                         "implemented_at", "state"], c)) for c in changes]
    records.append(dict(records[0]))  # duplicate record returned by the API
    payload = {
        "_note": "Saved/mock response of GET /api/now/table/change_request - used so the project runs offline.",
        "source": "mock-itsm-change-api",
        "retrieved_at": "2026-11-01T00:00:00Z",
        "result_count": len(records),
        "result": records,
    }
    (RAW / "mock_api").mkdir(parents=True, exist_ok=True)
    (RAW / "mock_api" / "change_records_response.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {len(incidents)} incident rows, {len(assets)} asset rows, "
          f"{len(sla)} SLA rows, {len(records)} change records to {RAW}")


if __name__ == "__main__":
    main()
