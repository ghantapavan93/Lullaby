"""Bounded load runs against an isolated server on the benchmark database.

    python -m bench.load --base http://127.0.0.1:8720 --scenario read_burst --concurrency 32 --requests 4000

Start the server first with bench/serve.py (same secret, bench databases).
Scenarios:
  read_burst          morning briefing reads across random households
  ack_burst           caregivers acknowledge the revision they read
  correction_storm    N concurrent corrections of ONE household's window (contention)
  duplicate_purchase  N concurrent purchases with ONE idempotency key
  mixed               90% briefing reads, 10% acknowledgments
  household_contention  both caregivers of ONE household read, acknowledge and correct at once
  purchase_backlog    many households buy while no worker runs; then N worker processes drain the queue
  reconnect_storm     every client reconnects at once after an outage and retries immediately on failure
  large_history       week views of a household with years of nights vs ordinary households
  soak                read/ack mix for a fixed time; latency, connections and server memory per interval

Every run records the environment and the measured numbers, and checks the
domain invariant the scenario stresses. HTTP 200 alone is never "success".
Results: artifacts/benchmarks/<scenario>-<utc time>.json
"""
import argparse
import asyncio
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("PROOF_LAYER_SECRET", "bench-only-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_bench")

import httpx  # noqa: E402
import psycopg  # noqa: E402

from app.security import issue_token  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "artifacts" / "benchmarks"
FACTS = ["recorded_sleep", "sleep_onset", "final_wake", "observation_coverage", "unobserved"]


def db():
    return psycopg.connect(os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://"),
                           options="-c timezone=UTC")


def sample_windows(n: int, seed: int):
    with db() as conn:
        rows = conn.execute(
            "SELECT w.id, w.household_id, w.current_revision FROM briefing_windows w "
            "WHERE w.local_date = (SELECT max(local_date) FROM briefing_windows) ORDER BY w.id").fetchall()
    rng = random.Random(seed)
    return rng.sample(rows, min(n, len(rows)))


def environment(base: str) -> dict:
    with db() as conn:
        pg = conn.execute("SHOW server_version").fetchone()[0]
        counts = dict(conn.execute(
            "SELECT 'households', count(*) FROM households UNION ALL SELECT 'evidence_events', count(*) "
            "FROM evidence_events UNION ALL SELECT 'briefing_revisions', count(*) FROM briefing_revisions").fetchall())
    return {"machine": platform.processor() or platform.machine(), "cpu_count": os.cpu_count(),
            "os": platform.platform(), "python": platform.python_version(), "postgres": pg, "server": base,
            "server_workers": int(os.environ.get("BENCH_SERVER_WORKERS", "1")), "dataset": counts, "executed_at": datetime.now(timezone.utc).isoformat()}


def summarize(latencies: list[float], statuses: list[int], wall: float) -> dict:
    lat = sorted(latencies)

    def pct(p):
        return round(lat[min(len(lat) - 1, int(p * len(lat)))] * 1000, 1) if lat else None
    codes: dict[str, int] = {}
    for s in statuses:
        codes[str(s)] = codes.get(str(s), 0) + 1
    return {"requests": len(lat), "wall_seconds": round(wall, 2), "throughput_rps": round(len(lat) / wall, 1),
            "p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99), "max_ms": pct(1.0),
            "mean_ms": round(statistics.fmean(lat) * 1000, 1) if lat else None, "status_codes": codes,
            "error_rate": round(sum(1 for s in statuses if s >= 500) / max(1, len(statuses)), 4),
            "client_timeouts": sum(1 for s in statuses if s == 599)}


async def drive(calls, concurrency: int):
    sem = asyncio.Semaphore(concurrency)
    latencies, statuses = [], []

    async def one(call):
        async with sem:
            t = time.perf_counter()
            try:
                status = await call()
            except httpx.HTTPError:
                status = 599          # no response within the client timeout: counted, never hidden
            latencies.append(time.perf_counter() - t)
            statuses.append(status)

    t0 = time.perf_counter()
    await asyncio.gather(*(one(c) for c in calls))
    return latencies, statuses, time.perf_counter() - t0


def pg_stats() -> dict:
    """Connections to the bench database, lock waits and the longest-running
    statement right now."""
    with db() as conn:
        row = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE wait_event_type = 'Lock'), "
            "count(*) FILTER (WHERE state = 'idle in transaction'), "
            "coalesce(max(extract(epoch FROM now() - query_start)) FILTER (WHERE state = 'active'), 0), "
            "coalesce(max(extract(epoch FROM now() - xact_start)) FILTER (WHERE state = 'idle in transaction'), 0) "
            "FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()").fetchone()
    return {"connections": row[0], "waiting_on_locks": row[1], "idle_in_transaction": row[2],
            "longest_active_query_s": round(float(row[3]), 2), "longest_idle_in_transaction_s": round(float(row[4]), 2)}


def server_memory_mb(port: str) -> float | None:
    """Working set of the uvicorn server listening on `port` and its worker
    processes (Windows, via PowerShell CIM). None if it can't be read."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Select-Object ProcessId,ParentProcessId,CommandLine,WorkingSetSize | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30).stdout
        rows = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    rows = rows if isinstance(rows, list) else [rows]
    roots = {r["ProcessId"] for r in rows if "app.main:app" in (r.get("CommandLine") or "")
             and f"--port {port}" in (r.get("CommandLine") or "")}
    tree, grew = set(roots), True
    while grew:
        children = {r["ProcessId"] for r in rows if r["ParentProcessId"] in tree} - tree
        grew = bool(children)
        tree |= children
    total = sum(int(r.get("WorkingSetSize") or 0) for r in rows if r["ProcessId"] in tree)
    return round(total / 2**20, 1) if tree else None


def owner(hh: str) -> dict:
    return {"Authorization": f"Bearer {issue_token('m_' + hh[3:] + '_a')}"}


def caregiver(hh: str) -> dict:
    return {"Authorization": f"Bearer {issue_token('m_' + hh[3:] + '_b')}"}


async def run(args) -> dict:
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(base_url=args.base, timeout=60, limits=limits) as client:
        result: dict = {"scenario": args.scenario, "concurrency": args.concurrency, "seed": args.seed}

        if args.scenario in ("read_burst", "ack_burst", "mixed"):
            windows = sample_windows(2000, args.seed)
            rng = random.Random(args.seed)
            revision_ids: dict[str, str] = {}

            async def read(w):
                r = await client.get(f"/v1/windows/{w[0]}/brief", headers=caregiver(w[1]))
                if r.status_code == 200:
                    revision_ids[w[0]] = r.json()["revision_id"]
                return r.status_code

            async def ack(w):
                rid = revision_ids.get(w[0])
                if rid is None:
                    rid = (await client.get(f"/v1/windows/{w[0]}/brief", headers=caregiver(w[1]))).json()["revision_id"]
                    revision_ids[w[0]] = rid
                r = await client.post(f"/v1/windows/{w[0]}/acknowledgments", headers=caregiver(w[1]),
                                      json={"revision_id": rid, "displayed_facts": FACTS})
                return r.status_code

            picks = [rng.choice(windows) for _ in range(args.requests)]
            if args.scenario == "read_burst":
                calls = [lambda w=w: read(w) for w in picks]
            elif args.scenario == "ack_burst":
                calls = [lambda w=w: ack(w) for w in picks]
            else:
                calls = [(lambda w=w: ack(w)) if rng.random() < 0.1 else (lambda w=w: read(w)) for w in picks]
            lat, st, wall = await drive(calls, args.concurrency)
            result["measured"] = summarize(lat, st, wall)
            if args.scenario != "read_burst":
                with db() as conn:
                    dupes = conn.execute("SELECT count(*) FROM (SELECT member_id, revision_id FROM acknowledgments "
                                         "GROUP BY 1, 2 HAVING count(*) > 1) d").fetchone()[0]
                result["invariant"] = {"rule": "one acknowledgment per member and revision",
                                       "duplicate_pairs": dupes, "held": dupes == 0}

        elif args.scenario == "correction_storm":
            with db() as conn:
                w = conn.execute(
                    "SELECT w.id, w.household_id, w.current_revision, e.id FROM briefing_windows w "
                    "JOIN evidence_events e ON e.household_id = w.household_id AND e.child_id = w.child_id "
                    "AND e.kind = 'sleep_interval' AND e.started_at >= w.starts_at AND e.ended_at <= w.ends_at "
                    "WHERE NOT EXISTS (SELECT 1 FROM evidence_events c WHERE c.supersedes_event_id = e.id) "
                    "AND w.local_date = (SELECT max(local_date) FROM briefing_windows) "
                    "ORDER BY w.id, e.ended_at DESC LIMIT 1").fetchone()
                before = conn.execute("SELECT count(*) FROM briefing_revisions WHERE window_id = %s", (w[0],)).fetchone()[0]

            # corrected_end must differ from the original; fetch it to shorten by one minute.
            with db() as conn:
                end = conn.execute("SELECT ended_at FROM evidence_events WHERE id = %s", (w[3],)).fetchone()[0]

            async def correct_end(i):
                r = await client.post(f"/v1/windows/{w[0]}/corrections", headers=owner(w[1]), json={
                    "target_event_id": w[3], "expected_revision": w[2],
                    "client_event_id": f"bench-storm-{args.seed}-{i:04d}",
                    "corrected_end": (end - timedelta(minutes=1 + i % 10)).isoformat()})
                return r.status_code

            lat, st, wall = await drive([lambda i=i: correct_end(i) for i in range(args.requests)], args.concurrency)
            with db() as conn:
                after = conn.execute("SELECT count(*) FROM briefing_revisions WHERE window_id = %s", (w[0],)).fetchone()[0]
                corrections = conn.execute("SELECT count(*) FROM evidence_events WHERE supersedes_event_id = %s",
                                           (w[3],)).fetchone()[0]
            result["measured"] = summarize(lat, st, wall)
            result["invariant"] = {"rule": "exactly one correction wins; the rest get REVISION_CONFLICT or ALREADY_CORRECTED",
                                   "new_revisions": after - before, "corrections_stored": corrections,
                                   "held": after - before == 1 and corrections == 1 and st.count(200) == 1}

        elif args.scenario == "duplicate_purchase":
            with db() as conn:
                hh = conn.execute(
                    "SELECT h.id FROM households h JOIN entitlement_claims s ON s.household_id = h.id "
                    "JOIN devices d ON d.household_id = h.id WHERE s.plan_code = 'sleep' "
                    "AND d.mount IN ('floor_stand','wall_mount') AND NOT EXISTS (SELECT 1 FROM purchase_intents p "
                    "WHERE p.household_id = h.id) ORDER BY h.id LIMIT 1").fetchone()[0]
            d = (await client.post("/v1/capability-decisions", headers=owner(hh),
                                   json={"feature": "developmental_milestones"})).json()
            key = f"bench-dup-{args.seed}-{hh}"

            async def buy():
                r = await client.post("/v1/purchase-intents", headers=owner(hh) | {"Idempotency-Key": key},
                                      json={"decision_id": d["decision_id"], "offer_id": d["offer"]["offer_id"]})
                return r.status_code

            lat, st, wall = await drive([buy for _ in range(args.requests)], args.concurrency)
            with db() as conn:
                intents = conn.execute("SELECT count(*) FROM purchase_intents WHERE household_id = %s", (hh,)).fetchone()[0]
                jobs = conn.execute("SELECT count(*) FROM outbox WHERE household_id = %s AND kind = 'submit_purchase'",
                                    (hh,)).fetchone()[0]
            result["measured"] = summarize(lat, st, wall)
            result["invariant"] = {"rule": "one idempotency key -> one intent and one submit job",
                                   "intents": intents, "submit_jobs": jobs,
                                   "held": intents == 1 and jobs == 1 and st.count(201) == 1}

        elif args.scenario == "household_contention":
            # Both caregivers of one household, at once: reads, acknowledgments of
            # whatever revision each last saw, and the owner's corrections from a
            # possibly stale screen. Expected: corrections either win or get a
            # conflict; revisions stay gap-free; nobody's ack head runs ahead.
            with db() as conn:
                w = conn.execute(
                    "SELECT w.id, w.household_id, w.current_revision, e.id, e.ended_at FROM briefing_windows w "
                    "JOIN evidence_events e ON e.household_id = w.household_id AND e.child_id = w.child_id "
                    "AND e.kind = 'sleep_interval' AND e.started_at >= w.starts_at AND e.ended_at <= w.ends_at "
                    "WHERE NOT EXISTS (SELECT 1 FROM evidence_events c WHERE c.supersedes_event_id = e.id) "
                    "AND w.local_date = (SELECT max(local_date) FROM briefing_windows) AND w.household_id > 'hh_000100' "
                    "ORDER BY w.id, e.ended_at DESC LIMIT 1").fetchone()
                start_revision = w[2]
            seen = {"revision": w[2], "revision_id": None, "target": w[3], "end": w[4]}
            outcome = {"corrections_won": 0, "correction_conflicts": 0, "acks": 0}
            rng = random.Random(args.seed)

            async def read(headers):
                r = await client.get(f"/v1/windows/{w[0]}/brief", headers=headers)
                if r.status_code == 200:
                    body = r.json()
                    if body["revision"] >= seen["revision"]:
                        seen["revision"], seen["revision_id"] = body["revision"], body["revision_id"]
                return r.status_code

            async def acknowledge(headers):
                if seen["revision_id"] is None:
                    return await read(headers)
                r = await client.post(f"/v1/windows/{w[0]}/acknowledgments", headers=headers,
                                      json={"revision_id": seen["revision_id"], "displayed_facts": FACTS})
                outcome["acks"] += r.status_code == 200
                return r.status_code

            async def correct(i):
                expected, target = seen["revision"], seen["target"]
                r = await client.post(f"/v1/windows/{w[0]}/corrections", headers=owner(w[1]), json={
                    "target_event_id": target, "expected_revision": expected,
                    "client_event_id": f"bench-contention-{args.seed}-{i:05d}",
                    "corrected_end": (seen["end"] - timedelta(minutes=1 + i % 30)).isoformat()})
                if r.status_code == 200:
                    outcome["corrections_won"] += 1
                    body = r.json()
                    seen["target"] = body.get("correction_event_id", target)
                    if body["revision"] > seen["revision"]:
                        seen["revision"], seen["revision_id"] = body["revision"], body["revision_id"]
                elif r.status_code == 409:
                    outcome["correction_conflicts"] += 1
                return r.status_code

            def pick(i):
                roll = rng.random()
                if roll < 0.5:
                    return lambda: read(caregiver(w[1]) if i % 2 else owner(w[1]))
                if roll < 0.8:
                    return lambda: acknowledge(caregiver(w[1]))
                return lambda: correct(i)

            lat, st, wall = await drive([pick(i) for i in range(args.requests)], args.concurrency)
            with db() as conn:
                numbers = [r[0] for r in conn.execute(
                    "SELECT revision FROM briefing_revisions WHERE window_id = %s ORDER BY revision", (w[0],))]
                current = conn.execute("SELECT current_revision FROM briefing_windows WHERE id = %s", (w[0],)).fetchone()[0]
                heads_ahead = conn.execute("SELECT count(*) FROM ack_heads WHERE window_id = %s AND revision > %s",
                                           (w[0], current)).fetchone()[0]
                head_mismatch = conn.execute(
                    "SELECT count(*) FROM ack_heads h WHERE h.window_id = %s AND h.revision <> "
                    "(SELECT max(a.revision) FROM acknowledgments a WHERE a.member_id = h.member_id "
                    "AND a.window_id = h.window_id)", (w[0],)).fetchone()[0]
            result["measured"] = summarize(lat, st, wall) | {"outcomes": outcome, "pg_after": pg_stats()}
            gap_free = numbers == list(range(numbers[0], numbers[-1] + 1)) and numbers[-1] == current
            result["invariant"] = {
                "rule": "each winning correction adds exactly one revision; revisions are gap-free; "
                        "ack heads equal each member's highest acknowledgment and never pass the current revision",
                "revisions_added": current - start_revision, "corrections_won": outcome["corrections_won"],
                "gap_free": gap_free, "heads_ahead_of_current": heads_ahead, "head_mismatches": head_mismatch,
                "held": gap_free and current - start_revision == outcome["corrections_won"]
                        and heads_ahead == 0 and head_mismatch == 0 and not any(s >= 500 for s in st)}

        elif args.scenario == "purchase_backlog":
            # Phase 1: many households buy while no worker runs (the queue only grows).
            # Phase 2: N worker processes drain it with SKIP LOCKED leases.
            with db() as conn:
                households = [r[0] for r in conn.execute(
                    "SELECT h.id FROM households h JOIN entitlement_claims s ON s.household_id = h.id "
                    "JOIN devices d ON d.household_id = h.id WHERE s.plan_code = 'sleep' "
                    "AND d.mount IN ('floor_stand','wall_mount') AND NOT EXISTS (SELECT 1 FROM purchase_intents p "
                    "WHERE p.household_id = h.id) ORDER BY h.id LIMIT %s", (args.requests,))]

            async def buy(hh):
                d = await client.post("/v1/capability-decisions", headers=owner(hh),
                                      json={"feature": "developmental_milestones"})
                if d.status_code != 200 or not d.json().get("offer"):
                    return d.status_code
                body = d.json()
                r = await client.post("/v1/purchase-intents", headers=owner(hh) | {"Idempotency-Key": f"bench-backlog-{hh}"},
                                      json={"decision_id": body["decision_id"], "offer_id": body["offer"]["offer_id"]})
                return r.status_code

            lat, st, wall = await drive([lambda hh=hh: buy(hh) for hh in households], args.concurrency)
            with db() as conn:
                backlog = conn.execute("SELECT count(*), min(created_at) FROM outbox WHERE kind = 'submit_purchase' "
                                       "AND state = 'pending'").fetchone()
            env = os.environ | {"DATABASE_URL": os.environ["DATABASE_URL"],
                                "PROVIDER_DATABASE_URL": os.environ.get(
                                    "PROVIDER_DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_bench")}
            root = Path(__file__).resolve().parent.parent
            t0 = time.perf_counter()
            procs = [subprocess.Popen([sys.executable, "-m", "app.worker", "--once", "--id", f"bench{k}"], cwd=root, env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for k in range(args.workers)]
            outputs = [p.communicate(timeout=1800) for p in procs]
            drain = time.perf_counter() - t0
            processed = [int(o[0].strip().splitlines()[-1].split()[1]) if o[0].strip() else 0 for o in outputs]
            with db() as conn, psycopg.connect(env["PROVIDER_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")) as prov:
                states = dict(conn.execute("SELECT state, count(*) FROM purchase_intents "
                                           "WHERE idempotency_key LIKE 'bench-backlog-%%' GROUP BY 1").fetchall())
                left = conn.execute("SELECT count(*) FROM outbox WHERE kind = 'submit_purchase' AND state <> 'done'").fetchone()[0]
                keys = [r[0] for r in conn.execute("SELECT provider_operation_key FROM purchase_intents "
                                                   "WHERE idempotency_key LIKE 'bench-backlog-%%'")]
                ops = prov.execute("SELECT count(*), coalesce(max(attempts), 0) FROM provider_operations "
                                   "WHERE operation_key = ANY(%s)", (keys,)).fetchone()
            result["measured"] = summarize(lat, st, wall) | {
                "backlog_before_drain": backlog[0], "worker_processes": args.workers,
                "jobs_processed_per_worker": processed, "drain_seconds": round(drain, 2),
                "drain_jobs_per_second": round(backlog[0] / drain, 1) if drain else None, "pg_after": pg_stats()}
            result["invariant"] = {
                "rule": "every queued purchase is submitted exactly once: one provider operation per intent, "
                        "attempts = 1, nothing left in the queue",
                "intents_by_state": states, "provider_operations": ops[0], "max_attempts": ops[1], "jobs_left": left,
                "held": ops[0] == len(keys) and ops[1] == 1 and left == 0 and states.get("CONFIRMED") == len(keys)}

        elif args.scenario == "reconnect_storm":
            # Every client comes back at once and retries immediately on failure
            # (the worst client behavior: no backoff, no jitter).
            windows = sample_windows(2000, args.seed)
            retries = {"count": 0, "gave_up": 0}

            async def reconnect(w):
                for attempt in range(4):
                    try:
                        r = await client.get(f"/v1/windows/{w[0]}/brief", headers=caregiver(w[1]))
                        if r.status_code < 500:
                            return r.status_code
                    except httpx.HTTPError:
                        pass
                    retries["count"] += 1
                retries["gave_up"] += 1
                return 599

            rng = random.Random(args.seed)
            lat, st, wall = await drive([lambda w=w: reconnect(w) for w in (rng.choice(windows) for _ in range(args.requests))],
                                        args.concurrency)
            result["measured"] = summarize(lat, st, wall) | {"retries": retries, "pg_after": pg_stats()}
            result["invariant"] = {"rule": "no request fails for good; the server sheds nothing with a 5xx",
                                   "gave_up": retries["gave_up"], "held": retries["gave_up"] == 0}

        elif args.scenario == "large_history":
            with db() as conn:
                heavy = conn.execute("SELECT id, household_id FROM briefing_windows WHERE household_id = 'hh_000000' "
                                     "ORDER BY local_date DESC LIMIT 1").fetchone()
                nights = conn.execute("SELECT count(*) FROM briefing_windows WHERE household_id = 'hh_000000'").fetchone()[0]
            normal = sample_windows(500, args.seed)
            per = {}
            for label, targets in (("long_history", [heavy] * args.requests),
                                   ("ordinary", [random.Random(args.seed).choice(normal) for _ in range(args.requests)])):
                async def week(w):
                    r = await client.get(f"/v1/windows/{w[0]}/week", headers=caregiver(w[1]))
                    return r.status_code
                lat, st, wall = await drive([lambda w=w: week(w) for w in targets], args.concurrency)
                per[label] = summarize(lat, st, wall)
            result["measured"] = per["long_history"] | {"ordinary": per["ordinary"], "long_history_nights": nights}
            result["invariant"] = {"rule": "a household's history length does not change the week view's cost "
                                           "(p50 within 2x of an ordinary household)",
                                   "held": per["long_history"]["p50_ms"] <= 2 * per["ordinary"]["p50_ms"]
                                   and per["long_history"]["error_rate"] == 0}

        elif args.scenario == "soak":
            windows = sample_windows(2000, args.seed)
            rng = random.Random(args.seed)
            revision_ids: dict[str, str] = {}

            async def step(w):
                if w[0] in revision_ids and rng.random() < 0.1:
                    r = await client.post(f"/v1/windows/{w[0]}/acknowledgments", headers=caregiver(w[1]),
                                          json={"revision_id": revision_ids[w[0]], "displayed_facts": FACTS})
                else:
                    r = await client.get(f"/v1/windows/{w[0]}/brief", headers=caregiver(w[1]))
                    if r.status_code == 200:
                        revision_ids[w[0]] = r.json()["revision_id"]
                return r.status_code

            port = args.base.rsplit(":", 1)[-1]
            intervals, all_lat, all_st = [], [], []
            deadline = time.monotonic() + args.duration
            t_start = time.perf_counter()
            while time.monotonic() < deadline:
                lat, st, wall = await drive([lambda w=w: step(w) for w in (rng.choice(windows) for _ in range(args.concurrency * 100))],
                                            args.concurrency)
                all_lat += lat
                all_st += st
                intervals.append({"t_seconds": round(time.perf_counter() - t_start), **{k: v for k, v in summarize(lat, st, wall).items()
                                  if k in ("requests", "throughput_rps", "p50_ms", "p99_ms", "max_ms", "error_rate", "client_timeouts")},
                                  **pg_stats(), "server_memory_mb": server_memory_mb(port)})
            result["measured"] = summarize(all_lat, all_st, time.perf_counter() - t_start) | {"intervals": intervals}
            first, last = intervals[0], intervals[-1]
            mem = [i["server_memory_mb"] for i in intervals if i["server_memory_mb"]]
            result["invariant"] = {
                "rule": "no drift: last-interval p50 within 1.5x of the first, connections bounded by the pools, "
                        "no 5xx, server memory growth under 25%",
                "p50_first_ms": first["p50_ms"], "p50_last_ms": last["p50_ms"],
                "max_connections_seen": max(i["connections"] for i in intervals),
                "memory_first_mb": mem[0] if mem else None, "memory_last_mb": mem[-1] if mem else None,
                "held": last["p50_ms"] <= 1.5 * first["p50_ms"] and not any(s >= 500 for s in all_st)
                        and max(i["connections"] for i in intervals) <= 20 * args.server_workers + 10
                        and (not mem or mem[-1] <= 1.25 * mem[0])}
        return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8720")
    ap.add_argument("--scenario", required=True,
                    choices=["read_burst", "ack_burst", "correction_storm", "duplicate_purchase", "mixed",
                             "household_contention", "purchase_backlog", "reconnect_storm", "large_history", "soak"])
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--requests", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--label", default="")
    ap.add_argument("--workers", type=int, default=4, help="purchase_backlog: worker processes that drain the queue")
    ap.add_argument("--duration", type=int, default=600, help="soak: seconds")
    ap.add_argument("--server-workers", type=int, default=int(os.environ.get("BENCH_SERVER_WORKERS", "1")))
    args = ap.parse_args()
    result = {"environment": environment(args.base), "label": args.label, **asyncio.run(run(args))}
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT / f"{args.scenario}{'-' + args.label if args.label else ''}-{stamp}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "environment"}, indent=2, default=str))
    print("saved", path)


if __name__ == "__main__":
    main()
