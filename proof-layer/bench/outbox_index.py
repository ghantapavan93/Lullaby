"""Optimization proof: the worker's job claim as finished jobs accumulate.

The outbox keeps finished jobs (state 'done') as history. The claim query
filters on state and orders by id, and only the primary key and household
index exist, so its cost grows with all jobs ever written, not with the jobs
waiting. This script measures that, applies one partial index, and measures
the same workload again.

Protocol (each step recorded in the result file):
1. Add `--history` finished jobs and `--pending` waiting jobs (synthetic,
   labelled 'bench-history-*' / 'bench-pending-*').
2. Baseline: the app's own claim SQL under EXPLAIN (ANALYZE, BUFFERS), median
   of `--repeat` runs, plus the job id it returns.
3. Change: CREATE INDEX ix_outbox_claimable ON outbox (id) WHERE state IN
   ('pending', 'in_progress').
4. Same workload again; the claimed job id must be identical.
5. Trade-off: time to insert and finish 10,000 jobs with and without the index.
6. Clean up the synthetic rows; the index is dropped unless --keep-index.

    python -m bench.outbox_index --history 1000000 --pending 200
"""
import argparse
import json
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg

DSN = os.environ.get("DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_bench").replace(
    "postgresql+psycopg://", "postgresql://")
OUT = Path(__file__).resolve().parents[2] / "artifacts" / "benchmarks"
INDEX = "CREATE INDEX ix_outbox_claimable ON outbox (id) WHERE state IN ('pending', 'in_progress')"
# The statement app.worker.claim() sends (captured by bench/explain.py), with its parameters inlined.
CLAIM = ("SELECT outbox.id FROM outbox WHERE (outbox.state = 'pending' OR outbox.state = 'in_progress' "
         "AND outbox.lease_until < now()) AND outbox.available_at <= now() ORDER BY outbox.id LIMIT 1 "
         "FOR UPDATE SKIP LOCKED")


def measure(conn, repeat: int) -> dict:
    runs, plan, claimed = [], None, None
    for _ in range(repeat):
        with conn.transaction(force_rollback=True):
            out = conn.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + CLAIM).fetchone()[0][0]
            runs.append(out["Execution Time"])
            plan = out
        with conn.transaction(force_rollback=True):
            claimed = conn.execute(CLAIM).fetchone()
    nodes, stack = [], [plan["Plan"]]
    while stack:
        n = stack.pop()
        nodes.append(n)
        stack.extend(n.get("Plans", []))
    return {"median_ms": round(statistics.median(runs), 3), "runs_ms": [round(r, 3) for r in runs],
            "plan": " > ".join(n["Node Type"] for n in nodes),
            "index": sorted({n["Index Name"] for n in nodes if "Index Name" in n}),
            "shared_hit_blocks": plan["Plan"].get("Shared Hit Blocks"), "shared_read_blocks": plan["Plan"].get("Shared Read Blocks"),
            "claimed_job_id": claimed[0] if claimed else None}


def write_cost(conn, n: int) -> float:
    """Insert n jobs and mark them done, inside a rolled-back transaction."""
    with conn.transaction(force_rollback=True):
        t = time.perf_counter()
        conn.execute("INSERT INTO outbox (household_id, kind, dedupe_key, payload, state) "
                     "SELECT 'hh_bench', 'notify_revision', 'bench-write-' || g, '{}'::jsonb, 'pending' "
                     "FROM generate_series(1, %s) g", (n,))
        conn.execute("UPDATE outbox SET state = 'done' WHERE dedupe_key LIKE 'bench-write-%%'")
        return round((time.perf_counter() - t) * 1000, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=int, default=1_000_000)
    ap.add_argument("--pending", type=int, default=200)
    ap.add_argument("--repeat", type=int, default=7)
    ap.add_argument("--keep-index", action="store_true")
    args = ap.parse_args()
    with psycopg.connect(DSN, autocommit=True, options="-c timezone=UTC") as conn:
        conn.execute("DROP INDEX IF EXISTS ix_outbox_claimable")
        conn.execute("DELETE FROM outbox WHERE dedupe_key LIKE 'bench-history-%' OR dedupe_key LIKE 'bench-pending-%'")
        t = time.perf_counter()
        conn.execute("INSERT INTO outbox (household_id, kind, dedupe_key, payload, state, attempts, created_at, updated_at) "
                     "SELECT 'hh_' || lpad((g %% 100000)::text, 6, '0'), 'notify_revision', 'bench-history-' || g, "
                     "'{}'::jsonb, 'done', 1, now() - interval '1 day', now() - interval '1 day' "
                     "FROM generate_series(1, %s) g", (args.history,))
        conn.execute("INSERT INTO outbox (household_id, kind, dedupe_key, payload, state) "
                     "SELECT 'hh_' || lpad((g %% 100000)::text, 6, '0'), 'notify_revision', 'bench-pending-' || g, "
                     "'{}'::jsonb, 'pending' FROM generate_series(1, %s) g", (args.pending,))
        conn.execute("VACUUM ANALYZE outbox")
        fill_s = round(time.perf_counter() - t, 1)
        rows = dict(conn.execute("SELECT state, count(*) FROM outbox GROUP BY 1").fetchall())
        size_before = conn.execute("SELECT pg_total_relation_size('outbox')").fetchone()[0]

        baseline = measure(conn, args.repeat)
        write_before = write_cost(conn, 10_000)
        t = time.perf_counter()
        conn.execute(INDEX)
        conn.execute("ANALYZE outbox")
        index_build_ms = round((time.perf_counter() - t) * 1000, 1)
        index_bytes = conn.execute("SELECT pg_relation_size('ix_outbox_claimable')").fetchone()[0]
        after = measure(conn, args.repeat)
        write_after = write_cost(conn, 10_000)

        conn.execute("DELETE FROM outbox WHERE dedupe_key LIKE 'bench-history-%' OR dedupe_key LIKE 'bench-pending-%'")
        if not args.keep_index:
            conn.execute("DROP INDEX IF EXISTS ix_outbox_claimable")
        conn.execute("VACUUM ANALYZE outbox")
        pg = conn.execute("SHOW server_version").fetchone()[0]

    same = baseline["claimed_job_id"] == after["claimed_job_id"]
    record = {
        "label": f"outbox-claim-{args.history}", "scenario": "optimization: outbox claim index", "synthetic": True,
        "workload": {"history_done_jobs": args.history, "pending_jobs": args.pending, "repeat": args.repeat,
                     "outbox_rows_by_state": rows, "outbox_bytes": size_before, "fill_seconds": fill_s, "query": CLAIM},
        "baseline": baseline, "change": INDEX, "index_build_ms": index_build_ms, "index_bytes": index_bytes,
        "after": after, "speedup": round(baseline["median_ms"] / after["median_ms"], 1) if after["median_ms"] else None,
        "identical_result": same,
        "write_cost_10k_jobs_ms": {"without_index": write_before, "with_index": write_after},
        "invariant": {"rule": "the index changes the plan, not the answer: the same job is claimed", "held": same},
        "environment": {"machine": platform.processor(), "cpu_count": os.cpu_count(), "postgres": pg,
                        "executed_at": datetime.now(timezone.utc).isoformat()},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"optimization-outbox-claim-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({k: record[k] for k in ("baseline", "after", "speedup", "identical_result",
                                             "write_cost_10k_jobs_ms", "index_bytes")}, indent=2))
    print("saved", path)


if __name__ == "__main__":
    main()
