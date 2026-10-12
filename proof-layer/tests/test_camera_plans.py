"""Plans per camera, derived from channel claims.

Invariants
- A camera's plan is a function of the SET of its claims: shuffling or
  duplicating channel events never changes it.
- A revoked transaction never grants, whatever order its events arrive in.
- Plan and team-member limit come from one computation and cannot disagree.
- While a purchase is unconfirmed, the camera is "checking", not offered it again.
- An activation code works on one camera only; a replay is a no-op.
- Eligibility, offers and purchases are per camera: buying for one camera
  never changes another, and the mount that counts is that camera's mount.
"""
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from hypothesis import given, settings, strategies as st

from app import claims, entitlements, seed, worker
from app.db import SessionLocal

from .conftest import PARENT

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
CAT = "cat-2026-10-public"


@dataclass(frozen=True)
class C:
    channel: str
    external_ref: str
    event_status: str
    plan_code: str
    starts_at: datetime
    ends_at: Optional[datetime]


def plan(cs):
    return entitlements.effective_plan(cs, NOW, CAT)


# ---------------------------------------------------------------- pure rule

events = st.lists(st.builds(
    C, channel=st.sampled_from(["apple", "google", "web", "trial"]),
    external_ref=st.sampled_from(["t1", "t2", "t3"]),
    event_status=st.sampled_from(["active", "pending", "revoked"]),
    plan_code=st.sampled_from(["sleep", "memories", "milestones"]),
    starts_at=st.sampled_from([NOW - timedelta(days=30), NOW + timedelta(days=1)]),
    ends_at=st.sampled_from([None, NOW + timedelta(days=30), NOW - timedelta(days=1)])),
    max_size=8).filter(lambda xs: all(x.ends_at is None or x.ends_at > x.starts_at for x in xs))


@settings(max_examples=400, deadline=None)
@given(events, st.randoms())
def test_order_and_duplicates_never_change_the_plan(cs, rnd):
    shuffled = list(cs) + list(cs[: len(cs) // 2])   # duplicates of a prefix
    rnd.shuffle(shuffled)
    a, b = plan(cs), plan(shuffled)
    assert (a["plan_code"], a["state"], a["member_cap"], a["pending_plans"]) == \
           (b["plan_code"], b["state"], b["member_cap"], b["pending_plans"])
    # Each purchase's own view too: a lapsed or scheduled purchase's plan is
    # shown on the Plan stage (mutation M16 survived without this line).
    assert a["transactions"] == b["transactions"]


@settings(max_examples=200, deadline=None)
@given(events)
def test_team_limit_always_matches_the_plan(cs):
    p = plan(cs)
    assert p["member_cap"] == entitlements.MEMBER_CAPS[p["plan_code"]]


def test_revoked_transaction_never_grants_in_any_order():
    active = C("apple", "t9", "active", "milestones", NOW - timedelta(days=1), NOW + timedelta(days=364))
    refund = C("apple", "t9", "revoked", "milestones", NOW - timedelta(days=1), NOW + timedelta(days=364))
    trial = C("trial", "tr", "active", "sleep", NOW - timedelta(days=10), NOW + timedelta(days=80))
    for order in ([active, refund, trial], [refund, active, trial], [trial, refund, active]):
        assert plan(order)["plan_code"] == "sleep"


def test_pending_purchase_means_checking_not_none():
    p = plan([C("apple", "t1", "pending", "memories", NOW, None)])
    assert p["state"] == "checking" and p["pending_plans"] == ["memories"] and p["plan_code"] == "none"


def test_lapsed_plan_is_not_active():
    p = plan([C("web", "old", "active", "milestones", NOW - timedelta(days=400), NOW - timedelta(days=35))])
    assert p["plan_code"] == "none" and p["transactions"][0]["state"] == "lapsed"


# ---------------------------------------------------------------- through the API

def claim(client, **body):
    return client.post("/v1/dev/claims", headers=PARENT, json={"days": 365, **body})


def cameras(client):
    return {d["id"]: d for d in client.get("/v1/household/commercial", headers=PARENT).json()["devices"]}


def test_upgrade_reflected_with_team_limit_in_the_same_step(client, demo):
    """A reported symptom: an upgrade that didn't raise the team limit."""
    r = claim(client, channel="apple", external_ref="apple-1", event_status="active", plan_code="memories")
    after = r.json()["after"]
    assert after["plan_code"] == "memories" and after["member_cap"] == 10
    assert cameras(client)[seed.CAMERA]["plan"]["member_cap"] == 10


def test_duplicate_store_event_is_a_no_op_and_changed_content_conflicts(client, demo):
    body = dict(channel="google", external_ref="gpa-77", event_status="active", plan_code="memories")
    first, again = claim(client, **body), claim(client, **body)
    assert again.json()["replayed"] is True and again.json()["claim_id"] == first.json()["claim_id"]
    assert claim(client, **(body | {"plan_code": "milestones"})).status_code == 409


def test_activation_code_works_on_one_camera_only(client, demo):
    code = dict(channel="activation_code", external_ref="CODE-ABC-123", event_status="active", plan_code="milestones")
    assert claim(client, device_id=seed.CAMERA, **code).status_code == 200
    assert claim(client, device_id=seed.CAMERA, **code).json()["replayed"] is True
    r = claim(client, device_id=seed.PLAYROOM, **code)
    assert r.status_code == 409 and r.json()["code"] == "CODE_ALREADY_REDEEMED"
    assert cameras(client)[seed.PLAYROOM]["plan"]["plan_code"] == "none"


def test_late_store_confirmation_moves_from_checking_to_active(client, demo):
    claim(client, channel="apple", external_ref="apple-late", event_status="pending", plan_code="milestones")
    assert cameras(client)[seed.CAMERA]["plan"]["state"] == "checking"
    claim(client, channel="apple", external_ref="apple-late", event_status="active", plan_code="milestones")
    cam = cameras(client)[seed.CAMERA]["plan"]
    assert cam["state"] == "active" and cam["plan_code"] == "milestones"


def test_each_camera_is_judged_by_its_own_mount_and_plan(client, demo):
    nursery = client.post("/v1/capability-decisions", headers=PARENT,
                          json={"feature": "developmental_milestones", "device_id": seed.CAMERA}).json()
    playroom = client.post("/v1/capability-decisions", headers=PARENT,
                           json={"feature": "developmental_milestones", "device_id": seed.PLAYROOM}).json()
    assert nursery["offer"]["plan_code"] == "milestones"
    assert playroom["offer"] is None and any(b["kind"] == "hardware" for b in playroom["blockers"])


def test_a_purchase_for_one_camera_never_changes_another(client, demo):
    d = client.post("/v1/capability-decisions", headers=PARENT,
                    json={"feature": "developmental_milestones", "device_id": seed.CAMERA}).json()
    r = client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": "camera-scope-1"},
                    json={"decision_id": d["decision_id"], "offer_id": d["offer"]["offer_id"]})
    assert r.status_code == 201
    worker.run_once("t")
    cams = cameras(client)
    assert cams[seed.CAMERA]["plan"]["plan_code"] == "milestones"
    assert cams[seed.PLAYROOM]["plan"]["plan_code"] == "none"


def test_plan_preview_shows_what_a_plan_cannot_fix(client, demo):
    preview = client.get(f"/v1/devices/{seed.PLAYROOM}/plan-preview", headers=PARENT).json()
    milestones = next(p for p in preview["plans"] if p["plan_code"] == "milestones")
    assert milestones["features"]["developmental_milestones"]["blocked_by"] == ["hardware"]
    assert milestones["features"]["video_history_7d"]["status"] == "AVAILABLE"


def test_claims_are_append_only(demo):
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.db import engine
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE entitlement_claims SET plan_code = 'milestones'"))


def test_a_second_pending_purchase_makes_earlier_offers_stale(client, demo):
    """Found by the model-based test (test_model_commerce.py). The camera is
    already "checking" a pending Memories purchase when a pending Milestones
    purchase arrives. Plan, state and team limit are unchanged, but what may be
    sold changed: Milestones is now being confirmed. The household version must
    move, so a screen still showing the Milestones offer is stale."""
    def pending(plan_code):
        r = client.post("/v1/dev/claims", headers=PARENT, json={
            "channel": "apple", "external_ref": f"pend-{plan_code}", "event_status": "pending",
            "plan_code": plan_code})
        assert r.status_code == 200, r.text

    pending("memories")
    d = client.post("/v1/capability-decisions", headers=PARENT,
                    json={"feature": "developmental_milestones"}).json()
    assert d["status"] == "BLOCKED" and d["offer"]["plan_code"] == "milestones"
    pending("milestones")
    again = client.get(f"/v1/capability-decisions/{d['decision_id']}", headers=PARENT).json()
    assert again["current"] is False and "HOUSEHOLD_STATE_CHANGED" in again["stale_reasons"]
    r = client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": "pend-key-01"},
                    json={"decision_id": d["decision_id"], "offer_id": d["offer"]["offer_id"]})
    assert r.status_code == 409 and r.json()["code"] == "STALE_DECISION"


def test_a_plan_that_starts_after_the_check_is_not_sold_again(client, demo):
    """Found by mutation testing (M13 survived): nothing else covered the
    purchase-time re-check. A store event can be recorded ahead of its start
    date, so a camera's plan can change with time alone, with no new event and
    no new household version. The decision still looks current; only re-running
    the rules at purchase time notices that the camera now has the plan."""
    import time
    from datetime import datetime, timedelta, timezone
    starts = datetime.now(timezone.utc) + timedelta(seconds=2)
    r = client.post("/v1/dev/claims", headers=PARENT, json={
        "channel": "web", "external_ref": "web-future-1", "event_status": "active", "plan_code": "milestones",
        "starts_at": starts.isoformat()})
    assert r.status_code == 200, r.text
    d = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "developmental_milestones"}).json()
    assert d["status"] == "BLOCKED" and d["offer"]["plan_code"] == "milestones"
    time.sleep(max(0.0, (starts - datetime.now(timezone.utc)).total_seconds()) + 0.5)
    assert client.get(f"/v1/capability-decisions/{d['decision_id']}", headers=PARENT).json()["current"] is True
    r = client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": "future-plan-01"},
                    json={"decision_id": d["decision_id"], "offer_id": d["offer"]["offer_id"]})
    assert r.status_code == 409 and r.json()["code"] == "STALE_DECISION", r.text


def test_one_code_redeemed_by_two_households_at_once_lands_on_one_camera(demo):
    """Found by mutation testing (M19 survived): codes are global, but the
    household lock serializes only one household. Two households redeeming the
    same code at the same moment must still end with one camera holding it,
    and the other must get a clear refusal, not a server error."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select
    from app.main import app
    from app.models import EntitlementClaim
    from .conftest import OTHER
    gate = Barrier(2)

    def redeem(args):
        headers, code = args
        with TestClient(app) as c:
            gate.wait()
            return c.post("/v1/dev/claims", headers=headers, json={
                "channel": "activation_code", "external_ref": code, "event_status": "active",
                "plan_code": "milestones", "starts_at": "2026-10-01T00:00:00Z"})

    for i in range(10):   # a fresh code each round, so every round is a real race
        code = f"CODE-RACE-{i}"
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(redeem, ((PARENT, code), (OTHER, code))))
        assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
        assert next(r for r in responses if r.status_code == 409).json()["code"] == "CODE_ALREADY_REDEEMED"
        with SessionLocal() as db:
            cameras = db.execute(select(func.count(func.distinct(EntitlementClaim.device_id))).where(
                EntitlementClaim.external_ref == code)).scalar_one()
        assert cameras == 1
