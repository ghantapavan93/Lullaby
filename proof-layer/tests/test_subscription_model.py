"""The subscription model under a controlled clock, against PostgreSQL.

Each test fixes application time (app.clock), drives the real API, and checks
four things: the persisted claims, the camera's effective entitlement, the
status a parent sees, and the recovery the server allows. Database ordering
(a connect versus the check that verifies it) stays on the database clock and
is tested with real concurrent transactions.

Synthetic purchases only; no store is contacted.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import access, claims, clock, seed
from app.db import SessionLocal, transaction
from app.models import AccessAction, AccessCheck, Device, EntitlementClaim
from app.security import Principal, lock_household

from .conftest import OTHER, PARENT

T0 = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
CAT = "cat-2026-10-public"
NURSERY = seed.CAMERA


def human(d: datetime) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def day(n: float) -> datetime:
    return (T0 + timedelta(days=n)).replace(hour=0, minute=0, second=0, microsecond=0)


@pytest.fixture
def at():
    """A world seeded at T0 with a frozen clock; returns a function that moves it."""
    with clock.frozen(T0):
        with transaction() as db:
            seed.load(db)
        yield lambda n: clock.advance_to(T0 + timedelta(days=n))


def evidence(client, ref, start, end, *, version=1, status="verified", plan="memories", channel="apple",
             headers=PARENT):
    return client.post("/v1/dev/purchase-evidence", headers=headers, json={
        "channel": channel, "external_ref": ref, "plan_code": plan, "status": status,
        "provider_version": version, "starts_at": day(start).isoformat(), "days": int(end - start)})


def connect(client, ref, channel="apple", key=None, device=NURSERY):
    rev = client.get("/v1/access", headers=PARENT).json()["state_revision"]
    return client.post("/v1/access/bind", headers=PARENT | {"Idempotency-Key": key or f"key-{ref}-{device}"},
                       json={"device_id": device, "channel": channel, "external_ref": ref,
                             "expected_state_revision": rev})


def verify(client, device=NURSERY):
    return client.post("/v1/access/verify", headers=PARENT, json={"device_id": device}).json()


def status(client, device=NURSERY):
    return client.get(f"/v1/access/cameras/{device}", headers=PARENT).json()


def rows(ref):
    with SessionLocal() as db:
        return [(c.device_id, c.event_status, c.plan_code, c.starts_at, c.ends_at) for c in db.execute(
            select(EntitlementClaim).where(EntitlementClaim.external_ref == ref)
            .order_by(EntitlementClaim.starts_at, EntitlementClaim.event_status)).scalars()]


def entitlement(device=NURSERY):
    with SessionLocal() as db:
        return claims.camera_plan(db, seed.HOUSEHOLD, device, CAT)


def actions_count():
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(AccessAction)).scalar_one()


# ---------------------------------------------------------------- operations

def test_renewal(client, at):
    evidence(client, "apple-ren", -360, 5)
    assert connect(client, "apple-ren").status_code == 201
    verify(client)
    at(10)                                                     # the period ended five days ago
    ended = status(client)
    assert ended["status"] == "PLAN_EXPIRED" and ended["offer"] is None
    assert ended["message"].startswith(f"Memories on Nursery camera ended on {human(day(5))}")
    assert ended["allowed_actions"] == ["check_again", "contact_support"]       # recovery: wait or support

    r = evidence(client, "apple-ren", 5, 370, version=2)        # the store's renewal arrives
    assert r.status_code == 200 and r.json()["renewed_claim"]
    assert [(d, s, p) for d, s, p, _, _ in rows("apple-ren")] == [(NURSERY, "active", "memories")] * 2
    assert entitlement()["plan_code"] == "memories"
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"
    assert actions_count() == 1                                 # no second connect


def test_cheaper_plan_renewal(client, at):
    evidence(client, "apple-down", -360, 5, plan="milestones")
    connect(client, "apple-down")
    verify(client)
    evidence(client, "apple-down", 5, 370, plan="sleep", version=2)     # renewed one tier lower
    assert entitlement()["plan_code"] == "milestones" and entitlement()["member_cap"] == 50
    at(10)
    plan = entitlement()
    assert plan["plan_code"] == "sleep" and plan["member_cap"] == 2
    s = status(client)
    assert s["status"] == "ACTIVE_FEATURE_USABLE" and s["message"].startswith("Sleep is active")
    assert "developmental_milestones" not in s["usable_features"]
    assert [p for _, _, p, _, _ in rows("apple-down")] == ["milestones", "sleep"]


def test_same_start_extension(client, at):
    evidence(client, "apple-ext", -360, 5)
    connect(client, "apple-ext")
    verify(client)
    evidence(client, "apple-ext", -360, 200, version=2)        # same start, later end
    stored = rows("apple-ext")
    assert len(stored) == 2 and stored[0][3] == stored[1][3] and stored[1][4] == day(200)
    at(10)
    assert entitlement()["plan_code"] == "memories" and status(client)["status"] == "ACTIVE_FEATURE_USABLE"


def test_late_arriving_prior_period(client, at):
    evidence(client, "apple-late", 300, 665, version=2)       # next year arrives first
    assert connect(client, "apple-late").status_code == 201
    early = status(client)
    assert early["status"] == "PLAN_SCHEDULED" and human(day(300)) in early["message"]
    evidence(client, "apple-late", -65, 300, version=1)       # the current year, late
    assert len(rows("apple-late")) == 2
    assert entitlement()["plan_code"] == "memories"
    assert status(client)["status"] == "ASSIGNED_PENDING_CONFIRMATION"   # recovery: verify
    verify(client)
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"


def test_future_effective_period(client, at):
    evidence(client, "apple-future", 20, 385)
    assert status(client)["status"] == "VERIFIED_UNBOUND"      # found, connectable now
    connect(client, "apple-future")
    s = status(client)
    assert s["status"] == "PLAN_SCHEDULED" and s["allowed_actions"] == ["check_again"]
    assert s["plan"]["plan_code"] == "sleep"                    # the trial still applies today
    at(21)
    assert status(client)["status"] == "ASSIGNED_PENDING_CONFIRMATION"
    verify(client)
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE" and entitlement()["plan_code"] == "memories"


def test_refund_before_verification(client, at):
    evidence(client, "apple-refund", -10, 355)
    connect(client, "apple-refund")
    evidence(client, "apple-refund", -10, 355, version=2, status="refunded")
    assert [s for _, s, _, _, _ in rows("apple-refund")] == ["active", "revoked"]
    s = status(client)
    assert s["status"] == "REFUNDED_OR_REVOKED" and s["allowed_actions"] == ["contact_support"]
    check = verify(client)["check"]
    assert check["kind"] == "activation" and check["status"] == "REFUNDED_OR_REVOKED"
    assert entitlement()["plan_code"] == "sleep"


def test_cross_household_purchase_ownership(client, at):
    evidence(client, "web-owned", -10, 355, channel="web")
    theirs = evidence(client, "web-owned", -10, 355, channel="web", headers=OTHER)
    assert theirs.status_code == 409 and theirs.json()["code"] == "PURCHASE_OWNED_ELSEWHERE"
    claim = client.post("/v1/dev/claims", headers=OTHER, json={"channel": "web", "external_ref": "web-owned",
                                                               "event_status": "active", "plan_code": "memories"})
    assert claim.status_code == 409 and claim.json()["code"] == "PURCHASE_OWNED_ELSEWHERE"
    assert connect(client, "web-owned", channel="web").status_code == 201
    assert {d for d, *_ in rows("web-owned")} == {NURSERY}


def test_same_instant_timezone_replay(client, at):
    first = client.post("/v1/dev/purchase-evidence", headers=PARENT, json={
        "channel": "apple", "external_ref": "apple-tz", "plan_code": "memories", "status": "verified",
        "provider_version": 1, "starts_at": "2026-10-01T04:00:00+00:00", "days": 365}).json()
    again = client.post("/v1/dev/purchase-evidence", headers=PARENT, json={
        "channel": "apple", "external_ref": "apple-tz", "plan_code": "memories", "status": "verified",
        "provider_version": 1, "starts_at": "2026-10-01T00:00:00-04:00", "days": 365}).json()
    assert again["replayed"] and again["evidence_id"] == first["evidence_id"]
    c1 = client.post("/v1/dev/claims", headers=PARENT, json={
        "channel": "google", "external_ref": "gpa-tz", "event_status": "active", "plan_code": "memories",
        "starts_at": "2026-10-01T04:00:00+00:00"}).json()
    c2 = client.post("/v1/dev/claims", headers=PARENT, json={
        "channel": "google", "external_ref": "gpa-tz", "event_status": "active", "plan_code": "memories",
        "starts_at": "2026-10-01T00:00:00-04:00"}).json()
    assert c2["replayed"] and c2["claim_id"] == c1["claim_id"] and len(rows("gpa-tz")) == 1


def test_free_trial_expiration(client, at):
    trial_end = next(e for d, s, p, _, e in rows(f"trial-{NURSERY}"))
    at((trial_end - T0).total_seconds() / 86400 + 3)
    s = status(client)
    assert s["status"] == "PLAN_EXPIRED" and s["offer"] is None
    assert s["message"].startswith(f"The Sleep trial on Nursery camera ended on {human(trial_end)}")
    assert "renewal" not in s["message"] and s["allowed_actions"] == []
    at((trial_end - T0).total_seconds() / 86400 + 40)                    # an old ending is history
    assert status(client)["status"] == "NO_PURCHASE"


def test_concurrent_connect_and_verification_ordering(client, at):
    """A check whose transaction began BEFORE the connect committed, and that
    waited for the household lock, must still sort after the connect it saw.
    With transaction-start timestamps it would sort first and the camera would
    stay 'connected, checking' after it was verified."""
    evidence(client, "apple-race", -10, 355)
    principal = Principal(seed.PARENT_A, seed.HOUSEHOLD, "owner", True, "Parent A")
    checker = SessionLocal()
    checker_started = checker.execute(select(func.transaction_timestamp())).scalar_one()   # begins its transaction
    result = {}

    def run_check():
        household = lock_household(checker, seed.HOUSEHOLD)                    # waits for the connect
        device = checker.execute(select(Device).where(Device.id == NURSERY)).scalar_one()
        result["check"] = access.verify(checker, principal, household, device)["check"]
        checker.commit()

    with transaction() as db:
        household = lock_household(db, seed.HOUSEHOLD)
        device = db.execute(select(Device).where(Device.id == NURSERY)).scalar_one()
        access.bind(db, principal, household, device, channel="apple", external_ref="apple-race",
                    expected_state_revision=household.state_revision, idempotency_key="race-connect-1")
        worker = threading.Thread(target=run_check)
        worker.start()
        time.sleep(0.3)                                                          # the check is now waiting
        assert worker.is_alive()
    worker.join(timeout=10)
    checker.close()

    with SessionLocal() as db:
        connected_at = db.execute(select(AccessAction.created_at)).scalar_one()
        checked_at = db.execute(select(AccessCheck.created_at)).scalar_one()
    assert checker_started < connected_at < checked_at        # transaction start would have sorted it first
    assert result["check"]["kind"] == "activation" and result["check"]["status"] == "ACTIVE_FEATURE_USABLE"
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"


def test_a_scenario_reset_never_fails_while_a_page_keeps_polling(client, at):
    """The demo reset used to deadlock with an open page's requests and return
    500 on either side. With all locks taken up front (NOWAIT, retried), neither
    side fails: readers wait for the reset, the reset never waits holding locks."""
    statuses, stop = [], threading.Event()

    def page():
        while not stop.is_set():
            for path in ("/v1/access", "/v1/me", "/v1/xray"):
                statuses.append(client.get(path, headers=PARENT).status_code)

    readers = [threading.Thread(target=page) for _ in range(4)]
    for r in readers:
        r.start()
    try:
        resets = [client.post("/v1/dev/access-scenario", headers=PARENT, json={"scenario": key}).status_code
                  for key in ("plan_ended", "paid_not_active", "renewal_reflected", "plan_ended")]
    finally:
        stop.set()
        for r in readers:
            r.join(timeout=30)
    assert resets == [200, 200, 200, 200]
    assert statuses and 500 not in statuses, sorted(set(statuses))


def test_a_store_contradiction_after_connect_is_not_hidden_behind_checking(client, at):
    """Reported by an external review (2026-10-11) and reproduced: after a
    connect and before its check, two statuses at the same provider version
    left the parent on calm "connected, checking" copy with the purchase
    stage marked verified. Contradictory evidence must surface."""
    evidence(client, "apple-contra", -10, 355)
    assert connect(client, "apple-contra").status_code == 201
    assert status(client)["status"] == "ASSIGNED_PENDING_CONFIRMATION"
    evidence(client, "apple-contra", -10, 355, version=1, status="refunded")   # same version, other status
    s = status(client)
    assert s["status"] == "CONFLICTING_EVIDENCE", s["status"]
    assert s["stages"][0]["state"] == "conflicting"
    assert s["allowed_actions"] == ["contact_support"] and s["offer"] is None
    assert "checking that its features are ready" not in s["message"]
    check = verify(client)["check"]                       # the check records the conflict, not "usable"
    assert check["status"] == "CONFLICTING_EVIDENCE"
