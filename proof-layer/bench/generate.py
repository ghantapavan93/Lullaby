"""Synthetic data factory. Reproducible, streamed, isolated from the demo.

    python -m bench.generate --profile dev            # 1,000 households
    python -m bench.generate --profile intermediate   # 10,000 households
    python -m bench.generate --profile large          # 100,000 households (refuses if disk is short)

Writes ONLY to the benchmark databases (default proof_layer_bench). Every
record is synthetic and marked so; nothing here is Nanit data. The same seed
always produces the same households, nights, gaps and corrections.

Facts and fingerprints are computed with the application's own pure rules
(`app.briefing`), so generated revisions are exactly what the API would have
written. Rows are streamed with COPY in batches to keep memory flat.
"""
import argparse
import json
import os
import random
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_bench")
os.environ.setdefault("PROVIDER_DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_bench")

import psycopg  # noqa: E402

from app import briefing  # noqa: E402
from app.engine import window_bounds  # noqa: E402

PROFILES = {  # households, nights per household, MB on disk (dev measured at 41.8 MB; others extrapolated)
    "demo": (10, 7, 5),
    "dev": (1_000, 7, 45),
    "intermediate": (10_000, 7, 450),
    "large": (100_000, 3, 2_000),
}
ZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"]
PLANS = [("sleep", 50), ("memories", 30), ("milestones", 15), ("none", 5)]
MOUNTS = [("floor_stand", 45), ("wall_mount", 35), ("flex_stand", 20)]
FIRST_NIGHT = date(2026, 9, 1)
TABLES = ("ack_heads, acknowledgments, briefing_revisions, briefing_windows, evidence_events, entitlement_claims, "
          "devices, children, members, households, outbox, purchase_intents, intent_transitions, "
          "capability_decisions, analytics_events, authorization_log, render_receipts, experiment_assignments, "
          "notifications")


def weighted(rng: random.Random, choices):
    return rng.choices([c for c, _ in choices], weights=[w for _, w in choices])[0]


def dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


def night(rng: random.Random, hh: str, child: str, device: str, owner: str, tz: str, day: date, n: int):
    """One synthetic night: usable camera observation with an optional gap,
    3-5 sleep segments, and sometimes a parent correction of the last one."""
    zone = ZoneInfo(tz)
    start, end = window_bounds(day, tz)
    events, base = [], f"{hh}-{day.isoformat()}"
    received = end + timedelta(minutes=rng.randint(1, 30))
    if rng.random() < 0.01:                      # late arrival: synced hours after the night
        received += timedelta(hours=rng.randint(6, 30))

    gap = None
    if rng.random() < 0.6:
        g_start = start + timedelta(minutes=rng.randint(60, 600))
        gap = (g_start, g_start + timedelta(minutes=rng.randint(10, 60)))
    usable = [(start, gap[0]), (gap[1], end)] if gap else [(start, end)]
    for i, (s, e) in enumerate(usable):
        events.append(dict(id=f"ev-{base}-o{i}", key=f"{base}-obs-{i}", kind="camera_availability", source="CAMERA",
                           s=s, e=e, payload={"usable": True, "synthetic": True, "schema": 1}, sup=None))
    if gap:
        events.append(dict(id=f"ev-{base}-off", key=f"{base}-off", kind="camera_availability", source="CAMERA",
                           s=gap[0], e=gap[1], payload={"usable": False, "synthetic": True, "schema": 1}, sup=None))

    cursor = datetime.combine(day, datetime.min.time(), zone).replace(hour=19) + timedelta(minutes=rng.randint(0, 50))
    cursor = cursor.astimezone(timezone.utc)
    segments = rng.randint(3, 5)
    for i in range(segments):
        length = timedelta(minutes=rng.randint(90, 230))
        s, e = cursor, min(cursor + length, end - timedelta(minutes=rng.randint(5, 40)))
        if e <= s:
            break
        events.append(dict(id=f"ev-{base}-s{i}", key=f"{base}-sleep-{i}", kind="sleep_interval", source="CAMERA",
                           s=s, e=e, payload={"synthetic": True, "schema": 1}, sup=None))
        cursor = e + timedelta(minutes=rng.randint(5, 45))
        if cursor >= end:
            break

    corrected = False
    sleeps = [x for x in events if x["kind"] == "sleep_interval"]
    if sleeps and rng.random() < 0.05:
        last = sleeps[-1]
        new_end = last["e"] - timedelta(minutes=rng.randint(5, 20))
        if new_end > last["s"]:
            corrected = True
            events.append(dict(id=f"ev-{base}-c", key=f"{base}-corr", kind="sleep_interval", source="PARENT",
                               s=last["s"], e=new_end, sup=last["id"], actor=owner,
                               payload={"synthetic": True, "schema": 1, "corrects": last["id"],
                                        "camera_basis": [[last["s"].isoformat(), last["e"].isoformat()]]}))
    for x in events:
        x.setdefault("actor", None)
        x["received"] = received
        x["device"] = device if x["source"] == "CAMERA" else None
    return events, (start, end), corrected


def revisions(events, window, tz, wid, hh):
    def facts_for(evs):
        superseded = {x["sup"] for x in evs if x["sup"]}
        views = [briefing.EvidenceView(x["id"], x["kind"], x["source"], x["s"], x["e"], x["payload"], x["sup"])
                 for x in evs if x["id"] not in superseded]
        return briefing.compute_facts(window, tz, views), [v.id for v in views]

    out = []
    has_correction = any(x["sup"] for x in events)
    first = [x for x in events if not x["sup"]]
    for number, evs in ((1, first), (2, events)) if has_correction else ((1, events),):
        facts, ids = facts_for(evs)
        out.append((f"rev-{wid}-{number}", hh, wid, number, json.dumps(facts), json.dumps(briefing.fingerprints(facts)),
                    json.dumps(ids), json.dumps({"kind": "correction" if number == 2 else "evidence", "synthetic": True})))
    return out


def generate(profile: str, seed: int, batch: int, history_nights: int = 0) -> dict:
    """history_nights > 0 gives household hh_000000 that many nights, so one
    household has a long history (the large-history read workload)."""
    households, nights, est_mb = PROFILES[profile]
    free_mb = shutil.disk_usage(os.environ.get("PROOF_LAYER_PGDATA_DRIVE", os.path.expanduser("~"))).free / 2**20
    if free_mb - est_mb < 2048:
        sys.exit(f"refusing: profile {profile} needs ~{est_mb} MB and would leave {free_mb - est_mb:.0f} MB free "
                 f"(keep 2 GB). Free disk space or choose a smaller profile.")
    rng = random.Random(seed)
    counts = dict(households=0, members=0, windows=0, revisions=0, evidence=0, corrections=0, late=0)
    t0 = time.perf_counter()
    with psycopg.connect(dsn(), autocommit=False, options="-c timezone=UTC") as conn:
        conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
        conn.commit()
        for lo in range(0, households, batch):
            hh_rows, mem_rows, child_rows, dev_rows, claim_rows, win_rows, rev_rows, ev_rows = ([] for _ in range(8))
            for i in range(lo, min(lo + batch, households)):
                hh, tz = f"hh_{i:06d}", ZONES[i % len(ZONES)]
                owner, care, child, device = f"m_{i:06d}_a", f"m_{i:06d}_b", f"c_{i:06d}", f"d_{i:06d}"
                hh_rows.append((hh, f"Synthetic household {i}", tz))
                mem_rows += [(owner, hh, "Parent A", "owner", True), (care, hh, "Caregiver B", "caregiver", False)]
                child_rows.append((child, hh, "Synthetic child"))
                dev_rows.append((device, hh, "Nursery camera", "pro", weighted(rng, MOUNTS), True))
                plan = weighted(rng, PLANS)
                if plan != "none":   # plans attach to the camera, as a channel claim
                    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
                    claim_rows.append((f"clm-{device}", hh, device, "trial" if plan == "sleep" else "web",
                                       f"bench-{device}", "active", plan, start, start + timedelta(days=365),
                                       "synthetic"))
                for k in range(history_nights if i == 0 and history_nights else nights):
                    # A long history ends on the same night as everyone else's.
                    back = history_nights - nights if i == 0 and history_nights else 0
                    day = FIRST_NIGHT + timedelta(days=k - back)
                    evs, window, corrected = night(rng, hh, child, device, owner, tz, day, k)
                    wid = f"w-{hh}-{day.isoformat()}"
                    revs = revisions(evs, window, tz, wid, hh)
                    win_rows.append((wid, hh, child, day, tz, window[0], window[1], len(revs)))
                    rev_rows += revs
                    for x in evs:
                        ev_rows.append((x["id"], hh, child, x["key"], "synthetic", x["kind"], x["source"], x["actor"],
                                        x["device"], x["s"], x["e"], json.dumps(x["payload"]), x["sup"], x["received"]))
                    counts["corrections"] += corrected
                    counts["late"] += int((evs[0]["received"] - window[1]) > timedelta(hours=1))
            with conn.cursor() as cur:
                for table, cols, rows in (
                    ("households", "id, name, timezone", hh_rows),
                    ("members", "id, household_id, display_name, role, can_bill", mem_rows),
                    ("children", "id, household_id, display_name", child_rows),
                    ("devices", "id, household_id, label, model, mount, is_primary", dev_rows),
                    ("entitlement_claims", "id, household_id, device_id, channel, external_ref, event_status, "
                                           "plan_code, starts_at, ends_at, request_hash", claim_rows),
                    ("briefing_windows", "id, household_id, child_id, local_date, timezone, starts_at, ends_at, "
                                         "current_revision", win_rows),
                    ("evidence_events", "id, household_id, child_id, client_event_id, request_hash, kind, source, "
                                        "actor_member_id, device_id, started_at, ended_at, payload, "
                                        "supersedes_event_id, received_at", ev_rows),
                    ("briefing_revisions", "id, household_id, window_id, revision, facts, fingerprints, evidence_ids, "
                                           "cause", rev_rows),
                ):
                    with cur.copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
                        for row in rows:
                            copy.write_row(row)
            conn.commit()
            counts["households"] += len(hh_rows)
            counts["members"] += len(mem_rows)
            counts["windows"] += len(win_rows)
            counts["revisions"] += len(rev_rows)
            counts["evidence"] += len(ev_rows)
            print(f"  {counts['households']:>7} households  {counts['evidence']:>9} evidence rows  "
                  f"{time.perf_counter() - t0:6.1f}s", flush=True)
        conn.execute("ANALYZE")
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    return {"profile": profile, "seed": seed, "nights_per_household": nights, "history_nights_hh_000000": history_nights,
            **counts,
            "seconds": round(time.perf_counter() - t0, 1), "database_mb": round(size / 2**20, 1)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=PROFILES, default="dev")
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--history-nights", type=int, default=0, help="nights for household hh_000000")
    ap.add_argument("--out", help="also write the dataset record (with machine details) to this JSON file")
    args = ap.parse_args()
    result = generate(args.profile, args.seed, args.batch, args.history_nights)
    if args.out:
        import platform
        record = {"dataset": result, "synthetic": True, "command": " ".join(sys.argv),
                  "environment": {"machine": platform.processor(), "cpu_count": os.cpu_count(),
                                  "os": platform.platform(), "python": platform.python_version()},
                  "executed_at": datetime.now(timezone.utc).isoformat()}
        Path(args.out).write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
