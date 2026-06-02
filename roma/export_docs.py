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
