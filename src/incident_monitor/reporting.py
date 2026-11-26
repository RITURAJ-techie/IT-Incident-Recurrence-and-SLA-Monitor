"""Charts (PNG) and the written report (Markdown) for submission.

Everything is regenerated from the *current* database contents, so the report
can be refreshed after edits in the app (button "Regenerate report").
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display needed
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import analysis, config  # noqa: E402

# Palette (validated categorical slots 1-2 + neutral inks)
BLUE, ORANGE = "#257de0", "#f47230"
INK, INK_2, GRID = "#191919", "#666666", "#e7e7e7"


def _style(ax, title: str, subtitle: str, xlabel: str) -> None:
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold", color=INK, pad=22)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9.5, color=INK_2)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=10)
    ax.tick_params(colors=INK_2, labelsize=9.5, length=0)
    ax.tick_params(axis="y", colors=INK)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def chart_sla_by_category(enriched: pd.DataFrame) -> plt.Figure:
    """Horizontal bar: SLA compliance % per standardized category."""
    table = analysis.sla_summary(enriched, "category")
    table = table[table["compliance_pct"].notna()].sort_values("compliance_pct")
    overall = analysis.kpis(enriched)["sla_compliance_pct"]

    fig, ax = plt.subplots(figsize=(8.5, 0.48 * max(len(table), 3) + 1.6), dpi=150)
    bars = ax.barh(table["category"], table["compliance_pct"], color=BLUE, height=0.6)
    for bar, (_, row) in zip(bars, table.iterrows()):
        ax.text(bar.get_width() + 1.2, bar.get_y() + bar.get_height() / 2,
                f"{row['compliance_pct']:.0f}%  ({row['met']}/{row['met'] + row['breached']})",
                va="center", fontsize=9, color=INK)
    if overall is not None:
        ax.axvline(overall, color=INK_2, linestyle="--", linewidth=1)
        ax.text(overall + 1, -0.95, f"overall {overall:.0f}%", color=INK_2,
                fontsize=9, va="center")
        ax.set_ylim(-1.3, len(table) - 0.4)
    ax.set_xlim(0, 115)
    ax.set_xticks(range(0, 101, 20))
    _style(ax, "SLA compliance by incident category",
           "Resolved tickets meeting their priority's resolution target "
           "(met / measurable). Tickets with missing times excluded.",
           "SLA compliance (%)")
    fig.tight_layout()
    return fig


def chart_incidents_by_service(enriched: pd.DataFrame) -> plt.Figure:
    """Stacked horizontal bar: first-time vs recurring incidents per service."""
    t = (enriched.assign(kind=enriched["is_recurring"].map({True: "Recurring",
                                                            False: "First occurrence"}))
         .pivot_table(index="service", columns="kind", values="ticket_id", aggfunc="count",
                      fill_value=0))
    for col in ("First occurrence", "Recurring"):
        if col not in t:
            t[col] = 0
    t["total"] = t["First occurrence"] + t["Recurring"]
    t = t.sort_values("total")

    fig, ax = plt.subplots(figsize=(8.5, 0.48 * max(len(t), 3) + 1.9), dpi=150)
    ax.barh(t.index, t["First occurrence"], color=BLUE, height=0.6, label="First occurrence",
            edgecolor="white", linewidth=1.5)
    ax.barh(t.index, t["Recurring"], left=t["First occurrence"], color=ORANGE, height=0.6,
            label=f"Recurring (same asset + category within {config.RECURRENCE_WINDOW_DAYS} days)",
            edgecolor="white", linewidth=1.5)
    for i, (svc, row) in enumerate(t.iterrows()):
        label = f"{row['total']}" + (f"  ({row['Recurring']} recurring)" if row["Recurring"] else "")
        ax.text(row["total"] + 0.2, i, label, va="center", fontsize=9, color=INK)
    ax.set_xlim(0, t["total"].max() * 1.35 + 1)
    _style(ax, "Incidents by service: first occurrence vs recurring",
           "Services whose orange share is large have problems that keep coming back.",
           "Number of incident tickets")
    ax.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=INK)
    fig.tight_layout()
    return fig


CHARTS = {
    "sla_compliance_by_category": chart_sla_by_category,
    "incidents_by_service": chart_incidents_by_service,
}


def chart_png(name: str, enriched: pd.DataFrame) -> bytes:
    """Render one chart to PNG bytes (used by the web app)."""
    fig = CHARTS[name](enriched)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def save_charts(enriched: pd.DataFrame, chart_dir: Path = config.CHART_DIR) -> list[Path]:
    chart_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in CHARTS:
        path = chart_dir / f"{name}.png"
        path.write_bytes(chart_png(name, enriched))
        paths.append(path)
    return paths


# ============================================================================ markdown report


def _md_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return "_No rows._"
    df = df.head(max_rows).copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    df = df.astype(object).where(df.notna(), "–")
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "| " + " | ".join("---" for _ in df.columns) + " |"
    body = ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([header, sep, *body])


def _findings(enriched: pd.DataFrame, k: dict) -> list[str]:
    out = []
    by_cat = analysis.sla_summary(enriched, "category").dropna(subset=["compliance_pct"])
    if not by_cat.empty:
        worst = by_cat.sort_values("compliance_pct").iloc[0]
        best = by_cat.sort_values("compliance_pct").iloc[-1]
        out.append(f"**SLA:** overall compliance is **{k['sla_compliance_pct']}%** "
                   f"({k['sla_met']} met, {k['sla_breached']} breached). "
                   f"*{worst['category']}* is weakest at {worst['compliance_pct']}%, "
                   f"*{best['category']}* strongest at {best['compliance_pct']}%.")
    hot = analysis.recurrence_hotspots(enriched)
    if not hot.empty:
        top = hot.iloc[0]
        out.append(f"**Recurrence:** {k['recurring_tickets']} of {k['total_tickets']} tickets "
                   f"({k['recurrence_rate_pct']}%) repeat an earlier fault on the same asset "
                   f"within {config.RECURRENCE_WINDOW_DAYS} days. The biggest hotspot is "
                   f"*{top['asset_name']} – {top['category']}* ({top['tickets']} tickets: "
                   f"{top['ticket_ids']}).")
    linked = analysis.change_related(enriched)
    if not linked.empty:
        changes = ", ".join(sorted(linked["linked_change_id"].unique()))
        out.append(f"**Changes:** {len(linked)} tickets were opened within "
                   f"{config.CHANGE_LINK_WINDOW_HOURS} h of a change on the same asset "
                   f"({changes}) – candidates for change-failure review.")
    svc = analysis.sla_summary(enriched, "service")
    svc = svc[svc["service"] != "Unknown"].dropna(subset=["compliance_pct"])
    if not svc.empty:
        w = svc.sort_values(["compliance_pct", "tickets"], ascending=[True, False]).iloc[0]
        out.append(f"**Services:** *{w['service']}* has the lowest compliance "
                   f"({w['compliance_pct']}% across {w['tickets']} tickets, "
                   f"{w['recurring']} recurring).")
    out.append(f"**Data quality:** {k['raw_category_labels']} different raw category labels were "
               f"standardized into {k['standard_categories']} categories; {k['sla_unknown']} "
               f"resolved tickets have no usable resolution time and are reported as "
               f"'Unknown' rather than guessed.")
    return out


def write_report(enriched: pd.DataFrame, quality_log: pd.DataFrame | None,
                 path: Path = config.OUTPUT_DIR / "report.md",
                 raw_counts: dict | None = None, clean_counts: dict | None = None) -> Path:
    k = analysis.kpis(enriched)
    lines = [
        "# IT Incident Recurrence & SLA Monitor – Report",
        "",
        f"_Generated {datetime.now():%Y-%m-%d %H:%M} from the application database._",
        "",
        "## 1. Steps performed",
        "",
        "1. **Load** three prepared CSV files (`incidents.csv`, `assets.csv`, `sla_targets.csv`) "
        "and a saved mock API response for change records "
        "(`mock_api/change_records_response.json`).",
        "2. **Check & clean**: remove exact duplicates and duplicate ticket ids (keep the most "
        "complete), reject impossible dates, clear resolution times earlier than the open time, "
        "flag missing resolution times, standardize categories/priorities/statuses/asset ids, "
        "check asset ids against the inventory. Every finding is written to "
        "`outputs/data_quality_log.csv`.",
        "3. **Combine & save**: cleaned tables and the combined, enriched incident table are "
        "saved to `data/processed/`, then loaded into the SQLite database used by the app.",
        f"4. **Analyse**: SLA status per ticket against its priority target, recurrence "
        f"(same asset + standardized category within {config.RECURRENCE_WINDOW_DAYS} days), "
        f"link to changes on the same asset within {config.CHANGE_LINK_WINDOW_HOURS} h, and "
        "asset/service comparisons.",
        "5. **Report**: this file, two labelled charts in `outputs/charts/`, summary CSVs in "
        "`outputs/`.",
        "",
    ]
    if raw_counts and clean_counts:
        rows = pd.DataFrame([{"dataset": d, "raw rows": raw_counts[d],
                              "clean rows": clean_counts.get(d, "–")} for d in raw_counts])
        lines += ["### Row counts", "", _md_table(rows), ""]
    if quality_log is not None and not quality_log.empty:
        qs = (quality_log.groupby(["dataset", "check"]).size().reset_index(name="findings")
              .sort_values(["dataset", "findings"], ascending=[True, False]))
        lines += ["### Data-quality findings", "", _md_table(qs), "",
                  "Examples of non-trivial fixes:", "",
                  _md_table(quality_log[quality_log["check"] != "standardization"]
                            [["dataset", "record_id", "issue", "action"]], 12), ""]

    lines += [
        "## 2. Key results", "",
        _md_table(pd.DataFrame([
            ("Tickets analysed", k["total_tickets"]),
            ("SLA compliance (met / measurable)", f"{k['sla_compliance_pct']}%"),
            ("SLA breached (resolved)", k["sla_breached"]),
            ("SLA unknown (missing resolution time)", k["sla_unknown"]),
            ("Open tickets already past target", k["open_breached"]),
            ("Recurring tickets", f"{k['recurring_tickets']} ({k['recurrence_rate_pct']}%)"),
            ("Asset/category groups with recurrence", k["recurring_groups"]),
            ("Tickets linked to a recent change", k["change_related_tickets"]),
            ("Average resolution time (h)", k["avg_resolution_hours"]),
            ("Raw category labels → standard categories",
             f"{k['raw_category_labels']} → {k['standard_categories']}"),
        ], columns=["Measure", "Value"])), "",
        "### Findings", "",
        *[f"- {f}" for f in _findings(enriched, k)], "",
        "## 3. Charts", "",
        "![SLA compliance by category](charts/sla_compliance_by_category.png)", "",
        "![Incidents by service](charts/incidents_by_service.png)", "",
        "## 4. Summary tables (each links back to source ticket ids)", "",
        "### SLA by category", "", _md_table(analysis.sla_summary(enriched, "category")), "",
        "### SLA by priority", "",
        _md_table(analysis.sla_summary(enriched, "priority").sort_values("priority")), "",
        "### Recurrence hotspots (problem-record candidates)", "",
        _md_table(analysis.recurrence_hotspots(enriched)[
            ["asset_name", "category", "tickets", "recurring", "breached", "linked_changes",
             "ticket_ids"]] if not analysis.recurrence_hotspots(enriched).empty
            else pd.DataFrame()), "",
        "### Service comparison", "", _md_table(analysis.sla_summary(enriched, "service")), "",
        "### Asset comparison", "", _md_table(analysis.asset_patterns(enriched)), "",
        "### Tickets opened shortly after a change", "",
        _md_table(analysis.change_related(enriched)), "",
        "### Category standardization map", "",
        _md_table(analysis.category_mapping(enriched), 60), "",
        "## 5. Limitations", "",
        "- Small, self-created dataset (two months, 12 assets) – patterns are illustrative.",
        "- Recurrence uses a simple rule (same asset + category within a time window); it does "
        "not compare description text.",
        "- Change links show timing only – they suggest, not prove, cause.",
        "- Missing resolution times are not imputed, so compliance is computed only on "
        "measurable tickets.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def save_summary_tables(enriched: pd.DataFrame, out_dir: Path = config.OUTPUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "summary_sla_by_category.csv": analysis.sla_summary(enriched, "category"),
        "summary_sla_by_priority.csv": analysis.sla_summary(enriched, "priority"),
        "summary_by_service.csv": analysis.sla_summary(enriched, "service"),
        "summary_by_asset.csv": analysis.asset_patterns(enriched),
        "summary_recurrence_hotspots.csv": analysis.recurrence_hotspots(enriched),
    }
    paths = []
    for name, df in tables.items():
        p = out_dir / name
        df.to_csv(p, index=False)
        paths.append(p)
    return paths
