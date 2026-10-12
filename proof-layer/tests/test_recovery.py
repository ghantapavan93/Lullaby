"""Provider boundary and recovery.

Invariants
- The provider is called only after SUBMITTED is committed, with a key fixed
  at preparation; every retry reuses it; duplicate deliveries never create a
  second provider operation.
- The application never records an outcome the provider did not record:
  FAILED stays FAILED, PENDING stays UNKNOWN, and UNKNOWN becomes CONFIRMED
  only when the provider's ledger says SUCCEEDED.
- A crash between the provider's commit and the application's commit is
  recovered from the provider's durable ledger, on the original intent.
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app import claims, provider, seed, worker
from app.db import SessionLocal, transaction
from app.models import Household, IntentTransition, PurchaseIntent

from .conftest import PARENT, ROOT

TRACES = ROOT.parent / "artifacts" / "recovery-traces"


def prepared(client, key="recovery-key-001"):
    d = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    r = client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": key},
                    json={"decision_id": d["decision_id"], "offer_id": d["offer"]["offer_id"]})
    assert r.status_code == 201, r.text
    return r.json()["intent_id"]


def intent(intent_id):
    with SessionLocal() as db:
        return db.get(PurchaseIntent, intent_id)


def states(intent_id):
    with SessionLocal() as db:
        return [t.to_state for t in db.execute(select(IntentTransition).where(
            IntentTransition.intent_id == intent_id).order_by(IntentTransition.id)).scalars()]


def plan():
    with SessionLocal() as db:
        # Plans are per camera and derived from claims (contract change, migration 0003).
        return claims.camera_plan(db, seed.HOUSEHOLD, seed.CAMERA, "cat-2026-10-public")["plan_code"]


def expire_leases():
    with transaction() as db:
        db.execute(text("UPDATE outbox SET lease_until = now() - interval '1 second', "
                        "available_at = now() WHERE state = 'in_progress' OR state = 'pending'"))


def save_trace(name, client, intent_id):
    """Evidence files are refreshed only on request (PROOF_LAYER_SAVE_TRACES=1),
    so ordinary test runs don't rewrite committed artifacts."""
    if os.environ.get("PROOF_LAYER_SAVE_TRACES") != "1":
        return
    TRACES.mkdir(parents=True, exist_ok=True)
    body = client.get(f"/v1/purchase-intents/{intent_id}", headers=PARENT).json()
    (TRACES / f"{name}.json").write_text(json.dumps(body, indent=2), encoding="utf-8")


def test_happy_path_confirms_and_grants_the_plan(client, demo):
    i = prepared(client)
    worker.run_once("t")
    assert intent(i).state == "CONFIRMED" and plan() == "memories"
    assert states(i) == ["PREPARED", "SUBMITTED", "CONFIRMED"]
    assert [op["status"] for op in provider.ledger(seed.HOUSEHOLD)] == ["SUCCEEDED"]
    d = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    assert d["status"] == "AVAILABLE"                          # the upgrade really unlocked it


def test_provider_failure_stays_failure(client, demo):
    provider.schedule(seed.HOUSEHOLD, "decline")
    i = prepared(client)
    worker.run_once("t")
    assert intent(i).state == "FAILED" and plan() == "sleep"
    worker.reconcile_intents("t")
    assert intent(i).state == "FAILED"


@pytest.mark.parametrize("settles_to,final", [("SUCCEEDED", "CONFIRMED"), ("FAILED", "FAILED")])
def test_provider_unknown_stays_unknown_until_the_provider_decides(client, demo, settles_to, final):
    provider.schedule(seed.HOUSEHOLD, "pending")
    i = prepared(client)
    worker.run_once("t")
    assert intent(i).state == "UNKNOWN"
    for _ in range(3):
        expire_leases()
        worker.reconcile_intents("t")
        worker.run_once("t")
        assert intent(i).state == "UNKNOWN" and plan() == "sleep"   # never invented
    provider.resolve_pending(intent(i).provider_operation_key, settles_to)
    expire_leases()
    worker.reconcile_intents("t")
    assert intent(i).state == final
    assert len(provider.ledger(seed.HOUSEHOLD)) == 1


def test_crash_after_provider_success_is_recovered_in_process(client, demo, monkeypatch):
    monkeypatch.setenv("PROOF_LAYER_INPROCESS_CRASH", "1")
    i = prepared(client)
    with pytest.raises(worker.SimulatedCrash):
        worker.run_once("t", crash_after_provider=True)
    assert intent(i).state == "SUBMITTED" and plan() == "sleep"
    assert provider.lookup(intent(i).provider_operation_key)["status"] == "SUCCEEDED"
    expire_leases()
    result = worker.reconcile_intents("t")
    assert result == [{"intent_id": i, "provider": "SUCCEEDED", "state": "CONFIRMED"}]
    assert intent(i).state == "CONFIRMED" and plan() == "memories"
    ops = provider.ledger(seed.HOUSEHOLD)
    assert len(ops) == 1 and ops[0]["attempts"] == 1 and ops[0]["operation_key"] == f"op_{i}"
    save_trace("crash-in-process-reconciled", client, i)


def test_crash_of_a_real_worker_process_is_recovered_by_a_restart(client, demo):
    i = prepared(client)
    env = os.environ | {"PROOF_LAYER_LEASE_SECONDS": "1"}
    env.pop("PROOF_LAYER_INPROCESS_CRASH", None)
    crashed = subprocess.run([sys.executable, "-m", "app.worker", "--once", "--crash-after-provider"],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert crashed.returncode == 17, crashed.stderr                # the process really died
    assert intent(i).state == "SUBMITTED"
    assert provider.lookup(f"op_{i}")["status"] == "SUCCEEDED"
    time.sleep(1.5)                                                # its lease runs out
    restarted = subprocess.run([sys.executable, "-m", "app.worker", "--once"], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=60)
    assert restarted.returncode == 0, restarted.stderr
    assert intent(i).state == "CONFIRMED" and plan() == "memories"
    assert len(provider.ledger(seed.HOUSEHOLD)) == 1
    save_trace("crash-real-process-reconciled", client, i)
    if os.environ.get("PROOF_LAYER_SAVE_TRACES") == "1":
        (TRACES / "crash-real-process-worker-output.txt").write_text(
            f"crashed exit={crashed.returncode}\n{crashed.stdout}{crashed.stderr}\n"
            f"restarted exit={restarted.returncode}\n{restarted.stdout}{restarted.stderr}", encoding="utf-8")


def test_duplicate_outbox_delivery_never_creates_a_second_operation(client, demo, monkeypatch):
    monkeypatch.setenv("PROOF_LAYER_INPROCESS_CRASH", "1")
    i = prepared(client)
    with pytest.raises(worker.SimulatedCrash):
        worker.run_once("t", crash_after_provider=True)
    expire_leases()
    worker.run_once("t")                                           # the job is delivered again
    assert intent(i).state == "CONFIRMED"
    ops = provider.ledger(seed.HOUSEHOLD)
    assert len(ops) == 1 and ops[0]["attempts"] == 2               # same key, deduplicated
    with transaction() as db:                                      # and once more, after completion
        db.execute(text("UPDATE outbox SET state='pending', available_at=now()"))
    worker.run_once("t")
    assert len(provider.ledger(seed.HOUSEHOLD)) == 1


def test_concurrent_workers_submit_once(client, demo):
    i = prepared(client)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda n: worker.run_once(f"w{n}"), range(4)))
    assert intent(i).state == "CONFIRMED"
    assert len(provider.ledger(seed.HOUSEHOLD)) == 1


def test_lost_response_is_resolved_from_the_ledger(client, demo):
    provider.schedule(seed.HOUSEHOLD, "lose_response")
    i = prepared(client)
    worker.run_once("t")
    assert intent(i).state == "CONFIRMED"
    with SessionLocal() as db:
        last = db.execute(select(IntentTransition).where(IntentTransition.intent_id == i)
                          .order_by(IntentTransition.id.desc())).scalars().first()
        assert last.reason == "reconciled_after_lost_response"


def test_unreachable_provider_retries_with_the_same_key(client, demo):
    provider.schedule(seed.HOUSEHOLD, "unavailable")
    i = prepared(client)
    worker.run_once("t")
    assert intent(i).state == "SUBMITTED" and provider.ledger(seed.HOUSEHOLD) == []
    expire_leases()
    worker.run_once("t")
    assert intent(i).state == "CONFIRMED"
    assert [op["operation_key"] for op in provider.ledger(seed.HOUSEHOLD)] == [f"op_{i}"]


def test_eligibility_change_before_submission_cancels_without_calling_the_provider(client, demo):
    i = prepared(client)
    client.post("/v1/dev/mount", headers=PARENT, json={"mount": "flex_stand"})
    worker.run_once("t")
    assert intent(i).state == "CANCELLED_BEFORE_SUBMISSION"
    assert provider.ledger(seed.HOUSEHOLD) == [] and plan() == "sleep"


def test_changes_after_the_authorization_boundary_cannot_recall_the_submission(client, demo, monkeypatch):
    """Documented limit: once SUBMITTED is committed the provider call is
    authorized; a later mount change cannot undo a charge the provider made."""
    monkeypatch.setenv("PROOF_LAYER_INPROCESS_CRASH", "1")
    i = prepared(client)
    with pytest.raises(worker.SimulatedCrash):
        worker.run_once("t", crash_after_provider=True)
    client.post("/v1/dev/mount", headers=PARENT, json={"mount": "flex_stand"})
    expire_leases()
    worker.reconcile_intents("t")
    assert intent(i).state == "CONFIRMED"   # the truth is recorded, not hidden


def test_intent_history_is_append_only(client, demo):
    from sqlalchemy.exc import DBAPIError
    from app.db import engine
    i = prepared(client)
    worker.run_once("t")
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE intent_transitions SET to_state='FAILED' WHERE intent_id=:i"), {"i": i})


def test_state_revision_moves_on_confirmation(client, demo):
    with SessionLocal() as db:
        before = db.get(Household, seed.HOUSEHOLD).state_revision
    prepared(client)
    worker.run_once("t")
    with SessionLocal() as db:
        assert db.get(Household, seed.HOUSEHOLD).state_revision == before + 1
