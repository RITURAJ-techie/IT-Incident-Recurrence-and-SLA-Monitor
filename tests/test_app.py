import pytest

from incident_monitor import database
from incident_monitor.app import create_app


@pytest.fixture()
def client(db_path):
    app = create_app(db_path)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("url", ["/", "/tickets", "/tickets?category=Email",
                                 "/tickets?recurring=1", "/data-quality", "/api/summary",
                                 "/chart/sla_compliance_by_category.png",
                                 "/chart/incidents_by_service.png"])
def test_pages_render(client, url):
    assert client.get(url).status_code == 200


def test_crud_cycle(client, db_path):
    form = {"short_description": "Outlook keeps asking for password", "category": "E-mail",
            "priority": "high", "status": "Open", "asset_id": "AST-004",
            "opened_at": "2026-11-02 09:00", "resolved_at": ""}
    # create
    resp = client.post("/tickets/new", data=form)
    assert resp.status_code == 302
    tid = resp.headers["Location"].rsplit("/", 1)[-1]
    rec = database.get_incident(tid, db_path)
    assert rec["category"] == "Email" and rec["priority"] == "P2"
    # read
    assert tid.encode() in client.get(f"/tickets/{tid}").data
    # update
    form.update(status="Resolved", resolved_at="2026-11-02 12:00")
    assert client.post(f"/tickets/{tid}/edit", data=form).status_code == 302
    assert database.get_incident(tid, db_path)["status"] == "Resolved"
    # delete
    assert client.post(f"/tickets/{tid}/delete").status_code == 302
    assert database.get_incident(tid, db_path) is None
    assert client.get(f"/tickets/{tid}").status_code == 404


def test_validation_rejects_bad_input(client, db_path):
    bad = {"short_description": "x", "priority": "P9", "status": "Open",
           "opened_at": "2026-09-31 10:00", "asset_id": "AST-999"}
    resp = client.post("/tickets/new", data=bad)
    assert resp.status_code == 200
    assert b"Please fix the following" in resp.data
    with pytest.raises(database.ValidationError):
        database.validate_incident({**bad, "opened_at": "2026-10-02 10:00",
                                    "resolved_at": "2026-10-01 10:00"}, db_path)
