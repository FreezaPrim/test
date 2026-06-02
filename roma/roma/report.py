"""Local insights report - templated from what the analyst learned.

No Claude. If a local Ollama model is available, Roma uses it to add a short
narrative summary on top of the hard numbers; otherwise the report is the
structured findings, which stand on their own.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import analyst, config, localchat


def _md_drivers(d: dict) -> list[str]:
    if "error" in d:
        return [f"_Drivers: {d['error']}_", ""]
    out = [f"### Drivers of `{d['target']}`",
           f"Model: {d['model']} ({d['score_metric']} = {d['score']}), "
           f"{d['rows_used']} rows.", "",
           "| Feature | Importance | Effect |", "|---|---|---|"]
    for f in d["features"]:
        eff = f"more = {f['effect']} {d['target']}" if f.get("effect") else ""
        out.append(f"| {f['feature']} | {f['importance']} | {eff} |")
    out.append("")
    return out


def _md_segments(s: dict) -> list[str]:
    if "error" in s:
        return [f"_Segments: {s['error']}_", ""]
    out = [f"### Segments (in `{s['table']}`)"]
    for seg in s["segments"]:
        avgs = ", ".join(f"{k}={v}" for k, v in seg["averages"].items())
        out.append(f"- **Segment {seg['segment']}** ({seg['size']} rows): {avgs}")
    out.append("")
    return out


def _md_trend(t: dict) -> list[str]:
    if "error" in t:
        return [f"_Trends: {t['error']}_", ""]
    out = ["### Trends over time"]
    for tr in t["trends"]:
        out.append(f"- `{tr['metric']}` is **{tr['direction']}** over "
                   f"`{tr['over']}` ({tr['first']} -> {tr['last']})")
    out.append("")
    return out


def _md_anom(a: dict) -> list[str]:
    if "error" in a:
        return [f"_Anomalies: {a['error']}_", ""]
    out = ["### Anomalies (unusual rows)"]
    for an in a["anomalies"]:
        vals = ", ".join(f"{k}={v}" for k, v in an["values"].items())
        out.append(f"- `{an['table']}` row {an['row']}: {vals}")
    out.append("")
    return out


def generate_report(conn, verbose: bool = True) -> Path:
    if verbose:
        print("    . learning from your data ...")
    snap = analyst.learn(conn)

    lines = ["# Customer Experience Insights",
             f"_Generated locally on {snap['learned_at']}_", ""]
    lines += ["## Data loaded"]
    for t in snap["tables"]:
        lines.append(f"- `{t['table']}` - {t['rows']} rows "
                     f"({len(t['columns'])} columns)")
    if snap["links"]:
        lines.append("- Linked on: " +
                     ", ".join(l["column"] for l in snap["links"]))
    lines.append("")

    from . import kpis
    kpi_rows = kpis.compute(conn)
    if kpi_rows:
        lines += ["## CX KPIs"]
        for k in kpi_rows:
            lines.append(f"- **{k['name']}**: {k['value']}  ({k['detail']})")
        lines.append("")

    lines += ["## Key findings"]
    lines += _md_drivers(snap["drivers"])
    lines += _md_segments(snap["segments"])
    lines += _md_trend(snap["trend"])
    lines += _md_anom(snap["anomalies"])
    report_md = "\n".join(lines)

    # Optional local-LLM narrative on top.
    ok, model = localchat.available()
    if ok and model:
        if verbose:
            print(f"    . adding narrative with local model '{model}' ...")
        try:
            system = ("You are a CX analyst. Write a short executive summary "
                      "(4-6 bullets) using ONLY the findings given. No invented "
                      "numbers.")
            narrative = localchat.chat(model, system, report_md)
            if narrative:
                report_md = ("# Customer Experience Insights\n\n## Executive summary\n"
                             + narrative + "\n\n" + report_md.split("\n", 2)[2])
        except Exception:  # noqa: BLE001
            pass

    config.ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = config.REPORTS_DIR / f"insights_{stamp}.md"
    out.write_text(report_md, encoding="utf-8")
    return out
