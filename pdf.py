"""
PDF generation (results and splits printouts) via reportlab.

Two outputs: a single competitor's :func:`splits_slip_pdf` (the finish-chute
slip in print form) and a whole-event :func:`class_results_pdf`. Both return raw
PDF bytes so the Flask layer can stream them as a download. Kept presentation-only:
all figures come pre-formatted from the engine/view layer.
"""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)

_GREEN = colors.HexColor("#1f6f43")
_LINE = colors.HexColor("#d9dee4")


def _styles():
    s = getSampleStyleSheet()
    return s


def _doc(buffer):
    return SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Punchcard",
    )


def _table_style(header=True):
    cmds = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, _LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        cmds += [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, 0), (-1, 0), _GREEN),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, _GREEN),
        ]
    return TableStyle(cmds)


def splits_slip_pdf(row: dict, event: dict) -> bytes:
    """One competitor's printable splits slip as PDF bytes."""
    buf = io.BytesIO()
    doc = _doc(buf)
    styles = _styles()
    story = []

    story.append(Paragraph(f"<b>{escape(event.get('name', ''))}</b>", styles["Title"]))
    story.append(Paragraph(escape(event.get("date", "")), styles["Normal"]))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph(f"<b>{escape(row['name'])}</b>", styles["Heading2"]))
    sub = escape(row.get("class", ""))
    if row.get("club"):
        sub += f" &middot; {escape(row['club'])}"
    if row.get("si"):
        sub += f" &middot; SI {row['si']}"
    story.append(Paragraph(sub, styles["Normal"]))
    story.append(Spacer(1, 4 * mm))

    meta = [
        ["Start", row.get("start") or "-", "Finish", row.get("finish") or "-"],
        ["Time", row.get("time") or "-", "Status", row.get("status_label") or "OK"],
    ]
    if row.get("points") is not None:
        meta.append(["Points", str(row["points"]), "", ""])
    mt = Table(meta, colWidths=[24 * mm, 40 * mm, 24 * mm, 40 * mm])
    mt.setStyle(_table_style(header=False))
    story.append(mt)
    story.append(Spacer(1, 6 * mm))

    if row.get("missed_control"):
        story.append(Paragraph(
            f"<font color='#b8302f'>Mispunch — missed control "
            f"{row['missed_control']}</font>", styles["Normal"]))
        story.append(Spacer(1, 3 * mm))

    splits = row.get("splits") or []
    if splits:
        data = [["Control", "Leg", "Cumulative"]]
        data += [[str(s["control"]), s["leg"], s["cumulative"]] for s in splits]
        st = Table(data, colWidths=[40 * mm, 40 * mm, 40 * mm])
        st.setStyle(_table_style())
        story.append(st)

    doc.build(story)
    return buf.getvalue()


def start_list_pdf(classes: list[dict], event: dict) -> bytes:
    """Start list grouped by class. ``classes`` = [{name, rows:[{bib,name,club,
    card,start}]}]."""
    buf = io.BytesIO()
    doc = _doc(buf)
    styles = _styles()
    story = [
        Paragraph(f"<b>{escape(event.get('name', ''))}</b> — Start list", styles["Title"]),
        Paragraph(escape(event.get("date", "")), styles["Normal"]),
        Spacer(1, 6 * mm),
    ]
    for cls in classes:
        story.append(Paragraph(escape(cls["name"]), styles["Heading2"]))
        data = [["Bib", "Start", "Name", "Club", "SI"]]
        for r in cls["rows"]:
            data.append([str(r.get("bib") or ""), r.get("start") or "-",
                         r["name"], r.get("club") or "", str(r.get("card") or "")])
        tbl = Table(data, colWidths=[14 * mm, 24 * mm, 52 * mm, 40 * mm, 24 * mm])
        tbl.setStyle(_table_style())
        story.append(tbl)
        story.append(Spacer(1, 7 * mm))
    doc.build(story)
    return buf.getvalue()


def bib_labels_pdf(labels: list[dict], event: dict) -> bytes:
    """A grid of bib labels (large bib number + name/class) for printing."""
    buf = io.BytesIO()
    doc = _doc(buf)
    styles = _styles()
    cell_style = styles["Normal"]
    cells = []
    for lab in labels:
        block = [
            Paragraph(f"<font size=22><b>{escape(str(lab.get('bib') or ''))}</b></font>",
                      cell_style),
            Paragraph(f"<b>{escape(lab.get('name', ''))}</b>", cell_style),
            Paragraph(escape(f"{lab.get('class', '')} · {lab.get('club', '')}"), cell_style),
        ]
        cells.append(block)
    # Lay out 3 per row.
    rows, row = [], []
    for block in cells:
        inner = Table([[p] for p in block], colWidths=[58 * mm])
        row.append(inner)
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        while len(row) < 3:
            row.append("")
        rows.append(row)
    story = [Paragraph(f"<b>{escape(event.get('name', ''))}</b> — Bib labels", styles["Title"]),
             Spacer(1, 5 * mm)]
    if rows:
        grid = Table(rows, colWidths=[60 * mm] * 3)
        grid.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, _LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(grid)
    doc.build(story)
    return buf.getvalue()


def podium_pdf(classes: list[dict], event: dict) -> bytes:
    """Prize-giving / podium list: the top three in each class.

    ``classes`` are view rows as built by ``app._console_data`` (each with
    ``name``, ``is_score`` and ranked ``rows``)."""
    buf = io.BytesIO()
    doc = _doc(buf)
    styles = _styles()
    story = [
        Paragraph(f"<b>{escape(event.get('name', ''))}</b> — Prize giving", styles["Title"]),
        Paragraph(escape(event.get("date", "")), styles["Normal"]),
        Spacer(1, 6 * mm),
    ]
    for cls in classes:
        winners = [r for r in cls["rows"] if r.get("position") in (1, 2, 3)]
        if not winners:
            continue
        story.append(Paragraph(escape(cls["name"]), styles["Heading2"]))
        is_score = cls.get("is_score")
        header = ["Place", "Athlete", "Club"] + (["Points"] if is_score else ["Time"])
        data = [header]
        for r in winners:
            line = [str(r["position"]), r["name"], r.get("club") or ""]
            line.append(("" if r.get("points") is None else str(r["points"]))
                        if is_score else (r.get("time") or "-"))
            data.append(line)
        widths = [16 * mm, 60 * mm, 44 * mm, 30 * mm]
        tbl = Table(data, colWidths=widths)
        tbl.setStyle(_table_style())
        story.append(tbl)
        story.append(Spacer(1, 7 * mm))
    if len(story) == 3:
        story.append(Paragraph("No placed competitors yet.", styles["Normal"]))
    doc.build(story)
    return buf.getvalue()


def still_out_pdf(rows: list[dict], event: dict) -> bytes:
    """Competitors who have started but not yet downloaded a finish -- the
    "still out on course" safety check. ``rows`` = [{start, name, club, class}]."""
    buf = io.BytesIO()
    doc = _doc(buf)
    styles = _styles()
    story = [
        Paragraph(f"<b>{escape(event.get('name', ''))}</b> — Still out on course", styles["Title"]),
        Paragraph(escape(event.get("date", "")), styles["Normal"]),
        Spacer(1, 4 * mm),
        Paragraph(f"{len(rows)} competitor(s) started, not yet finished.", styles["Normal"]),
        Spacer(1, 4 * mm),
    ]
    data = [["Start", "Name", "Club", "Class"]]
    for r in rows:
        data.append([r.get("start") or "-", r["name"], r.get("club") or "", r.get("class") or ""])
    tbl = Table(data, colWidths=[24 * mm, 56 * mm, 44 * mm, 36 * mm])
    tbl.setStyle(_table_style())
    story.append(tbl)
    doc.build(story)
    return buf.getvalue()


def class_results_pdf(classes: list[dict], event: dict) -> bytes:
    """Whole-event results (one block per class) as PDF bytes.

    ``classes`` are view rows as built by ``app._console_data`` (each with
    ``name``, ``is_score`` and ``rows``).
    """
    buf = io.BytesIO()
    doc = _doc(buf)
    styles = _styles()
    story = [
        Paragraph(f"<b>{escape(event.get('name', ''))}</b> — Results", styles["Title"]),
        Paragraph(escape(event.get("date", "")), styles["Normal"]),
        Spacer(1, 6 * mm),
    ]

    for cls in classes:
        story.append(Paragraph(escape(cls["name"]), styles["Heading2"]))
        is_score = cls.get("is_score")
        header = ["#", "Athlete", "Club"] + (["Points"] if is_score else []) + ["Time", "Status"]
        data = [header]
        for r in cls["rows"]:
            pos = str(r["position"]) if r.get("position") else "-"
            line = [pos, r["name"], r.get("club") or ""]
            if is_score:
                line.append("" if r.get("points") is None else str(r["points"]))
            line += [r.get("time") or "-", r.get("status_label") or "OK"]
            data.append(line)
        widths = [10 * mm, 46 * mm, 34 * mm] + ([18 * mm] if is_score else []) + [24 * mm, 22 * mm]
        tbl = Table(data, colWidths=widths)
        tbl.setStyle(_table_style())
        story.append(tbl)
        story.append(Spacer(1, 7 * mm))

    doc.build(story)
    return buf.getvalue()
