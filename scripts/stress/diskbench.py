"""
How long a card read's save takes on this disk, for each SQLite journal mode.
Each 'read' rewrites one competitor and its punches and commits, as the app
does. Run on the machine in question: python scripts/stress/diskbench.py
"""

import os
import sqlite3
import statistics
import sys
import tempfile
import time

MODES = [("delete", "full"), ("truncate", "full"), ("persist", "full"),
         ("wal", "full"), ("wal", "normal")]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 1500


def bench(journal, sync):
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "bench.bmeos")
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA journal_mode={journal}")
    con.execute(f"PRAGMA synchronous={sync}")
    con.executescript("""
        CREATE TABLE competitors (id INTEGER PRIMARY KEY, name TEXT, card INTEGER,
                                  start TEXT, finish TEXT, read_at TEXT);
        CREATE TABLE punches (competitor_id INTEGER, code INTEGER, time TEXT, sequence INTEGER);
    """)
    con.executemany("INSERT INTO competitors VALUES (?, ?, ?, NULL, NULL, NULL)",
                    [(i, f"Runner {i}", 8000000 + i) for i in range(2000)])
    con.commit()
    times = []
    for i in range(N):
        t0 = time.perf_counter()
        cid = i % 2000
        con.execute("UPDATE competitors SET finish=?, read_at=? WHERE id=?",
                    ("10:40:00", "10:41:00", cid))
        con.execute("DELETE FROM punches WHERE competitor_id=?", (cid,))
        con.executemany("INSERT INTO punches VALUES (?, ?, ?, ?)",
                        [(cid, 31 + k, f"10:{k:02d}:00", k) for k in range(15)])
        con.commit()
        times.append(time.perf_counter() - t0)
    con.close()
    times.sort()
    return {"p50": statistics.median(times), "p95": times[int(N * .95)],
            "p99": times[int(N * .99)], "max": times[-1],
            "over_1s": sum(1 for t in times if t > 1)}


if __name__ == "__main__":
    for journal, sync in MODES:
        r = bench(journal, sync)
        print(f"{journal:>8} sync={sync:<6}  p50 {r['p50'] * 1000:7.1f} ms  p95 "
              f"{r['p95'] * 1000:7.1f} ms  p99 {r['p99'] * 1000:7.1f} ms  max "
              f"{r['max'] * 1000:7.0f} ms  over 1 s: {r['over_1s']}", flush=True)
