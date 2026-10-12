"""Red team: eligibility, purchases and the provider boundary (I11-I15, I18, I23)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app import claims, decisions, provider, seed, worker
from app.db import SessionLocal, transaction
from app.main import app
from app.models import EntitlementClaim, IntentTransition, OutboxJob, PurchaseIntent

from ..conftest import OTHER, PARENT


def decide(client, feature="nextnap", headers=PARENT, **extra):
    r = client.post("/v1/capability-decisions", headers=headers, json={"feature": feature} | extra)
    assert r.status_code == 200, r.text
    return r.json()


def buy(client, decision, key, headers=PARENT, offer_id=None, **extra):
    body = {"decision_id": decision["decision_id"],
            "offer_id": offer_id or (decision.get("offer") or {}).get("offer_id", "offer-none")} | extra
    return client.post("/v1/purchase-intents", headers=headers | {"Idempotency-Key": key}, json=body)


def count(model, *where):
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


def plan(device=seed.CAMERA):
    with SessionLocal() as db:
        return claims.camera_plan(db, seed.HOUSEHOLD, device, "cat-2026-10-public")["plan_code"]


def expire_leases():
    with transaction() as db:
        db.execute(text("UPDATE outbox SET lease_until = now() - interval '1 second', available_at = now() "
                        "WHERE state IN ('in_progress', 'pending')"))


def state(intent_id):
    with SessionLocal() as db:
        return db.get(PurchaseIntent, intent_id).state


# ---------------------------------------------------------------- hardware blocks (I11, I12)

def test_rt_hardware_blocked_decision_never_produces_a_provider_submission(client, demo):
    blocked = decide(client, "nextnap", device_id=seed.PLAYROOM)       # Essential on its Anywhere Stand
    assert blocked["status"] == "BLOCKED" and blocked["offer"] is None
    assert any(b["kind"] == "hardware" for b in blocked["blockers"])
    real_offer = decide(client, "nextnap")["offer"]["offer_id"]        # a valid offer id from another camera
    for i, offer_id in enumerate([real_offer, "offer-none", "offer-milestones-year-cat-2026-10-public"]):
        r = buy(client, blocked, f"rt-hw-blocked-{i:03d}", offer_id=offer_id,
                expected_amount_minor=12000, expected_currency="USD")
        assert r.status_code == 409 and r.json()["code"] == "NO_ELIGIBLE_OFFER", r.text
    worker.run_once("rt")
    assert count(PurchaseIntent) == 0 and provider.ledger(seed.HOUSEHOLD) == []


def test_rt_mount_change_after_prepare_cancels_before_the_provider(client, demo):
    d = decide(client, "nextnap")
    r = buy(client, d, "rt-hw-after-prepare")
    assert r.status_code == 201
    assert client.post("/v1/dev/mount", headers=PARENT, json={"mount": "flex_stand"}).status_code == 200
    worker.run_once("rt")
    assert state(r.json()["intent_id"]) == "CANCELLED_BEFORE_SUBMISSION"
    assert provider.ledger(seed.HOUSEHOLD) == []


# ---------------------------------------------------------------- ownership

def test_rt_decision_belongs_to_its_household(client, demo):
    d = decide(client, "nextnap")
    r = buy(client, d, "rt-foreign-decision", headers=OTHER)
    assert r.status_code == 404 and count(PurchaseIntent) == 0
    assert client.get(f"/v1/capability-decisions/{d['decision_id']}", headers=OTHER).status_code == 404


# ---------------------------------------------------------------- client prices (I12)

def test_rt_client_price_fields_cannot_override_server_terms(client, demo):
    d = decide(client, "nextnap")
    server = d["offer"]
    r = buy(client, d, "rt-price-extra-001", amount_minor=1, price=0, currency="EUR", plan_code="milestones",
            terms={"amount_minor": 1, "currency": "EUR", "plan_code": "milestones"}, billing_period="DAY")
    assert r.status_code == 201, r.text
    terms = r.json()["terms"]
    assert (terms["amount_minor"], terms["currency"], terms["plan_code"], terms["billing_period"]) == \
        (server["amount_minor"], server["currency"], server["plan_code"], server["billing_period"])
    worker.run_once("rt")
    op = provider.ledger(seed.HOUSEHOLD)[0]
    assert (op["amount_minor"], op["currency"]) == (server["amount_minor"], server["currency"])


@pytest.mark.parametrize("expected", [{"expected_amount_minor": 1}, {"expected_amount_minor": 0},
                                      {"expected_currency": "usd"}, {"expected_currency": "EUR"},
                                      {"expected_amount_minor": 12001, "expected_currency": "USD"}])
def test_rt_mismatched_expected_terms_are_rejected(client, demo, expected):
    d = decide(client, "nextnap")
    r = buy(client, d, "rt-price-check-01", **expected)
    assert r.status_code == 409 and r.json()["code"] == "TERMS_MISMATCH" and count(PurchaseIntent) == 0


# ---------------------------------------------------------------- stale and expired (I12)

def test_rt_expired_decision_is_rejected(client, demo, monkeypatch):
    d = decide(client, "nextnap")
    real_now = decisions.utcnow
    monkeypatch.setattr(decisions, "utcnow", lambda: real_now() + decisions.DECISION_TTL + timedelta(seconds=1))
    r = buy(client, d, "rt-expired-001")
    assert r.status_code == 409 and r.json()["code"] == "STALE_DECISION"
    assert "DECISION_EXPIRED" in r.json()["detail"]["reasons"] and count(PurchaseIntent) == 0


def test_rt_stale_decision_is_rejected_even_if_state_returns(client, demo):
    d = decide(client, "nextnap")
    client.post("/v1/dev/mount", headers=PARENT, json={"mount": "flex_stand"})
    client.post("/v1/dev/mount", headers=PARENT, json={"mount": "floor_stand"})   # back where it was
    r = buy(client, d, "rt-stale-001")
    assert r.status_code == 409 and "HOUSEHOLD_STATE_CHANGED" in r.json()["detail"]["reasons"]
    assert count(PurchaseIntent) == 0


# ---------------------------------------------------------------- idempotency (I13, I18)

def test_rt_same_key_from_concurrent_separate_clients_creates_one_intent(client, demo):
    d = decide(client, "nextnap")
    n = 16
    gate = Barrier(n)

    def send(_):
        with TestClient(app) as c:
            gate.wait(timeout=20)
            return buy(c, d, "rt-concurrent-same-key")

    with ThreadPoolExecutor(n) as pool:
        results = list(pool.map(send, range(n)))
    assert sorted(r.status_code for r in results) == [200] * (n - 1) + [201], [r.text for r in results]
    assert len({r.json()["intent_id"] for r in results}) == 1
    assert count(PurchaseIntent) == 1 and count(OutboxJob, OutboxJob.kind == "submit_purchase") == 1
    worker.run_once("rt")
    assert len(provider.ledger(seed.HOUSEHOLD)) == 1


def test_rt_different_keys_concurrently_still_charge_one_upgrade(client, demo):
    d = decide(client, "nextnap")
    n = 12
    gate = Barrier(n)

    def send(i):
        with TestClient(app) as c:
            gate.wait(timeout=20)
            return buy(c, d, f"rt-concurrent-key-{i:03d}")

    with ThreadPoolExecutor(n) as pool:
        results = list(pool.map(send, range(n)))
    codes = sorted(r.status_code for r in results)
    assert codes == [201] + [409] * (n - 1), [r.text for r in results]
    assert {r.json()["code"] for r in results if r.status_code == 409} == {"UPGRADE_IN_PROGRESS"}
    worker.run_once("rt")
    assert count(PurchaseIntent) == 1 and len(provider.ledger(seed.HOUSEHOLD)) == 1


def test_rt_same_key_with_conflicting_payload_is_rejected(client, demo):
    d = decide(client, "nextnap")
    first = buy(client, d, "rt-conflict-key-01")
    assert first.status_code == 201
    for variant in ({"expected_currency": "USD"}, {"expected_amount_minor": 12000}, {"offer_id": "offer-x"}):
        r = buy(client, d, "rt-conflict-key-01", **variant)
        assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED", (variant, r.text)
    other = decide(client, "video_history_7d")
    r = buy(client, other, "rt-conflict-key-01")
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert count(PurchaseIntent) == 1


def test_rt_conflicting_payloads_racing_on_one_key(client, demo):
    d = decide(client, "nextnap")
    n = 10
    gate = Barrier(n)

    def send(i):
        with TestClient(app) as c:
            gate.wait(timeout=20)
            extra = {"expected_currency": "USD"} if i % 2 else {"expected_amount_minor": 12000}
            return buy(c, d, "rt-race-conflict", **extra)

    with ThreadPoolExecutor(n) as pool:
        results = list(pool.map(send, range(n)))
    assert all(r.status_code in (200, 201, 409) for r in results)
    assert sum(r.status_code == 201 for r in results) == 1
    winner = next(r.json() for r in results if r.status_code == 201)
    for r in results:
        if r.status_code == 200:
            assert r.json()["intent_id"] == winner["intent_id"]
        elif r.status_code == 409:
            assert r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert count(PurchaseIntent) == 1


# ---------------------------------------------------------------- provider outcomes (I14, I15)

@pytest.mark.parametrize("behavior,intent_state,ledger_status", [
    ("succeed", "CONFIRMED", "SUCCEEDED"),
    ("decline", "FAILED", "FAILED"),
    ("pending", "UNKNOWN", "PENDING"),
    ("lose_response", "CONFIRMED", "SUCCEEDED"),
    ("unavailable", "SUBMITTED", None),
])
def test_rt_provider_outcomes_are_recorded_distinctly(client, demo, behavior, intent_state, ledger_status):
    assert client.post("/v1/dev/provider/next", headers=PARENT, json={"behavior": behavior}).status_code == 200
    i = buy(client, decide(client, "nextnap"), f"rt-outcome-{behavior}").json()["intent_id"]
    worker.run_once("rt")
    assert state(i) == intent_state
    ledger = provider.ledger(seed.HOUSEHOLD)
    assert [op["status"] for op in ledger] == ([ledger_status] if ledger_status else [])
    assert plan() == ("memories" if intent_state == "CONFIRMED" else "sleep")
    if behavior == "pending":   # still undecided after another look: never CONFIRMED without SUCCEEDED
        expire_leases()
        worker.reconcile_intents("rt")
        assert state(i) == "UNKNOWN" and plan() == "sleep"
        provider.resolve_pending(f"op_{i}", "FAILED")
        worker.reconcile_intents("rt")
        assert state(i) == "FAILED" and plan() == "sleep"


# ---------------------------------------------------------------- crash after external success (I15)

def test_rt_crash_after_provider_success_reconciles_once_under_racing_recovery(client, demo, monkeypatch):
    monkeypatch.setenv("PROOF_LAYER_INPROCESS_CRASH", "1")
    i = buy(client, decide(client, "nextnap"), "rt-crash-001").json()["intent_id"]
    with pytest.raises(worker.SimulatedCrash):
        worker.run_once("rt", crash_after_provider=True)
    assert state(i) == "SUBMITTED" and provider.lookup(f"op_{i}")["status"] == "SUCCEEDED"
    monkeypatch.delenv("PROOF_LAYER_INPROCESS_CRASH")
    expire_leases()
    gate = Barrier(4)

    def recover(k):
        gate.wait(timeout=20)
        return worker.reconcile_intents(f"rt{k}") if k % 2 else worker.run_once(f"rt{k}")

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(recover, range(4)))
    assert state(i) == "CONFIRMED" and plan() == "memories"
    assert count(EntitlementClaim, EntitlementClaim.external_ref == f"op_{i}") == 1
    assert len(provider.ledger(seed.HOUSEHOLD)) == 1
    with SessionLocal() as db:
        to_states = [t.to_state for t in db.execute(select(IntentTransition).where(
            IntentTransition.intent_id == i).order_by(IntentTransition.id)).scalars()]
    assert to_states == ["PREPARED", "SUBMITTED", "CONFIRMED"]


def test_rt_real_process_crash_via_dev_worker_reconciles_without_duplicates(client, demo):
    i = buy(client, decide(client, "nextnap"), "rt-crash-proc").json()["intent_id"]
    crashed = client.post("/v1/dev/worker", headers=PARENT, json={"crash_after_provider": True}).json()
    assert crashed["crashed"] is True, crashed
    assert state(i) == "SUBMITTED"
    expire_leases()
    again = client.post("/v1/dev/worker", headers=PARENT, json={}).json()
    assert again["exit_code"] == 0, again
    assert state(i) == "CONFIRMED"
    assert count(EntitlementClaim, EntitlementClaim.external_ref == f"op_{i}") == 1
    assert len(provider.ledger(seed.HOUSEHOLD)) == 1


# ---------------------------------------------------------------- I23 for the app's own purchases

# RT-8 (fixed): the app's own unconfirmed purchase used to be offered again.
def test_rt_purchase_being_confirmed_is_not_offered_again(client, demo):
    client.post("/v1/dev/provider/next", headers=PARENT, json={"behavior": "pending"})
    i = buy(client, decide(client, "nextnap"), "rt-unknown-001").json()["intent_id"]
    worker.run_once("rt")
    assert state(i) == "UNKNOWN"
    again = decide(client, "nextnap")
    # The second purchase is blocked (UPGRADE_IN_PROGRESS) so nothing is charged twice,
    # but the parent is shown a paywall for what is being confirmed (I23).
    assert again["offer"] is None and again["status"] == "UNKNOWN"
