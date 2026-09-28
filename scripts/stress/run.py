"""
Run the stress scenarios: python scripts/stress/run.py [scenario ...]

Each scenario starts its own app and checks what it does against the rules.
Exits non-zero if anything is off, listing every problem.
"""

import importlib
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Check  # noqa: E402

SCENARIOS = ["sprint", "formats", "series"]


def main(names) -> int:
    names = names or SCENARIOS
    checks = []
    for name in names:
        print(f"== {name}", flush=True)
        check = Check(name)
        t0 = time.time()
        try:
            importlib.import_module(name).run(check)
        except Exception:  # a crash is a problem too, not the end of the run
            check(False, "scenario crashed", traceback.format_exc()[-1500:])
        print(f"   {check.passes} checks passed, {len(check.problems)} problems "
              f"({time.time() - t0:.0f}s)", flush=True)
        checks.append(check)
    problems = [(c.scenario, p) for c in checks for p in c.problems]
    print(f"\n{sum(c.passes for c in checks)} checks passed, {len(problems)} problems")
    for scenario, p in problems:
        print(f" - {scenario}: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
