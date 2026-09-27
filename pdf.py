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
    PageBreak, SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
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
        title="better-meos",
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


def start_list_by_time_pdf(rows: list[dict], event: dict) -> bytes:
    """Starter's list: every start in time order, one table with a heading per
    start minute. ``rows`` = [{start, bib, name, club, card, class}]."""
    buf = io.BytesIO()
    doc = _doc(buf)
    styles = _styles()
    story = [
        Paragraph(f"<b>{escape(event.get('name', ''))}</b> — Starters by time", styles["Title"]),
        Paragraph(escape(event.get("date", "")), styles["Normal"]),
        Spacer(1, 6 * mm),
    ]
    data = [["Start", "Bib", "Name", "Class", "Club", "SI"]]
    for r in rows:
        data.append([r["start"], str(r.get("bib") or ""), r["name"], r["class"],
                     r.get("club") or "", str(r.get("card") or "")])
    tbl = Table(data, colWidths=[20 * mm, 12 * mm, 50 * mm, 22 * mm, 42 * mm, 22 * mm],
                repeatRows=1)
    tbl.setStyle(_table_style())
    story.append(tbl)
    doc.build(story)
    return buf.getvalue()


def prize_list_pdf(classes: list[dict], event: dict, places: int) -> bytes:
    """Prize-giving list: the top ``places`` of each class, read out in reverse
    at the ceremony. ``classes`` = [{name, is_score, rows:[view rows]}]."""
    buf = io.BytesIO()
    doc = _doc(buf)
    styles = _styles()
    story = [
        Paragraph(f"<b>{escape(event.get('name', ''))}</b> — Prize giving", styles["Title"]),
        Paragraph(escape(f"{event.get('date', '')} · top {places} per class"), styles["Normal"]),
        Spacer(1, 6 * mm),
    ]
    for cls in classes:
        story.append(Paragraph(escape(cls["name"]), styles["Heading2"]))
        head = ["Place", "Name", "Club", "Points" if cls["is_score"] else "Time"]
        data = [head] + [[str(r["position"]), r["name"], r.get("club") or "",
                          str(r["points"]) if cls["is_score"] else (r.get("time") or "")]
                         for r in cls["rows"]]
        tbl = Table(data, colWidths=[16 * mm, 62 * mm, 58 * mm, 30 * mm])
        tbl.setStyle(_table_style())
        story.append(tbl)
        story.append(Spacer(1, 6 * mm))
    doc.build(story)
    return buf.getvalue()


def invoices_pdf(clubs: list[dict], event: dict, *, currency: str = "",
                 text: str = "", due_days: int = 0) -> bytes:
    """Club invoices, one page per club: each runner's fee, what's been paid
    and what's still owed, then the payment details. ``clubs`` =
    [{club, due, paid, owing, rows: [{name, class, due, paid, owing, method}]}]."""
    from datetime import date, timedelta

    buf = io.BytesIO()
    doc = _doc(buf)
    styles = _styles()
    story = []
    today = date.today()
    money = (lambda v: f"{v:,.2f}")
    for n, club in enumerate(clubs):
        if n:
            story.append(PageBreak())
        story.append(Paragraph(f"<b>Invoice</b>: {escape(club['club'])}", styles["Title"]))
        line = f"{escape(event.get('name', ''))} · {escape(event.get('date', ''))}"
        story.append(Paragraph(line, styles["Normal"]))
        dated = f"Issued {today.strftime('%d %B %Y').lstrip('0')}"
        if due_days:
            due = today + timedelta(days=due_days)
            dated += f" · due {due.strftime('%d %B %Y').lstrip('0')}"
        story.append(Paragraph(dated, styles["Normal"]))
        story.append(Spacer(1, 6 * mm))
        data = [["Runner", "Class", "Fee", "Paid", "Owing"]]
        for r in club["rows"]:
            paid = money(r["paid"]) + (f" ({r['method']})" if r["paid"] and r["method"] else "")
            data.append([r["name"], r["class"], money(r["due"]), paid, money(r["owing"])])
        data.append(["Total", "", money(club["due"]), money(club["paid"]), money(club["owing"])])
        tbl = Table(data, colWidths=[56 * mm, 26 * mm, 26 * mm, 36 * mm, 26 * mm])
        style = _table_style()
        style.add("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")
        style.add("LINEABOVE", (0, -1), (-1, -1), 0.8, _GREEN)
        style.add("ALIGN", (2, 0), (-1, -1), "RIGHT")
        tbl.setStyle(style)
        story.append(tbl)
        story.append(Spacer(1, 6 * mm))
        owing = f"<b>Amount owing: {money(club['owing'])} {escape(currency)}</b>"
        story.append(Paragraph(owing if club["owing"] else "<b>Paid in full. Thank you!</b>",
                               styles["Heading3"]))
        if text:
            story.append(Spacer(1, 3 * mm))
            for para in text.splitlines():
                story.append(Paragraph(escape(para) or "&nbsp;", styles["Normal"]))
    if not story:
        story.append(Paragraph("No entries to invoice.", styles["Normal"]))
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
