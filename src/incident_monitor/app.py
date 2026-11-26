"""Flask web application: dashboard + incident CRUD.

Pages
-----
/                       dashboard: KPI tiles, two charts, summary tables (every row links
                        to the tickets behind it)
/tickets                list + filters (category, priority, service, asset, SLA status, ...)
/tickets/new            add a ticket          (Create)
/tickets/<id>           ticket detail         (Read)
/tickets/<id>/edit      edit a ticket         (Update)
/tickets/<id>/delete    delete a ticket       (Delete, POST only)
/data-quality           the data-quality log produced by the cleaning step
/api/summary            the KPI numbers as JSON
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)

from . import analysis, config, database, pipeline, reporting

def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of dicts with NaN/NaT turned into None (clean for templates)."""
    return [{k: (None if not isinstance(v, (str, list, dict)) and pd.isna(v) else v)
             for k, v in row.items()} for row in df.to_dict("records")]


FILTERS = ["category", "priority", "service", "asset_id", "sla_status", "status",
           "recurring", "change", "key", "q"]


def create_app(db_path: Path | str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "local-demo-only"  # only used for flash messages
    app.config["DB_PATH"] = str(db_path or config.DB_PATH)

    def db() -> str:
        return app.config["DB_PATH"]

    def enriched() -> pd.DataFrame:
        t = database.load_all(db())
        return analysis.enrich_incidents(t["incidents"], t["assets"], t["sla_targets"],
                                         t["changes"])

    @app.template_filter("dt")
    def _dt(value):
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return "—"
        return pd.Timestamp(value).strftime(config.DATETIME_FORMAT)

    @app.template_filter("num")
    def _num(value, digits=1):
        if value is None or pd.isna(value):
            return "—"
        return f"{value:.{digits}f}" if isinstance(value, float) else str(value)

    @app.context_processor
    def _globals():
        return {"categories": config.CANONICAL_CATEGORIES, "priorities": config.VALID_PRIORITIES,
                "statuses": config.VALID_STATUSES, "sla_statuses": analysis.SLA_STATUSES,
                "window_days": config.RECURRENCE_WINDOW_DAYS,
                "change_hours": config.CHANGE_LINK_WINDOW_HOURS}

    # ------------------------------------------------------------------ dashboard
    @app.get("/")
    def dashboard():
        df = enriched()
        hot = analysis.recurrence_hotspots(df)
        return render_template(
            "dashboard.html",
            k=analysis.kpis(df),
            by_category=analysis.sla_summary(df, "category").to_dict("records"),
            by_priority=analysis.sla_summary(df, "priority").sort_values("priority")
            .to_dict("records"),
            by_service=analysis.sla_summary(df, "service").to_dict("records"),
            by_asset=analysis.asset_patterns(df).to_dict("records"),
            hotspots=hot.to_dict("records") if not hot.empty else [],
            changes=analysis.change_related(df).to_dict("records"),
        )

    @app.get("/chart/<name>.png")
    def chart(name: str):
        if name not in reporting.CHARTS:
            abort(404)
        png = reporting.chart_png(name, enriched())
        return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/summary")
    def api_summary():
        return jsonify(analysis.kpis(enriched()))

    # ------------------------------------------------------------------ tickets (Read)
    @app.get("/tickets")
    def tickets():
        df = enriched()
        f = {k: request.args.get(k, "").strip() for k in FILTERS}
        for col in ["category", "priority", "service", "asset_id", "sla_status", "status"]:
            if f[col]:
                df = df[df[col] == f[col]]
        if f["recurring"] == "1":
            df = df[df["is_recurring"]]
        if f["change"] == "1":
            df = df[df["linked_change_id"].notna()]
        if f["key"]:
            df = df[df["recurrence_key"] == f["key"]]
        if f["q"]:
            q = f["q"].lower()
            df = df[df["short_description"].str.lower().str.contains(q, regex=False)
                    | df["ticket_id"].str.lower().str.contains(q, regex=False)
                    | df["category_raw"].fillna("").str.lower().str.contains(q, regex=False)]
        df = df.sort_values("opened_at", ascending=False)
        services = sorted(enriched()["service"].unique())
        return render_template("tickets.html", rows=records(df), f=f,
                               services=services, assets=database.list_assets(db()),
                               active=any(f.values()))

    @app.get("/tickets/<ticket_id>")
    def ticket_detail(ticket_id: str):
        df = enriched()
        match = df[df["ticket_id"] == ticket_id]
        if match.empty:
            abort(404)
        t = records(match)[0]
        related = (df[(df["recurrence_key"] == t["recurrence_key"])
                      & (df["ticket_id"] != ticket_id)].sort_values("opened_at")
                   if t["recurrence_key"] else df.iloc[0:0])
        change = database.get_change(t["linked_change_id"], db()) if t["linked_change_id"] else None
        return render_template("ticket_detail.html", t=t, related=records(related),
                               change=change)

    # ------------------------------------------------------------------ Create / Update
    @app.route("/tickets/new", methods=["GET", "POST"])
    def ticket_new():
        form = request.form.to_dict() if request.method == "POST" else {
            "status": "Open", "priority": "P3",
            "opened_at": pd.Timestamp.now().strftime(config.DATETIME_FORMAT)}
        errors = []
        if request.method == "POST":
            try:
                ticket_id, notes = database.create_incident(form, db())
                flash(f"Ticket {ticket_id} created.", "success")
                for n in notes:
                    flash(n, "info")
                return redirect(url_for("ticket_detail", ticket_id=ticket_id))
            except database.ValidationError as exc:
                errors = exc.errors
        return render_template("ticket_form.html", form=form, errors=errors, mode="new",
                               assets=database.list_assets(db()))

    @app.route("/tickets/<ticket_id>/edit", methods=["GET", "POST"])
    def ticket_edit(ticket_id: str):
        current = database.get_incident(ticket_id, db())
        if current is None:
            abort(404)
        form = request.form.to_dict() if request.method == "POST" else {
            **{k: ("" if v is None else v) for k, v in current.items()},
            "category": current["category"]}
        errors = []
        if request.method == "POST":
            try:
                notes = database.update_incident(ticket_id, form, db())
                flash(f"Ticket {ticket_id} updated.", "success")
                for n in notes:
                    flash(n, "info")
                return redirect(url_for("ticket_detail", ticket_id=ticket_id))
            except database.ValidationError as exc:
                errors = exc.errors
        return render_template("ticket_form.html", form=form, errors=errors, mode="edit",
                               ticket_id=ticket_id, assets=database.list_assets(db()))

    # ------------------------------------------------------------------ Delete
    @app.post("/tickets/<ticket_id>/delete")
    def ticket_delete(ticket_id: str):
        if database.delete_incident(ticket_id, db()):
            flash(f"Ticket {ticket_id} deleted.", "success")
        else:
            flash(f"Ticket {ticket_id} was not found.", "error")
        return redirect(url_for("tickets"))

    # ------------------------------------------------------------------ data quality & outputs
    @app.get("/data-quality")
    def data_quality():
        path = config.OUTPUT_DIR / "data_quality_log.csv"
        log = pd.read_csv(path).fillna("") if path.exists() else pd.DataFrame(
            columns=["dataset", "record_id", "check", "issue", "action"])
        check = request.args.get("check", "")
        summary = (log.groupby(["dataset", "check"]).size().reset_index(name="count")
                   .to_dict("records")) if not log.empty else []
        if check:
            log = log[log["check"] == check]
        mapping = analysis.category_mapping(enriched()).to_dict("records")
        return render_template("data_quality.html", rows=log.to_dict("records"),
                               summary=summary, check=check, mapping=mapping)

    @app.post("/report/regenerate")
    def regenerate_report():
        pipeline.generate_outputs(db_path=db())
        flash("Report, charts and summary CSVs regenerated in the outputs/ folder.", "success")
        return redirect(request.referrer or url_for("dashboard"))

    @app.get("/outputs/<path:filename>")
    def outputs(filename: str):
        return send_from_directory(config.OUTPUT_DIR, filename)

    @app.errorhandler(404)
    def not_found(_):
        return render_template("404.html"), 404

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
