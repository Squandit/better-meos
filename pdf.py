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
