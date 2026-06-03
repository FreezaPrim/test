"""Export Roma analyses to PowerPoint, Word, and PDF - e&-branded, offline.

Each exporter pulls the same detractor/KPI/driver data the Excel exporter uses,
so all formats stay consistent. Heavy imports are done inside functions so the
rest of Roma still runs if one library is missing.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from . import analyst, config, database, detractors, kpis

BRAND_RED = "E00800"
DARK = "1A1A1A"


def _gather(conn, time_q: str = "") -> dict[str, Any]:
    """Collect everything once, so every format renders the same content."""
    data = {"kpis": kpis.compute(conn), "drivers": analyst.drivers(conn),
            "detractors": detractors.full_report(conn, time_q)}
    return data


def _stamp(kind: str, ext: str) -> Path:
    config.ensure_dirs()
    s = datetime.now().strftime("%Y%m%d_%H%M%S")
    return config.REPORTS_DIR / f"{kind}_{s}.{ext}"


# ------------------------------- PowerPoint -------------------------------- #

def export_pptx(conn, time_q: str = "") -> Path:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    red = RGBColor.from_string(BRAND_RED)
    dark = RGBColor.from_string(DARK)
    white = RGBColor.from_string("FFFFFF")
    data = _gather(conn, time_q)
    det = data["detractors"]
    period = det.get("count", {}).get("period", "all data") if "error" not in det else ""

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    def bar(slide):
        box = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.25))
        box.fill.solid(); box.fill.fore_color.rgb = red
        box.line.fill.background()

    def textbox(slide, left, top, width, height, text, size, *, bold=False,
                color=dark, align=PP_ALIGN.LEFT):
        tb = slide.shapes.add_textbox(left, top, width, height)
        tf = tb.text_frame; tf.word_wrap = True
        p = tf.paragraphs[0]; p.alignment = align
        run = p.add_run(); run.text = text
        run.font.size = Pt(size); run.font.bold = bold
        run.font.color.rgb = color; run.font.name = "Arial"
        return tb

    # Title slide
    s = prs.slides.add_slide(blank)
    fill = s.background.fill; fill.solid(); fill.fore_color.rgb = dark
    textbox(s, Inches(0.8), Inches(2.6), Inches(11.7), Inches(1.5),
            "Customer Experience Report", 44, bold=True, color=white)
    textbox(s, Inches(0.8), Inches(4.0), Inches(11.7), Inches(0.8),
            f"e& Consumer  |  {period}  |  prepared by Roma", 18, color=white)

    # KPI slide
    if data["kpis"]:
        s = prs.slides.add_slide(blank); bar(s)
        textbox(s, Inches(0.6), Inches(0.5), Inches(12), Inches(0.8),
                "Key CX KPIs", 32, bold=True, color=red)
        top = Inches(1.7)
        for k in data["kpis"][:6]:
            textbox(s, Inches(0.8), top, Inches(6), Inches(0.6),
                    k["name"], 18, bold=True)
            textbox(s, Inches(6.8), top, Inches(5.7), Inches(0.6),
                    str(k["value"]), 18, color=red, bold=True)
            top += Inches(0.8)

    # Detractor summary slide
    if "error" not in det:
        c = det["count"]
        s = prs.slides.add_slide(blank); bar(s)
        textbox(s, Inches(0.6), Inches(0.5), Inches(12), Inches(0.8),
                f"Detractors - {period}", 32, bold=True, color=red)
        textbox(s, Inches(0.8), Inches(2.0), Inches(11), Inches(2),
                f"{c['detractors']}", 96, bold=True, color=dark)
        textbox(s, Inches(0.8), Inches(4.3), Inches(11), Inches(0.8),
                f"detractors out of {c['base']} respondents ({c['pct']}%)", 22)
        # top breakdown
        if det["breakdowns"]:
            b = det["breakdowns"][0]
            lines = "   ".join(f"{r.get(b['dimension'])}: {r.get('detractors')}"
                               for r in b["rows"][:5])
            textbox(s, Inches(0.8), Inches(5.3), Inches(12), Inches(1),
                    f"By {b['dimension']}:  {lines}", 16, color=dark)

    # Drivers slide
    d = data["drivers"]
    if "error" not in d:
        s = prs.slides.add_slide(blank); bar(s)
        textbox(s, Inches(0.6), Inches(0.5), Inches(12), Inches(0.8),
                f"What drives {d['target']}", 32, bold=True, color=red)
        top = Inches(1.8)
        for f in d["features"][:6]:
            eff = f" ({f['effect']})" if f.get("effect") else ""
            textbox(s, Inches(0.8), top, Inches(11.7), Inches(0.5),
                    f"{f['feature']}  -  {f['importance']}{eff}", 18)
            top += Inches(0.7)

    out = _stamp("cx_presentation", "pptx")
    prs.save(out)
    return out


# ---------------------------------- Word ----------------------------------- #

def export_docx(conn, time_q: str = "") -> Path:
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    red = RGBColor.from_string(BRAND_RED)
    data = _gather(conn, time_q)
    det = data["detractors"]
    period = det.get("count", {}).get("period", "all data") if "error" not in det else ""

    doc = Document()
    style = doc.styles["Normal"]; style.font.name = "Arial"; style.font.size = Pt(11)

    title = doc.add_heading("Customer Experience Report", level=0)
    for r in title.runs:
        r.font.color.rgb = red
    doc.add_paragraph(f"e& Consumer  |  {period}  |  prepared by Roma").italic = True

    if data["kpis"]:
        doc.add_heading("Key CX KPIs", level=1)
        t = doc.add_table(rows=1, cols=3); t.style = "Light Grid Accent 1"
        h = t.rows[0].cells
        h[0].text, h[1].text, h[2].text = "KPI", "Value", "Detail"
        for k in data["kpis"]:
            row = t.add_row().cells
            row[0].text, row[1].text, row[2].text = k["name"], str(k["value"]), k["detail"]

    if "error" not in det:
        c = det["count"]
        doc.add_heading(f"Detractors - {period}", level=1)
        doc.add_paragraph(
            f"{c['detractors']} detractors out of {c['base']} respondents "
            f"({c['pct']}%), scored 0-6 on {c['nps']}.")
        for b in det["breakdowns"]:
            doc.add_heading(f"By {b['dimension']}", level=2)
            t = doc.add_table(rows=1, cols=2); t.style = "Light Grid Accent 1"
            t.rows[0].cells[0].text = str(b["dimension"])
            t.rows[0].cells[1].text = "Detractors"
            for r in b["rows"]:
                row = t.add_row().cells
                row[0].text = str(r.get(b["dimension"]))
                row[1].text = str(r.get("detractors"))
        rp = det["repeated"]
        if "error" not in rp and rp["repeat_count"]:
            doc.add_heading("Repeated detractors", level=2)
            doc.add_paragraph(f"{rp['repeat_count']} customers were detractors "
                              f"more than once (by {rp['id']}).")

    d = data["drivers"]
    if "error" not in d:
        doc.add_heading(f"What drives {d['target']}", level=1)
        doc.add_paragraph(f"Model {d['model']} ({d['score_metric']}={d['score']}), "
                          f"{d['rows_used']} rows.")
        for f in d["features"]:
            eff = f" ({f['effect']})" if f.get("effect") else ""
            doc.add_paragraph(f"{f['feature']} - {f['importance']}{eff}",
                              style="List Bullet")

    out = _stamp("cx_report", "docx")
    doc.save(out)
    return out


# ----------------------------------- PDF ----------------------------------- #

def export_pdf(conn, time_q: str = "") -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle)

    red = colors.HexColor("#" + BRAND_RED)
    dark = colors.HexColor("#" + DARK)
    data = _gather(conn, time_q)
    det = data["detractors"]
    period = det.get("count", {}).get("period", "all data") if "error" not in det else ""

    out = _stamp("cx_report", "pdf")
    doc = SimpleDocTemplate(str(out), pagesize=A4, topMargin=2 * cm)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], textColor=red,
                        fontName="Helvetica-Bold")
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=dark)
    body = styles["BodyText"]
    flow = [Paragraph("Customer Experience Report", h1),
            Paragraph(f"e& Consumer | {period} | prepared by Roma", body),
            Spacer(1, 0.5 * cm)]

    def table(headers, rows):
        t = Table([headers] + rows, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), dark),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FCE9E7")]),
        ]))
        return t

    if data["kpis"]:
        flow.append(Paragraph("Key CX KPIs", h2))
        flow.append(table(["KPI", "Value", "Detail"],
                          [[k["name"], str(k["value"]), k["detail"]] for k in data["kpis"]]))
        flow.append(Spacer(1, 0.4 * cm))

    if "error" not in det:
        c = det["count"]
        flow.append(Paragraph(f"Detractors - {period}", h2))
        flow.append(Paragraph(f"{c['detractors']} of {c['base']} respondents "
                              f"({c['pct']}%), scored 0-6 on {c['nps']}.", body))
        for b in det["breakdowns"]:
            flow.append(Spacer(1, 0.2 * cm))
            flow.append(Paragraph(f"By {b['dimension']}", h2))
            flow.append(table([str(b["dimension"]), "Detractors"],
                              [[str(r.get(b["dimension"])), str(r.get("detractors"))]
                               for r in b["rows"]]))

    d = data["drivers"]
    if "error" not in d:
        flow.append(Spacer(1, 0.4 * cm))
        flow.append(Paragraph(f"What drives {d['target']}", h2))
        flow.append(table(["Feature", "Importance", "Effect"],
                          [[f["feature"], str(f["importance"]), f.get("effect") or ""]
                           for f in d["features"]]))

    doc.build(flow)
    return out


# ─────────────────────────── TNPS PowerPoint ────────────────────────────── #

def export_tnps_pptx(conn, time_q: str = "") -> Path:
    """Full TNPS-focused PowerPoint deck with e& branding (10 slides)."""
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    from . import tnps_analytics as ta, winback as wb, kpis as kp

    red   = RGBColor.from_string(BRAND_RED)
    dark  = RGBColor.from_string(DARK)
    white = RGBColor.from_string("FFFFFF")
    grey  = RGBColor.from_string("F2F2F2")
    amber = RGBColor.from_string("FFA500")

    prs = Presentation()
    prs.slide_width  = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # ── helpers ────────────────────────────────────────────────────────── #
    def _bar(slide, height=0.22):
        box = slide.shapes.add_shape(
            1, 0, 0, prs.slide_width, Inches(height))
        box.fill.solid(); box.fill.fore_color.rgb = red
        box.line.fill.background()

    def _tb(slide, left, top, width, height, text, size, *,
            bold=False, color=dark, align=PP_ALIGN.LEFT, italic=False):
        tb = slide.shapes.add_textbox(
            Inches(left), Inches(top), Inches(width), Inches(height))
        tf = tb.text_frame; tf.word_wrap = True
        p = tf.paragraphs[0]; p.alignment = align
        run = p.add_run(); run.text = str(text)
        run.font.size = Pt(size); run.font.bold = bold
        run.font.italic = italic
        run.font.color.rgb = color; run.font.name = "Arial"
        return tb

    def _kpi_box(slide, left, top, label, value, sub=""):
        box = slide.shapes.add_shape(
            1, Inches(left), Inches(top), Inches(2.8), Inches(1.6))
        box.fill.solid(); box.fill.fore_color.rgb = grey
        box.line.color.rgb = RGBColor.from_string("DDDDDD")
        _tb(slide, left + 0.12, top + 0.08, 2.6, 0.4, label, 11, color=dark)
        _tb(slide, left + 0.12, top + 0.45, 2.6, 0.7, value, 28,
            bold=True, color=red, align=PP_ALIGN.LEFT)
        if sub:
            _tb(slide, left + 0.12, top + 1.1, 2.6, 0.4, sub, 9,
                italic=True, color=dark)

    def _table_on_slide(slide, top_in, headers, rows, max_rows=15):
        rows = rows[:max_rows]
        if not rows:
            return
        n_cols = len(headers)
        col_w  = 12.0 / n_cols
        # header row
        for j, h in enumerate(headers):
            box = slide.shapes.add_shape(
                1, Inches(0.6 + j * col_w), Inches(top_in),
                Inches(col_w), Inches(0.38))
            box.fill.solid(); box.fill.fore_color.rgb = dark
            box.line.fill.background()
            _tb(slide, 0.6 + j * col_w + 0.05, top_in + 0.04,
                col_w - 0.1, 0.32, h, 10, bold=True, color=white)
        # data rows
        for i, row in enumerate(rows):
            fill_rgb = grey if i % 2 == 0 else white
            for j, cell in enumerate(row):
                box = slide.shapes.add_shape(
                    1, Inches(0.6 + j * col_w), Inches(top_in + 0.38 + i * 0.36),
                    Inches(col_w), Inches(0.36))
                box.fill.solid(); box.fill.fore_color.rgb = fill_rgb
                box.line.color.rgb = RGBColor.from_string("EEEEEE")
                _tb(slide, 0.6 + j * col_w + 0.05,
                    top_in + 0.38 + i * 0.36 + 0.04,
                    col_w - 0.1, 0.3, str(cell), 9, color=dark)

    def _heading(slide, text, sub=""):
        _bar(slide)
        _tb(slide, 0.6, 0.38, 12.0, 0.8, text, 28, bold=True, color=dark)
        if sub:
            _tb(slide, 0.6, 1.05, 12.0, 0.4, sub, 13, italic=True, color=dark)

    stamp = f"e& Consumer  |  {datetime.now():%B %Y}  |  prepared by Roma"

    # load TNPS data once
    import pandas as pd  # noqa: PLC0415
    df, cols = ta.load_tnps_df(conn)

    # ── Slide 1: Title ─────────────────────────────────────────────────── #
    s = prs.slides.add_slide(blank)
    bg = s.background.fill; bg.solid(); bg.fore_color.rgb = dark
    _tb(s, 0.8, 2.2, 11.7, 1.4, "tNPS Performance Report",
        44, bold=True, color=white)
    _tb(s, 0.8, 3.75, 11.7, 0.7, stamp, 18, italic=True, color=white)
    _bar(s, height=0.18)

    # ── Slide 2: KPI Summary ───────────────────────────────────────────── #
    s = prs.slides.add_slide(blank)
    _heading(s, "KPI Summary", stamp)
    kpi_list = kp.compute(conn)
    kpi_map = {k["name"]: (k["value"], k.get("detail","")) for k in kpi_list}
    positions = [(0.5, 2.2), (3.5, 2.2), (6.5, 2.2), (9.8, 2.2),
                 (0.5, 4.1), (3.5, 4.1), (6.5, 4.1), (9.8, 4.1)]
    for i, (name, (val, det)) in enumerate(list(kpi_map.items())[:8]):
        lx, ty = positions[i]
        short = name.split("(")[0].strip()
        sub = det[:55] + "…" if len(det) > 55 else det
        _kpi_box(s, lx, ty, short, val, sub)

    # ── Slide 3: Detractor Trend ───────────────────────────────────────── #
    s = prs.slides.add_slide(blank)
    _heading(s, "Monthly Detractor Rate Trend")
    if not df.empty:
        monthly = ta.build_monthly_trend(df, cols)
        if not monthly.empty:
            mcol = next((c for c in monthly.columns
                         if "det" in c.lower() and "rate" in c.lower()), None)
            dcol = next((c for c in monthly.columns if "date" in c.lower() or "month" in c.lower()), None)
            if mcol and dcol:
                rows = [[str(r[dcol])[:7], f"{r[mcol]:.1f}%"]
                        for _, r in monthly.iterrows()]
                _table_on_slide(s, 1.6, ["Month", "Detractor Rate %"], rows, 10)

    # ── Slide 4: Top Detractors by Short Code ─────────────────────────── #
    s = prs.slides.add_slide(blank)
    _heading(s, "Top Detractors by Short Code")
    if not df.empty:
        sc_col = cols.get("short_code")
        nps_col = cols.get("nps")
        if sc_col and nps_col and sc_col in df.columns:
            top_sc = (df[df[nps_col].apply(pd.to_numeric, args=("coerce",)) <= 6]
                      .groupby(sc_col).size()
                      .nlargest(12).reset_index())
            top_sc.columns = ["Short Code", "Detractors"]
            _table_on_slide(s, 1.6, list(top_sc.columns),
                            top_sc.values.tolist(), 12)

    # ── Slide 5: Top Detractors by Queue ──────────────────────────────── #
    s = prs.slides.add_slide(blank)
    _heading(s, "Top Detractors by Agent Queue")
    if not df.empty:
        aq_col = cols.get("agent_queue")
        nps_col = cols.get("nps")
        if aq_col and nps_col and aq_col in df.columns:
            top_q = (df[pd.to_numeric(df[nps_col], errors="coerce") <= 6]
                     .groupby(aq_col).size()
                     .nlargest(12).reset_index())
            top_q.columns = ["Agent Queue", "Detractors"]
            _table_on_slide(s, 1.6, list(top_q.columns),
                            top_q.values.tolist(), 12)

    # ── Slide 6: NPS Waterfall ─────────────────────────────────────────── #
    s = prs.slides.add_slide(blank)
    _heading(s, "NPS Monthly Waterfall")
    if not df.empty:
        wf = ta.build_nps_waterfall(df, cols)
        if not wf.empty:
            cols_show = [c for c in ["Month","NPS_Score","Promoters","Detractors",
                                      "Net_Gain","Detractor_Rate_%"] if c in wf.columns]
            rows = [[str(r[c]) for c in cols_show] for _, r in wf.iterrows()]
            _table_on_slide(s, 1.6, cols_show, rows, 10)

    # ── Slide 7: Forecast ─────────────────────────────────────────────── #
    s = prs.slides.add_slide(blank)
    _heading(s, "30-Day Detractor Rate Forecast (Holt-Winters)")
    if not df.empty:
        from . import forecast as fc
        daily = ta.build_daily_trend(df, cols)
        if not daily.empty:
            try:
                fcast = fc.build_forecast(daily, horizon=30)
                if not fcast.empty:
                    fcol = next((c for c in fcast.columns if "forecast" in c.lower()
                                 and "lower" not in c.lower() and "upper" not in c.lower()), None)
                    lcol = next((c for c in fcast.columns if "lower" in c.lower()), None)
                    ucol = next((c for c in fcast.columns if "upper" in c.lower()), None)
                    dc   = "Date" if "Date" in fcast.columns else fcast.columns[0]
                    rows_fc = []
                    for _, r in fcast.iterrows():
                        rows_fc.append([str(r[dc])[:10],
                                        f"{r[fcol]:.1f}%" if fcol else "",
                                        f"{r[lcol]:.1f}%–{r[ucol]:.1f}%" if lcol and ucol else ""])
                    hdrs = ["Date", "Forecast Det.%", "Range"]
                    _table_on_slide(s, 1.6, hdrs, rows_fc, 12)
                    breach = fcast.attrs.get("breach_date", "")
                    if breach and breach != "No breach in horizon":
                        _tb(s, 0.6, 6.6, 12, 0.5,
                            f"⚠  Projected 40% target breach: {breach}",
                            13, color=amber, bold=True)
            except Exception:
                pass

    # ── Slide 8: Toxic Combos ─────────────────────────────────────────── #
    s = prs.slides.add_slide(blank)
    _heading(s, "Toxic Short Code + Queue Combos")
    if not df.empty:
        tox = ta.build_toxic_combos(df, cols)
        if not tox.empty:
            show_cols = [c for c in tox.columns if c not in ("_ord",)][:5]
            rows = [[str(r[c]) for c in show_cols] for _, r in tox.head(12).iterrows()]
            _table_on_slide(s, 1.6, show_cols, rows, 12)

    # ── Slide 9: Agent Ranking ─────────────────────────────────────────── #
    s = prs.slides.add_slide(blank)
    _heading(s, "Agent Detractor Ranking & Peer Benchmark")
    if not df.empty:
        ar = ta.build_agent_ranking(df, cols)
        if not ar.empty:
            show_cols = [c for c in ar.columns][:5]
            rows = [[str(r[c]) for c in show_cols] for _, r in ar.head(12).iterrows()]
            _table_on_slide(s, 1.6, show_cols, rows, 12)

    # ── Slide 10: Win-Back Recovery Rate ──────────────────────────────── #
    s = prs.slides.add_slide(blank)
    _heading(s, "Churn & Win-Back Recovery Rate")
    if not df.empty:
        wbs = wb.build_winback_summary(df, cols)
        if not wbs.empty:
            show_cols = [c for c in wbs.columns]
            rows = [[str(r[c]) for c in show_cols] for _, r in wbs.iterrows()]
            _table_on_slide(s, 1.6, show_cols, rows, 10)
        else:
            _tb(s, 0.6, 3.0, 12, 1,
                "Not enough multi-month MSISDN data to compute recovery rate.\n"
                "Load 2+ months of dated survey data with customer IDs to enable this.",
                14, italic=True, color=dark)

    out = _stamp("tnps_presentation", "pptx")
    prs.save(out)
    return out
