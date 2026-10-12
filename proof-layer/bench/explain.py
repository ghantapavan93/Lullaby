"""Query plans for the app's real queries, on the benchmark dataset.

The SQL is not written by hand. Each workload below runs through the real app
(in-process) against the benchmark database while a SQLAlchemy hook records
every statement and its parameters. Each recorded SELECT is then run again
under EXPLAIN (ANALYZE, BUFFERS) and summarized: time, plan shape, indexes
used, sequential scans and buffer traffic.

    python -m bench.explain --label large

Writes artifacts/benchmarks/explain-<label>-<utc time>.json. Writes performed
by the workloads (a decision, an authorization record) land in the benchmark
database only.
"""
import argparse
import json
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_bench")
os.environ.setdefault("PROVIDER_DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_bench")
os.environ.setdefault("PROOF_LAYER_SECRET", "bench-only-secret")
os.environ["PROOF_LAYER_DEV"] = "0"

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from app import growth, worker  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import issue_token  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "artifacts" / "benchmarks"
captured: list[tuple[str, str, object]] = []
current = {"label": None}


@event.listens_for(engine, "before_cursor_execute")
def _capture(conn, cursor, statement, parameters, context, executemany):
    if current["label"] and statement.lstrip().upper().startswith("SELECT") and not executemany:
        captured.append((current["label"], statement, parameters))


def _walk(node, out):
    out.append(node)
    for child in node.get("Plans", []):
        _walk(child, out)
    return out


def summarize(plan_json) -> dict:
    top = plan_json[0]
    nodes = _walk(top["Plan"], [])
    indexes = sorted({n["Index Name"] for n in nodes if "Index Name" in n})
    seq = sorted({n["Relation Name"] for n in nodes if n["Node Type"] == "Seq Scan"})
    hit = top["Plan"].get("Shared Hit Blocks", 0)
    read = top["Plan"].get("Shared Read Blocks", 0)
    shape = " > ".join(dict.fromkeys(n["Node Type"] for n in nodes))
    return {"execution_ms": round(top["Execution Time"], 3), "planning_ms": round(top["Planning Time"], 3),
            "rows": top["Plan"].get("Actual Rows"), "indexes": indexes, "seq_scans": seq,
            "shared_hit_blocks": hit, "shared_read_blocks": read,
            "summary": f"{shape}; " + (f"index {', '.join(indexes)}" if indexes else "no index")
                       + (f"; seq scan {', '.join(seq)}" if seq else "")}


def workloads(client: TestClient):
    with psycopg.connect(os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")) as conn:
        hh, window = conn.execute(
            "SELECT household_id, id FROM briefing_windows WHERE household_id > 'hh_000100' "
            "AND local_date = (SELECT max(local_date) FROM briefing_windows) ORDER BY id LIMIT 1").fetchone()
        heavy = conn.execute("SELECT id FROM briefing_windows WHERE household_id = 'hh_000000' "
                             "ORDER BY local_date DESC LIMIT 1").fetchone()
    owner = {"Authorization": f"Bearer {issue_token('m_' + hh[3:] + '_a')}"}
    care = {"Authorization": f"Bearer {issue_token('m_' + hh[3:] + '_b')}"}
    yield "current briefing", lambda: client.get(f"/v1/windows/{window}/brief", headers=care)
    yield "historical revision", lambda: client.get(f"/v1/windows/{window}/brief?revision=1", headers=care)
    yield "revision history", lambda: client.get(f"/v1/windows/{window}/revisions", headers=owner)
    yield "caregiver changes", lambda: client.get(f"/v1/windows/{window}/changes", headers=owner)
    yield "week view", lambda: client.get(f"/v1/windows/{window}/week", headers=care)
    if heavy:
        heavy_owner = {"Authorization": f"Bearer {issue_token('m_000000_b')}"}
        yield "week view, long history", lambda: client.get(f"/v1/windows/{heavy[0]}/week", headers=heavy_owner)
    yield "eligibility decision + assignment", lambda: client.post(
        "/v1/capability-decisions", headers=owner, json={"feature": "developmental_milestones"})
    yield "camera plans", lambda: client.get("/v1/household/commercial", headers=owner)
    yield "purchase intent list", lambda: client.get("/v1/purchase-intents", headers=owner)

    def claim_job():
        with SessionLocal() as db:
            worker.claim(db, "explain")
            db.rollback()
    yield "outbox job claim", claim_job

    def reconcile_scan():
        with SessionLocal() as db:
            from sqlalchemy import select
            from app.models import PurchaseIntent
            db.execute(select(PurchaseIntent.id).where(PurchaseIntent.state.in_(("SUBMITTED", "UNKNOWN")))).all()
    yield "reconciliation scan", reconcile_scan

    def experiment_health():
        with SessionLocal() as db:
            growth.experiment_health(db, "availability_explanation")
    yield "experiment health aggregation", experiment_health


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="bench")
    args = ap.parse_args()
    with TestClient(app) as client:
        for label, call in workloads(client):
            current["label"] = label
            call()
            current["label"] = None
    plans: dict[str, dict] = {}
    seen = set()
    with psycopg.connect(os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://"),
                         options="-c timezone=UTC") as conn:
        counts = dict(conn.execute(
            "SELECT relname, GREATEST(reltuples, 0)::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND relkind = 'r'").fetchall())
        n = 0
        for label, sql, params in captured:
            key = (label, re.sub(r"\s+", " ", sql))
            if key in seen or " FROM authorization_log" in sql:
                continue
            seen.add(key)
            n += 1
            with conn.transaction(force_rollback=True):
                result = conn.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).fetchone()[0]
            s = summarize(result)
            s["sql"] = re.sub(r"\s+", " ", sql)[:600]
            s["seq_scan_on_large_table"] = [t for t in s["seq_scans"] if counts.get(t, 0) > 10_000]
            plans[f"{label} #{n}"] = s
        pg = conn.execute("SHOW server_version").fetchone()[0]
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    record = {"label": args.label, "scenario": "explain", "explain": plans,
              "environment": {"machine": platform.processor(), "cpu_count": os.cpu_count(), "postgres": pg,
                              "database_mb": round(size / 2**20, 1), "table_rows": counts,
                              "executed_at": datetime.now(timezone.utc).isoformat()}}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"explain-{args.label}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    worst = sorted(plans.items(), key=lambda kv: -kv[1]["execution_ms"])[:8]
    for k, v in worst:
        print(f"{v['execution_ms']:>9.3f} ms  {k}: {v['summary']}")
    flagged = {k: v["seq_scan_on_large_table"] for k, v in plans.items() if v["seq_scan_on_large_table"]}
    print("seq scans on large tables:", flagged or "none")
    print("saved", path)


if __name__ == "__main__":
    main()
