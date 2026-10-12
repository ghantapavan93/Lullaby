"""Failure lab: inject one fault at a time and record what the system did.

Each scenario starts from a freshly seeded synthetic household, injects one
real fault through the real code paths (API calls, the engine, the worker as a
separate OS process, the provider simulator), then compares the observed
durable state with the invariant that should hold. HTTP 200 is never the
verdict; the stored rows are. An explicit, safe refusal counts as correct.

    python scripts/failure_lab.py

Runs against its own databases (proof_layer_lab, provider_sim_lab). Results:
artifacts/failure-lab/failure-lab.json and failure-lab.md.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

ROOT = Path(__file__).resolve().parent.parent
os.environ["DATABASE_URL"] = "postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_lab"
os.environ["PROVIDER_DATABASE_URL"] = "postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_lab"
os.environ["PROOF_LAYER_DEV"] = "1"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402

from app import provider, seed, worker  # noqa: E402
from app.db import SessionLocal, transaction  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (AnalyticsEvent, BriefingRevision, BriefingWindow, EvidenceEvent, OutboxJob,  # noqa: E402
                        PurchaseIntent, RenderReceipt)
from app.security import issue_token  # noqa: E402

OUT = ROOT.parent / "artifacts" / "failure-lab"
PARENT = {"Authorization": f"Bearer {issue_token(seed.PARENT_A)}"}
CAREGIVER = {"Authorization": f"Bearer {issue_token(seed.CAREGIVER_B)}"}
FACTS = ["recorded_sleep", "sleep_onset", "final_wake", "observation_coverage", "unobserved"]
SCENARIOS = []


def scenario(fault: str, uncertainty: str = ""):
    def register(fn):
        SCENARIOS.append((fault, uncertainty, fn))
        return fn
    return register


def count(model, *where) -> int:
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


def brief(client, window_id: str, headers=PARENT) -> dict:
    return client.get(f"/v1/windows/{window_id}/brief", headers=headers).json()


def camera(items):
    with transaction() as db:
        return seed.camera_sync(db, seed.HOUSEHOLD, seed.CHILD, seed.CAMERA, items)


def expire_leases():
    with transaction() as db:
        db.execute(text("UPDATE outbox SET lease_until = now() - interval '1 second', available_at = now() "
                        "WHERE state IN ('in_progress', 'pending')"))


def decide(client, feature="nextnap"):
    return client.post("/v1/capability-decisions", headers=PARENT, json={"feature": feature}).json()


def buy(client, d, key):
    return client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": key},
                       json={"decision_id": d["decision_id"], "offer_id": (d.get("offer") or {}).get("offer_id", "none")})


def intent_state(intent_id):
    with SessionLocal() as db:
        return db.get(PurchaseIntent, intent_id).state


# ------------------------------------------------------------------ evidence

@scenario("Duplicate sleep event (camera re-sends a record under a new key)")
def duplicate_sleep(client, demo):
    w = demo["window_id"]
    before = brief(client, w)
    with SessionLocal() as db:
        e = db.execute(select(EvidenceEvent).where(EvidenceEvent.kind == "sleep_interval",
                                                   EvidenceEvent.source == "CAMERA",
                                                   EvidenceEvent.household_id == seed.HOUSEHOLD)
                       .order_by(EvidenceEvent.ended_at.desc())).scalars().first()
    camera([seed.sleep("lab-dup-" + uuid.uuid4().hex[:8], e.started_at, e.ended_at)])
    after = brief(client, w)
    rs_b, rs_a = before["facts"]["recorded_sleep"], after["facts"]["recorded_sleep"]
    held = (rs_a["uncontested_minutes"] == rs_b["uncontested_minutes"]
            and after["fingerprints"]["recorded_sleep"] == before["fingerprints"]["recorded_sleep"])
    return {"expected": "minutes and the recorded-sleep fingerprint unchanged; caregivers aren't asked to re-review",
            "observed": f"rev {before['revision']}→{after['revision']}, minutes {rs_b['uncontested_minutes']}→{rs_a['uncontested_minutes']}, "
                        f"fingerprint {'same' if after['fingerprints']['recorded_sleep'] == before['fingerprints']['recorded_sleep'] else 'changed'}",
            "held": held, "final_state": {"revision": after["revision"], "evidence_rows": count(EvidenceEvent)}}


@scenario("Out-of-order observation (an earlier camera report arrives last)",
          "Coverage reflects what was reported, whenever it arrives; a report that never arrives stays a gap.")
def out_of_order(client, demo):
    w = demo["window_id"]
    before = brief(client, w)["facts"]["observation_coverage"]
    camera([seed.obs("lab-late-obs", seed.at(10, "03:40"), seed.at(10, "04:00"))])
    after = brief(client, w)["facts"]["observation_coverage"]
    gained = round(after["observed_minutes"] - before["observed_minutes"], 1)
    return {"expected": "observed minutes rise by exactly the 20 newly covered minutes; coverage stays ≤ 100%",
            "observed": f"observed {before['observed_minutes']}→{after['observed_minutes']} min (+{gained}), {before['pct']}%→{after['pct']}%",
            "held": gained == 20 and after["pct"] <= 100, "final_state": after}


@scenario("Missing camera coverage (sleep recorded, no availability report)")
def missing_coverage(client, demo):
    camera([seed.sleep("lab-nocov-1", seed.at(11, "21:00"), seed.at(11, "23:30"))])
    with SessionLocal() as db:
        wid = db.execute(select(BriefingWindow.id).where(BriefingWindow.child_id == seed.CHILD)
                         .order_by(BriefingWindow.local_date.desc())).scalars().first()
    cov = brief(client, wid)["facts"]["observation_coverage"]
    return {"expected": "coverage is unknown (no percentage), never 100% and never a measured 0%",
            "observed": f"state={cov['state']}, pct={cov['pct']}",
            "held": cov["state"] == "unknown" and cov["pct"] is None, "final_state": cov}


@scenario("Simultaneous corrections (8 parents' screens submit at once)")
def simultaneous_corrections(client, demo):
    w, gate = demo["window_id"], Barrier(8)

    def attempt(i):
        gate.wait()
        with TestClient(app) as c:
            return c.post(f"/v1/windows/{w}/corrections", headers=PARENT, json={
                "target_event_id": demo["final_wake_event_id"], "expected_revision": demo["revision"],
                "client_event_id": f"lab-sim-{i}-{uuid.uuid4().hex[:6]}",
                "corrected_end": f"2026-10-10T06:{30 + i:02d}:00-04:00"}).status_code
    with ThreadPoolExecutor(8) as pool:
        codes = sorted(pool.map(attempt, range(8)))
    revisions = count(BriefingRevision, BriefingRevision.window_id == w)
    return {"expected": "exactly one wins (200); seven get 409; exactly one new revision",
            "observed": f"status codes {codes}; revisions {demo['revision']}→{revisions}",
            "held": codes.count(200) == 1 and codes.count(409) == 7 and revisions == demo["revision"] + 1,
            "final_state": {"revisions": revisions}}


@scenario("Stale caregiver screen (acknowledges an older revision after a correction)",
          "The caregiver is told what changed; the system can't know whether they read it.")
def stale_screen(client, demo):
    w = demo["window_id"]
    shown = brief(client, w, CAREGIVER)
    client.post(f"/v1/windows/{w}/corrections", headers=PARENT, json={
        "target_event_id": demo["final_wake_event_id"], "expected_revision": shown["revision"],
        "client_event_id": "lab-stale-corr-1", "corrected_end": "2026-10-10T06:35:00-04:00"})
    ack = client.post(f"/v1/windows/{w}/acknowledgments", headers=CAREGIVER,
                      json={"revision_id": shown["revision_id"], "displayed_facts": FACTS}).json()
    return {"expected": "the ack binds to the revision shown, says a newer one exists, and names what changed",
            "observed": f"acknowledged rev {ack['acknowledged_revision']}, current {ack['current_revision']}, "
                        f"changed {ack['changed_since']}",
            "held": ack["acknowledged_revision"] == shown["revision"] and ack["newer_available"]
                    and "final_wake" in ack["changed_since"],
            "final_state": {k: ack[k] for k in ("acknowledged_revision", "current_revision", "changed_since")}}


# ------------------------------------------------------------------ commerce

@scenario("Expired upgrade (offer bought after its expiry)")
def expired_offer(client, demo):
    d = decide(client)
    with transaction() as db:
        db.execute(text("UPDATE capability_decisions SET expires_at = now() - interval '1 second' WHERE id = :id"),
                   {"id": d["decision_id"]})
    r = buy(client, d, "lab-expired-01")
    return {"expected": "409 STALE_DECISION; no intent; nothing charged",
            "observed": f"{r.status_code} {r.json().get('code')}; intents {count(PurchaseIntent)}; provider ops {len(provider.ledger(seed.HOUSEHOLD))}",
            "held": r.status_code == 409 and r.json()["code"] == "STALE_DECISION" and count(PurchaseIntent) == 0,
            "final_state": {"intents": count(PurchaseIntent)}}


@scenario("Hardware change (camera moved to a Flex Stand after the offer was shown)")
def hardware_change(client, demo):
    d = decide(client, "developmental_milestones")
    client.post("/v1/dev/mount", headers=PARENT, json={"mount": "flex_stand"})
    r = buy(client, d, "lab-hardware-01")
    again = decide(client, "developmental_milestones")
    return {"expected": "the shown offer is refused; a fresh check blocks on hardware with no offer",
            "observed": f"buy {r.status_code} {r.json().get('code')}; new decision {again['status']} "
                        f"{[b['code'] for b in again['blockers']]}, offer {again['offer']}",
            "held": r.status_code == 409 and again["offer"] is None
                    and "MOUNT_NOT_SUPPORTED" in {b["code"] for b in again["blockers"]},
            "final_state": {"intents": count(PurchaseIntent), "provider_ops": len(provider.ledger(seed.HOUSEHOLD))}}


@scenario("Duplicate purchase (10 taps with one idempotency key, at once)")
def duplicate_purchase(client, demo):
    d, gate = decide(client), Barrier(10)

    def tap(_):
        gate.wait()
        with TestClient(app) as c:
            return buy(c, d, "lab-dup-key-0001").status_code
    with ThreadPoolExecutor(10) as pool:
        codes = sorted(pool.map(tap, range(10)))
    jobs = count(OutboxJob, OutboxJob.kind == "submit_purchase")
    return {"expected": "one 201, nine replays (200); one intent; one submit job",
            "observed": f"codes {codes}; intents {count(PurchaseIntent)}; submit jobs {jobs}",
            "held": codes.count(201) == 1 and codes.count(200) == 9 and count(PurchaseIntent) == 1 and jobs == 1,
            "final_state": {"intents": count(PurchaseIntent), "submit_jobs": jobs}}


@scenario("Worker crash after the provider charged (real process exit)",
          "Between the crash and reconciliation the app says 'submitted', not 'confirmed'.")
def worker_crash(client, demo):
    i = buy(client, decide(client), "lab-crash-0001").json()["intent_id"]
    env = os.environ | {"PROOF_LAYER_LEASE_SECONDS": "1"}
    env.pop("PROOF_LAYER_INPROCESS_CRASH", None)
    crashed = subprocess.run([sys.executable, "-m", "app.worker", "--once", "--crash-after-provider"],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    mid = intent_state(i)
    charged = provider.lookup(f"op_{i}")["status"]
    time.sleep(1.2)
    worker.reconcile_intents("lab")
    ops = provider.ledger(seed.HOUSEHOLD)
    return {"expected": "process exits 17; intent stays SUBMITTED; reconciliation confirms from the ledger; one operation, one attempt",
            "observed": f"exit {crashed.returncode}; after crash {mid} (provider {charged}); after reconcile {intent_state(i)}; "
                        f"operations {len(ops)}, attempts {[o['attempts'] for o in ops]}",
            "held": crashed.returncode == 17 and mid == "SUBMITTED" and intent_state(i) == "CONFIRMED"
                    and len(ops) == 1 and ops[0]["attempts"] == 1,
            "final_state": {"intent": intent_state(i), "provider_operations": len(ops)}}


@scenario("Provider timeout (charge pending at the provider)",
          "While UNKNOWN, the app neither grants nor re-sells; only the provider can settle it.")
def provider_pending(client, demo):
    provider.schedule(seed.HOUSEHOLD, "pending")
    i = buy(client, decide(client), "lab-pending-01").json()["intent_id"]
    worker.run_once("lab")
    first = intent_state(i)
    for _ in range(3):
        expire_leases()
        worker.reconcile_intents("lab")
    still = intent_state(i)
    offer_while_unknown = decide(client)["offer"]
    provider.resolve_pending(f"op_{i}", "SUCCEEDED")
    expire_leases()
    worker.reconcile_intents("lab")
    return {"expected": "UNKNOWN until the provider decides, no offer meanwhile, then CONFIRMED; one operation",
            "observed": f"{first} → after 3 reconciles {still}; offer while unknown: {offer_while_unknown}; "
                        f"after settle {intent_state(i)}; operations {len(provider.ledger(seed.HOUSEHOLD))}",
            "held": first == "UNKNOWN" and still == "UNKNOWN" and offer_while_unknown is None
                    and intent_state(i) == "CONFIRMED" and len(provider.ledger(seed.HOUSEHOLD)) == 1,
            "final_state": {"intent": intent_state(i)}}


@scenario("Lost response after a committed charge")
def lost_response(client, demo):
    provider.schedule(seed.HOUSEHOLD, "lose_response")
    i = buy(client, decide(client), "lab-lost-0001").json()["intent_id"]
    worker.run_once("lab")
    ops = provider.ledger(seed.HOUSEHOLD)
    return {"expected": "the worker asks the ledger instead of retrying blind: CONFIRMED with one operation, one attempt",
            "observed": f"{intent_state(i)}; operations {len(ops)}, attempts {[o['attempts'] for o in ops]}",
            "held": intent_state(i) == "CONFIRMED" and len(ops) == 1 and ops[0]["attempts"] == 1,
            "final_state": {"intent": intent_state(i)}}


# ------------------------------------------------------------------ growth

@scenario("Analytics duplication (client resends an event and a render receipt)")
def analytics_duplication(client, demo):
    d = decide(client)
    event = {"event_id": "evt-lab-0001", "name": "briefing_viewed", "properties": {"revision": 4, "status": "incomplete_observation"}}
    receipt = {"render_id": "render-lab-0001", "surface": "feature_availability", "subject_id": d["decision_id"],
               "experiment_key": "availability_explanation", "variant": d["experiment"]["variant"]}
    for _ in range(3):
        client.post("/v1/analytics", headers=PARENT, json=event)
        client.post("/v1/render-receipts", headers=PARENT, json=receipt)
    events = count(AnalyticsEvent, AnalyticsEvent.name == "briefing_viewed")
    receipts = count(RenderReceipt)
    return {"expected": "each stored once", "observed": f"events {events}, render receipts {receipts}",
            "held": events == 1 and receipts == 1, "final_state": {"events": events, "receipts": receipts}}


def run() -> list[dict]:
    from app.db import engine
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True,
                   capture_output=True, env=os.environ.copy())
    provider.create_schema()
    results = []
    for fault, uncertainty, fn in SCENARIOS:
        with transaction() as db:
            demo = seed.load(db)
        with TestClient(app) as client:
            started = time.perf_counter()
            try:
                r = fn(client, demo)
            except Exception as exc:  # a crash in the lab is a result, not a reason to stop
                r = {"expected": "", "observed": f"lab error: {exc!r}", "held": False, "final_state": None}
        results.append({"fault": fault, **r, "uncertainty": uncertainty or "None beyond the stated rule.",
                        "seconds": round(time.perf_counter() - started, 2)})
        print(f"{'HELD  ' if r['held'] else 'BROKEN'} {fault}", flush=True)
    results.append({"fault": "Invalid referral qualification", "expected": "", "observed": "not run: referrals are not built",
                    "held": None, "final_state": None, "uncertainty": "Deferred by research (11_RESEARCH_DECISIONS.md).",
                    "seconds": 0})
    engine.dispose()
    return results


def main() -> None:
    results = run()
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "command": "python scripts/failure_lab.py", "synthetic": True, "scenarios": results}
    (OUT / "failure-lab.json").write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    rows = ["| Fault | Expected | Observed | Result |", "|---|---|---|---|"]
    for r in results:
        verdict = "not run" if r["held"] is None else ("**held**" if r["held"] else "**BROKEN**")
        rows.append(f"| {r['fault']} | {r['expected']} | {r['observed']} | {verdict} |")
    held = sum(1 for r in results if r["held"])
    ran = sum(1 for r in results if r["held"] is not None)
    (OUT / "failure-lab.md").write_text(
        f"# Failure lab\n\nGenerated {doc['generated_at']} by `{doc['command']}` on synthetic data. "
        f"{held} of {ran} injected faults held their invariant.\n\n" + "\n".join(rows) + "\n\n"
        "Uncertainty that remains after each fault is in `failure-lab.json` (`uncertainty`).\n", encoding="utf-8")
    print(f"{held}/{ran} held")


if __name__ == "__main__":
    main()
