import pandas as pd

from incident_monitor import analysis

ASSETS = pd.DataFrame([
    {"asset_id": "A1", "asset_name": "VPN", "asset_type": "Firewall", "service": "Remote",
     "criticality": "High"},
])
SLA = pd.DataFrame([{"priority": "P2", "resolution_target_hours": 8.0}])
CHANGES = pd.DataFrame([{"change_id": "C1", "asset_id": "A1",
                         "implemented_at": pd.Timestamp("2026-10-01 06:00"),
                         "state": "Implemented"}])


def _inc(tid, opened, resolved, status="Resolved", asset="A1", cat="Network"):
    return {"ticket_id": tid, "opened_at": pd.Timestamp(opened),
            "resolved_at": pd.Timestamp(resolved) if resolved else pd.NaT,
            "status": status, "priority": "P2", "category": cat, "category_raw": cat,
            "asset_id": asset, "reassignment_count": 0}


def enrich(rows):
    return analysis.enrich_incidents(pd.DataFrame(rows), ASSETS, SLA, CHANGES,
                                     as_of=pd.Timestamp("2026-11-01"))


def test_sla_status_rules():
    df = enrich([
        _inc("T1", "2026-10-01 08:00", "2026-10-01 12:00"),   # 4h  -> met
        _inc("T2", "2026-10-20 08:00", "2026-10-21 08:00"),   # 24h -> breached
        _inc("T3", "2026-10-25 08:00", None),                 # resolved w/o time -> unknown
        _inc("T4", "2026-10-31 20:00", None, status="Open"),  # 4h open -> within SLA
    ]).set_index("ticket_id")
    assert df.loc["T1", "sla_status"] == analysis.SLA_MET
    assert df.loc["T2", "sla_status"] == analysis.SLA_BREACHED
    assert df.loc["T3", "sla_status"] == analysis.SLA_UNKNOWN
    assert df.loc["T4", "sla_status"] == analysis.SLA_OPEN_OK
    k = analysis.kpis(df.reset_index())
    assert k["sla_compliance_pct"] == 50.0  # unknown/open excluded from the denominator


def test_recurrence_needs_same_asset_and_category_within_window():
    df = enrich([
        _inc("T1", "2026-10-01 08:00", "2026-10-01 09:00"),
        _inc("T2", "2026-10-03 08:00", "2026-10-03 09:00"),                # +2 days -> recurring
        _inc("T3", "2026-10-04 08:00", "2026-10-04 09:00", cat="Email"),   # other category
        _inc("T4", "2026-10-30 08:00", "2026-10-30 09:00"),                # +27 days -> not
    ]).set_index("ticket_id")
    assert df["is_recurring"].to_dict() == {"T1": False, "T2": True, "T3": False, "T4": False}
    assert df.loc["T2", "previous_ticket_id"] == "T1"


def test_change_link_window():
    df = enrich([
        _inc("T1", "2026-10-01 09:00", "2026-10-01 10:00"),  # 3 h after C1
        _inc("T2", "2026-10-09 09:00", "2026-10-09 10:00"),  # 8 days after
    ]).set_index("ticket_id")
    assert df.loc["T1", "linked_change_id"] == "C1"
    assert pd.isna(df.loc["T2", "linked_change_id"])


def test_hotspots_list_source_tickets():
    df = enrich([_inc("T1", "2026-10-01 08:00", "2026-10-01 09:00"),
                 _inc("T2", "2026-10-02 08:00", "2026-10-02 09:00")])
    hot = analysis.recurrence_hotspots(df)
    assert hot.iloc[0]["ticket_ids"] == "T1, T2"
