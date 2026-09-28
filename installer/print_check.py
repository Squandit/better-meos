"""
CI check that direct slip printing really works on Windows.

Prints a test slip through the same code the app uses (pywin32 GDI) to the
"Microsoft Print to PDF" printer, writing to a file instead of asking where to
save, and checks a PDF comes out. Run by .github/workflows/build-exe.yml.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import slip_printer  # noqa: E402

PDF_PRINTER = "Microsoft Print to PDF"


def main() -> int:
    found = slip_printer.printers()
    print("printers:", found)
    if PDF_PRINTER not in found["printers"]:
        print(f"{PDF_PRINTER!r} is not installed on this machine")
        return 1
    out = os.path.abspath("print-check.pdf")
    if os.path.exists(out):
        os.remove(out)
    where = slip_printer._print_windows(slip_printer.test_slip(), PDF_PRINTER, output=out)
    print("sent to", where)
    for _ in range(40):                      # the spooler writes the file after EndDoc
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            with open(out, "rb") as f:
                assert f.read(5) == b"%PDF-", "not a PDF"
            print(f"ok: {out} ({os.path.getsize(out)} bytes)")
            return 0
        time.sleep(0.5)
    print("no PDF came out")
    return 1


if __name__ == "__main__":
    sys.exit(main())
