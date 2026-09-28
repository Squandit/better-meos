"""
Split slips printed by the app itself, straight to a printer, with no dialog.

A browser can't print without showing its print dialog (unless it was started
in kiosk mode), so on Windows the server prints slips itself: it lays the slip
out, draws it as an image at the printer's own resolution and hands it to the
Windows print spooler (pywin32 GDI). That works whichever browser, tablet or
phone the operator uses, and even with no page open at all.

Pieces:

* :func:`slip_lines` -- the slip as a list of lines (plain data, no drawing).
* :func:`render` -- those lines as a black-and-white Pillow image, any width
  and resolution. Also used for the preview and the tests.
* :func:`send` -- one slip to the printer, on a background worker so a slow or
  jammed printer never holds up reading the next card.
* :func:`after_read` -- the auto-print rule ("Every card read", "Only OK runs"
  ...), called by the SI reader for each card it reads.

Off Windows, direct printing is unavailable (the browser prints instead).
With ``BMEOS_PRINT_DIR`` set, on any system, slips are saved there as PNG
files instead of printed: that's how the tests and the full-event simulation
check what would print.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from collections import deque
from datetime import datetime

import config

log = logging.getLogger(__name__)

# Paper widths: how wide the slip is drawn (the printable part of an 80 mm
# roll is about 72 mm; on A4 the slip sits in the top-left corner).
SLIP_WIDTH_MM = {"80mm": 72, "a4": 120}

# Font sizes in points, and line spacing as a multiple of the size.
SIZES = {"small": 8, "normal": 9, "big": 13, "title": 11}
LINE_SPACING = 1.3


class PrintError(Exception):
    """A slip couldn't be printed (no printer, printer offline, ...)."""


# ---------------------------------------------------------------------------
# Layout: a slip as plain lines
# ---------------------------------------------------------------------------

def _line(*cells, size="normal", bold=False, gap=0.0):
    """One line of text. ``cells`` are ``(x, align, text)`` with x a fraction
    of the width and align "l", "r" or "c"; ``gap`` adds space above (in lines)."""
    return {"cells": [list(c) for c in cells if c[2] not in (None, "")],
            "size": size, "bold": bold, "gap": gap}


def _rule(gap=0.3):
    return {"rule": True, "gap": gap}


def _pair(label_a, value_a, label_b="", value_b="", *, bold=False):
    return _line((0, "l", label_a), (0.2, "l", value_a),
                 (0.52, "l", label_b), (0.72, "l", value_b), bold=bold)


def slip_lines(comp_id: int) -> dict | None:
    """
    The split slip for one competitor, as ``{"title", "status", "lines"}``, or
    None if there's no such competitor. Same content as the on-screen slip
    (``templates/slip.html``), laid out for a receipt printer.
    """
    import display
    import store

    view = display.slip_view(comp_id)
    if view is None:
        return None
    row = view["row"]
    lines = [_line((0, "l", store.EVENT.get("name") or "Event"), size="title", bold=True),
             _line((0, "l", store.EVENT.get("date") or ""), size="small"),
             _rule(),
             _line((0, "l", row["name"]), size="big", bold=True, gap=0.2)]
    who = " · ".join(str(x) for x in (row.get("class"), row.get("club"),
                                      f"SI {row['si']}" if row.get("si") else None) if x)
    lines.append(_line((0, "l", who)))
    lines.append(_pair("Start", row.get("start") or "–", "Finish", row.get("finish") or "–")
                 | {"gap": 0.4})
    status = "OK" if row.get("is_ok") else (row.get("status_label") or "").upper()
    lines.append(_pair("Time", row.get("time") or "–", "Status", status, bold=True))
    if view.get("show_place") and view.get("place"):
        lines.append(_pair("Place", f"{view['place']} of {view['of']}",
                           "Behind" if view.get("behind") else "", view.get("behind") or ""))
    if row.get("points") is not None:
        lines.append(_pair("Points", str(row["points"])))
    if row.get("missed_control"):
        lines.append(_line((0, "l", f"Mispunch: missed control {row['missed_control']}"),
                           bold=True, gap=0.3))

    legs = view.get("legs")
    places = view.get("leg_places")
    if legs:
        lines.append(_rule(gap=0.4))
        head = [(0, "l", "#"), (0.1, "l", "Control"), (0.55, "r", "Leg")]
        if places:
            head.append((0.72, "r", "Place"))
        head.append((1, "r", "Time"))
        lines.append(_line(*head, size="small", bold=True))
        for leg in legs:
            if leg.get("missing"):
                lines.append(_line((0, "l", leg["n"]), (0.1, "l", leg["code"]),
                                   (1, "r", "missing"), bold=True))
                continue
            cells = [(0, "l", leg["n"]), (0.1, "l", leg["code"]),
                     (0.55, "r", leg.get("leg") or "")]
            if places:
                cells.append((0.72, "r", leg.get("leg_rank") or ""))
            cells.append((1, "r", leg.get("cum") or ""))
            lines.append(_line(*cells, bold=bool(leg.get("best_leg"))))
    elif row.get("splits"):
        lines.append(_rule(gap=0.4))
        lines.append(_line((0, "l", "Control"), (0.6, "r", "Leg"), (1, "r", "Cumulative"),
                           size="small", bold=True))
        for s in row["splits"]:
            lines.append(_line((0, "l", s.get("control")), (0.6, "r", s.get("leg") or ""),
                               (1, "r", s.get("cumulative") or "")))

    lines.append(_rule(gap=0.4))
    lines.append(_line((0.5, "c", view.get("footer") or "Printed by better-meos"), size="small"))
    for line in lines:            # every cell as text (leg numbers are ints)
        for cell in line.get("cells", []):
            cell[2] = str(cell[2])
    return {"title": f"Splits {row['name']}", "status": row.get("status"),
            "competitor_id": comp_id, "lines": lines}


def test_slip() -> dict:
    """A sample slip for the "Print a test slip" button."""
    now = datetime.now().strftime("%H:%M:%S")
    lines = [_line((0, "l", "better-meos test print"), size="title", bold=True),
             _line((0, "l", f"Printed at {now}"), size="small"), _rule(),
             _line((0, "l", "If you can read this, split slips"), gap=0.2),
             _line((0, "l", "will print here with no dialog.")),
             _rule(gap=0.4),
             _line((0, "l", "#"), (0.1, "l", "Control"), (0.55, "r", "Leg"),
                   (0.72, "r", "Place"), (1, "r", "Time"), size="small", bold=True),
             _line((0, "l", "1"), (0.1, "l", "31"), (0.55, "r", "2:04"),
                   (0.72, "r", "3"), (1, "r", "2:04")),
             _line((0, "l", "2"), (0.1, "l", "32"), (0.55, "r", "1:47"),
                   (0.72, "r", "1"), (1, "r", "3:51"), bold=True)]
    return {"title": "better-meos test print", "status": "ok", "competitor_id": None,
            "lines": lines}


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

_FONT_FILES = {
    False: ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"),
    True: ("arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"),
}
_FONT_DIRS = (os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
              "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation",
              "/Library/Fonts", "/System/Library/Fonts/Supplemental")
_font_cache: dict = {}


def _font(px: int, bold: bool):
    from PIL import ImageFont
    key = (px, bold)
    if key not in _font_cache:
        font = None
        for name in _FONT_FILES[bold]:
            for folder in _FONT_DIRS:
                path = os.path.join(folder, name)
                if os.path.exists(path):
                    font = ImageFont.truetype(path, px)
                    break
            if font:
                break
        _font_cache[key] = font or ImageFont.load_default(size=px)
    return _font_cache[key]


def render(slip: dict, *, width_px: int, dpi: int):
    """Draw a slip as a black-and-white image ``width_px`` wide at ``dpi``."""
    from PIL import Image, ImageDraw

    def px(points):
        return max(1, round(points * dpi / 72))

    margin = px(2)
    inner = width_px - 2 * margin
    heights = []
    for line in slip["lines"]:
        size = px(SIZES["normal"] if line.get("rule") else SIZES[line["size"]])
        gap = round(line.get("gap", 0) * size)
        heights.append((gap, size, px(3) if line.get("rule") else round(size * LINE_SPACING)))
    height = margin * 2 + sum(g + h for g, _, h in heights) + px(10)   # room before the cut

    img = Image.new("L", (width_px, height), 255)
    draw = ImageDraw.Draw(img)
    y = margin
    for line, (gap, size, h) in zip(slip["lines"], heights):
        y += gap
        if line.get("rule"):
            draw.line((margin, y + h // 2, width_px - margin, y + h // 2), fill=0,
                      width=max(1, px(0.6)))
        else:
            font = _font(size, line.get("bold", False))
            for x, align, text in line["cells"]:
                left = margin + x * inner
                w = draw.textlength(text, font=font)
                if align == "r":
                    left -= w
                elif align == "c":
                    left -= w / 2
                draw.text((max(margin, left), y), text, fill=0, font=font)
        y += h
    # Pure black and white: receipt printers dither grey, which blurs text.
    return img.point(lambda v: 0 if v < 150 else 255)


def width_px(dpi: int, printable_px: int | None = None) -> int:
    """How many pixels wide to draw the slip for this printer."""
    paper = (config.get_str("slip_paper") or "80mm").lower()
    want = round(SLIP_WIDTH_MM.get(paper, 72) / 25.4 * dpi)
    return min(want, printable_px) if printable_px else want


# ---------------------------------------------------------------------------
# Printers
# ---------------------------------------------------------------------------

def _win32():
    """The pywin32 print modules, or None off Windows / without pywin32 / when
    slips go to a PNG folder instead (``BMEOS_PRINT_DIR``)."""
    if sys.platform != "win32" or _print_dir():
        return None
    try:
        import win32print
        import win32ui
    except ImportError:
        return None
    return win32print, win32ui


def _print_dir() -> str | None:
    return os.environ.get("BMEOS_PRINT_DIR") or None


def available() -> bool:
    """Can this computer print slips directly?"""
    return _win32() is not None or _print_dir() is not None


def direct() -> bool:
    """Should slips go straight to the printer (rather than through a browser)?"""
    return config.get_str("print_method") != "browser" and available()


def printers() -> dict:
    """``{"printers": [names], "default": name}`` for the printer picker."""
    mods = _win32()
    if mods is None:
        folder = _print_dir()
        return {"printers": [f"Save as PNG in {folder}"] if folder else [], "default": None,
                "available": bool(folder)}
    win32print, _ = mods
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    names = sorted({p[2] for p in win32print.EnumPrinters(flags, None, 1)}, key=str.lower)
    try:
        default = win32print.GetDefaultPrinter()
    except Exception:  # no default printer set
        default = None
    return {"printers": names, "default": default, "available": True}


def _printer_name() -> str | None:
    return config.get_str("slip_printer") or None


def _print_windows(slip: dict, printer: str | None, output: str | None = None) -> str:
    win32print, win32ui = _win32()
    from PIL import ImageWin
    if not printer:
        try:
            printer = win32print.GetDefaultPrinter()
        except Exception:
            printer = None
        if not printer:
            raise PrintError("No printer: pick one in Settings (Readout & printing) "
                             "or set a Windows default printer")
    dc = win32ui.CreateDC()
    try:
        dc.CreatePrinterDC(printer)
    except Exception as err:
        raise PrintError(f"Can't open printer {printer!r}: {err}") from err
    try:
        page_w, page_h = dc.GetDeviceCaps(8), dc.GetDeviceCaps(10)       # HORZRES, VERTRES
        dpi_x, dpi_y = dc.GetDeviceCaps(88), dc.GetDeviceCaps(90)        # LOGPIXELSX/Y
        img = render(slip, width_px=width_px(dpi_x, page_w), dpi=dpi_x)
        # Drawn at the printer's X resolution; stretch rows if Y differs.
        rows_per_page = max(1, round(page_h * dpi_x / dpi_y))
        if output:
            dc.StartDoc(slip["title"], output)
        else:
            dc.StartDoc(slip["title"])
        for top in range(0, img.height, rows_per_page):
            part = img.crop((0, top, img.width, min(img.height, top + rows_per_page)))
            dc.StartPage()
            ImageWin.Dib(part).draw(dc.GetHandleOutput(),
                                    (0, 0, part.width, round(part.height * dpi_y / dpi_x)))
            dc.EndPage()
        dc.EndDoc()
    except PrintError:
        raise
    except Exception as err:
        raise PrintError(f"Printer {printer!r}: {err}") from err
    finally:
        dc.DeleteDC()
    return printer


def _save_png(slip: dict) -> str:
    folder = _print_dir()
    os.makedirs(folder, exist_ok=True)
    img = render(slip, width_px=width_px(203), dpi=203)     # a typical receipt printer
    stamp = datetime.now().strftime("%H%M%S%f")
    path = os.path.join(folder, f"slip-{stamp}-{slip.get('competitor_id') or 'test'}.png")
    img.save(path)
    return path


def print_now(slip: dict, *, output: str | None = None) -> str:
    """Print one slip and wait for it to reach the spooler. Returns where it went."""
    if _win32() is not None:
        return _print_windows(slip, _printer_name(), output)
    if _print_dir():
        return _save_png(slip)
    raise PrintError("Printing straight to a printer needs the Windows app")


# ---------------------------------------------------------------------------
# The print queue
# ---------------------------------------------------------------------------

_queue: queue.Queue = queue.Queue()
_jobs: deque = deque(maxlen=40)            # newest last, for the status line
_jobs_lock = threading.Lock()
_worker: threading.Thread | None = None
_seq = 0


def send(slip: dict, *, reason: str = "manual") -> dict:
    """Queue a slip for printing; returns the job (its status updates later)."""
    global _worker, _seq
    with _jobs_lock:
        _seq += 1
        job = {"id": _seq, "title": slip["title"], "competitor_id": slip.get("competitor_id"),
               "reason": reason, "state": "queued", "error": None, "printer": None,
               "time": datetime.now().strftime("%H:%M:%S")}
        _jobs.append(job)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="slip-printer", daemon=True)
            _worker.start()
    _queue.put((job, slip))
    return dict(job)


def _work() -> None:
    while True:
        job, slip = _queue.get()
        try:
            where = print_now(slip)
        except Exception as err:  # never let one slip stop the queue
            log.error("slip not printed (%s): %s", slip["title"], err)
            _update(job, state="failed", error=str(err))
        else:
            _update(job, state="printed", printer=where)
        finally:
            _queue.task_done()


def _update(job: dict, **fields) -> None:
    with _jobs_lock:
        job.update(fields)


def wait_idle(timeout: float = 10.0) -> bool:
    """Block until the queue is empty (tests and the simulation use this)."""
    done = threading.Event()
    threading.Thread(target=lambda: (_queue.join(), done.set()), daemon=True).start()
    return done.wait(timeout)


def jobs() -> list[dict]:
    """Recent print jobs, newest first."""
    with _jobs_lock:
        return [dict(j) for j in reversed(_jobs)]


def status() -> dict:
    """What the Download page's printer line shows."""
    recent = jobs()
    return {"direct": direct(), "available": available(),
            "printer": (_printer_name() or printers().get("default")) if _win32()
                       else ("PNG files" if _print_dir() else None),
            "last": recent[0] if recent else None,
            "failed": [j for j in recent if j["state"] == "failed"][:3]}


# ---------------------------------------------------------------------------
# Auto-print
# ---------------------------------------------------------------------------

def wanted(status: str | None, rule: str | None = None) -> bool:
    """Does the auto-print rule want a slip for a run with this status?"""
    rule = rule if rule is not None else (config.get_str("auto_print") or "off")
    if rule == "all":
        return True
    if rule == "ok":
        return status == "ok"
    if rule == "not_ok":
        return status is not None and status != "ok"
    return False


def maybe_print(slip: dict | None) -> dict | None:
    """Print a slip if printing is direct and the auto-print rule wants it."""
    if slip is None or not direct() or not wanted(slip.get("status")):
        return None
    return send(slip, reason="auto")


def after_read(comp_id: int) -> dict | None:
    """A card was read here (or a kept read was given to a runner): auto-print."""
    if not direct() or (config.get_str("auto_print") or "off") == "off":
        return None
    try:
        return maybe_print(slip_lines(comp_id))
    except Exception as err:  # the read itself must never fail over a slip
        log.error("auto-print for competitor %s failed: %s", comp_id, err)
        return None
