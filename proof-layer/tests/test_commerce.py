"""Eligibility, offers and purchase preparation.

Invariants
- An offer exists only when every blocker is a subscription blocker, and the
  offered plan provably unlocks the feature under the same rules.
- Nothing is prepared from a stale, expired, foreign, offer-less, mismatched
  or re-priced decision; the price is always the server's.
- One (household, idempotency key) is one intent, under any concurrency; the
  same key with different content is a conflict.
"""
import itertools
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select, text

from app import entitlements, seed
from app.db import SessionLocal, transaction
from app.models import OutboxJob, PurchaseIntent

from .conftest import CAREGIVER, PARENT, auth


def decide(client, feature="nextnap", headers=PARENT):
    r = client.post("/v1/capability-decisions", headers=headers, json={"feature": feature})
    assert r.status_code == 200, r.text
    return r.json()


def buy(client, decision, key, headers=PARENT, **extra):
    body = {"decision_id": decision["decision_id"],
            "offer_id": (decision.get("offer") or {}).get("offer_id", "offer-none")} | extra
    return client.post("/v1/purchase-intents", headers=headers | {"Idempotency-Key": key}, json=body)


def set_mount(client, mount):
    r = client.post("/v1/dev/mount", headers=PARENT, json={"mount": mount})
    assert r.status_code == 200, r.text


def intents():
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(PurchaseIntent)).scalar_one()


# ---------------------------------------------------------------- rules (pure)

@pytest.mark.parametrize("feature,plan,mounts", list(itertools.product(
    ["developmental_milestones", "nextnap", "sleep_analysis", "video_history_7d", "unknown_feature"],
    ["none", "sleep", "memories", "milestones", None],
    [["floor_stand"], ["wall_mount"], ["flex_stand"], ["anywhere_stand"], ["flex_stand", "floor_stand"], None])))
def test_every_offer_unlocks_its_feature_and_hardware_blocks_never_get_offers(feature, plan, mounts):
    result = entitlements.evaluate(feature=feature, plan_code=plan, mounts=mounts,
                                   catalog_version="cat-2026-10-public", policy_version="pol-2026-10-public")
    if result["offer"]:
        assert result["status"] == "BLOCKED"
        assert all(b["resolution"] == "PURCHASE" for b in result["blockers"])
        after = entitlements.evaluate(feature=feature, plan_code=result["offer"]["plan_code"], mounts=mounts,
                                      catalog_version="cat-2026-10-public", policy_version="pol-2026-10-public")
        assert after["status"] == "AVAILABLE"
    if any(b["kind"] == "hardware" for b in result["blockers"]) or result["status"] == "UNKNOWN":
        assert result["offer"] is None


def test_developmental_milestones_needs_both_the_plan_and_an_overhead_mount():
    def ev(plan, mounts):
        return entitlements.evaluate(feature="developmental_milestones", plan_code=plan, mounts=mounts,
                                     catalog_version="cat-2026-10-public", policy_version="pol-2026-10-public")
    assert ev("sleep", ["wall_mount"])["offer"]["plan_code"] == "milestones"
    assert ev("sleep", ["wall_mount"])["offer"]["amount_minor"] == 30000
    assert ev("sleep", ["flex_stand"])["offer"] is None
    assert ev("milestones", ["flex_stand"])["status"] == "BLOCKED"
    assert ev("milestones", ["floor_stand"])["status"] == "AVAILABLE"


def test_no_plan_household_is_offered_the_sleep_plan_for_sleep_analysis():
    """Research correction 2026-10-10: Sleep is sold ($100/yr web), so it is the
    cheapest plan that unlocks sleep analysis, not Memories."""
    r = entitlements.evaluate(feature="sleep_analysis", plan_code="none", mounts=["wall_mount"],
                              catalog_version="cat-2026-10-public", policy_version="pol-2026-10-public")
    assert r["offer"]["plan_code"] == "sleep" and r["offer"]["amount_minor"] == 10000


@pytest.mark.parametrize("mount", ["anywhere_stand", "multi_stand"])
def test_essential_and_multi_stand_mounts_block_overhead_features(mount):
    r = entitlements.evaluate(feature="developmental_milestones", plan_code="milestones", mounts=[mount],
                              catalog_version="cat-2026-10-public", policy_version="pol-2026-10-public")
    assert r["status"] == "BLOCKED" and r["offer"] is None
    assert "Anywhere Stand" in r["blockers"][0]["parent_message"] or "Multi-Stand" in r["blockers"][0]["parent_message"]


def test_offer_is_the_cheapest_plan_that_resolves_the_blocker():
    r = entitlements.evaluate(feature="nextnap", plan_code="sleep", mounts=["floor_stand"],
                              catalog_version="cat-2026-10-public", policy_version="pol-2026-10-public")
    assert r["offer"]["plan_code"] == "memories" and r["offer"]["amount_minor"] == 12000
    r = entitlements.evaluate(feature="video_history_7d", plan_code="memories", mounts=["flex_stand"],
                              catalog_version="cat-2026-10-public", policy_version="pol-2026-10-public")
    assert r["offer"]["plan_code"] == "milestones"


# ---------------------------------------------------------------- the demo story

def test_truthful_upgrade_story(client, demo):
    d = decide(client)
    assert d["status"] == "BLOCKED" and [b["code"] for b in d["blockers"]] == ["PLAN_REQUIRED"]
    assert d["offer"]["plan_code"] == "memories" and d["offer"]["simulated"] is True
    set_mount(client, "flex_stand")                          # the camera moves while the offer is open
    r = buy(client, d, "story-key-0001")
    assert r.status_code == 409 and r.json()["code"] == "STALE_DECISION"
    assert "HOUSEHOLD_STATE_CHANGED" in r.json()["detail"]["reasons"]
    assert intents() == 0
    fresh = decide(client)
    assert {b["kind"] for b in fresh["blockers"]} == {"subscription", "hardware"}
    assert fresh["offer"] is None                             # a plan cannot fix the mount
    r = buy(client, fresh, "story-key-0002")
    assert r.status_code == 409 and r.json()["code"] == "NO_ELIGIBLE_OFFER"
    assert intents() == 0


def test_hardware_blocked_decision_cannot_create_a_submission(client, demo):
    set_mount(client, "flex_stand")
    d = decide(client)
    for offer_id in ("offer-memories-year-cat-2026-10-public", "anything"):
        r = client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": f"hw-{offer_id}"},
                        json={"decision_id": d["decision_id"], "offer_id": offer_id})
        assert r.status_code == 409
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(OutboxJob).where(
            OutboxJob.kind == "submit_purchase")).scalar_one() == 0


def test_expired_decision_is_rejected(client, demo):
    d = decide(client)
    with transaction() as db:
        db.execute(text("UPDATE capability_decisions SET expires_at = now() - interval '1 second'"))
    r = buy(client, d, "expired-key-01")
    assert r.status_code == 409 and r.json()["detail"]["reasons"] == ["DECISION_EXPIRED"]


@pytest.mark.parametrize("column,value,reason", [("catalog_version", "cat-next", "CATALOG_CHANGED"),
                                                 ("policy_version", "pol-next", "POLICY_CHANGED")])
def test_catalog_or_policy_change_invalidates_open_decisions(client, demo, column, value, reason):
    d = decide(client)
    with transaction() as db:
        db.execute(text(f"UPDATE commercial_config SET {column} = :v"), {"v": value})
    r = buy(client, d, f"cfg-{column}")
    assert r.status_code == 409 and reason in r.json()["detail"]["reasons"]


def test_client_price_manipulation_is_rejected(client, demo):
    d = decide(client)
    for extra in ({"expected_amount_minor": 1}, {"expected_currency": "EUR"}):
        r = buy(client, d, f"price-{list(extra)[0]}", **extra)
        assert r.status_code == 409 and r.json()["code"] == "TERMS_MISMATCH"
    r = buy(client, d, "price-ok-0001", expected_amount_minor=12000, expected_currency="USD")
    assert r.status_code == 201 and r.json()["terms"]["amount_minor"] == 12000


def test_offer_must_match_the_decision(client, demo):
    d = decide(client)
    r = client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": "mismatch-01"},
                    json={"decision_id": d["decision_id"], "offer_id": "offer-milestones-year-cat-2026-10-public"})
    assert r.status_code == 409 and r.json()["code"] == "OFFER_MISMATCH"


def test_member_without_billing_permission_cannot_buy(client, demo):
    d = decide(client)
    assert buy(client, d, "caregiver-01", headers=CAREGIVER).status_code == 403
    with transaction() as db:
        db.execute(text("UPDATE members SET role='admin', can_bill=false WHERE id=:id"), {"id": seed.CAREGIVER_B})
    assert buy(client, d, "caregiver-02", headers=CAREGIVER).status_code == 403


def test_matching_retry_returns_the_original_intent_even_after_the_decision_expires(client, demo):
    d = decide(client)
    first = buy(client, d, "retry-key-001")
    assert first.status_code == 201
    with transaction() as db:
        db.execute(text("UPDATE capability_decisions SET expires_at = now() - interval '1 second'"))
    again = buy(client, d, "retry-key-001")
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["intent_id"] == first.json()["intent_id"]


def test_same_key_with_different_content_conflicts(client, demo):
    d = decide(client)
    assert buy(client, d, "reuse-key-001").status_code == 201
    r = buy(client, d, "reuse-key-001", expected_amount_minor=12000)
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert intents() == 1


def test_simultaneous_same_key_requests_create_one_intent(client, demo):
    d = decide(client)
    gate = Barrier(8)

    def submit(_):
        gate.wait(timeout=10)
        return buy(client, d, "concurrent-key-1")

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(submit, range(8)))
    assert sorted(r.status_code for r in results) == [200] * 7 + [201], [r.text for r in results]
    assert len({r.json()["intent_id"] for r in results}) == 1
    assert intents() == 1
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(OutboxJob).where(
            OutboxJob.kind == "submit_purchase")).scalar_one() == 1


def test_unique_constraint_is_the_backstop_without_the_lock(demo):
    """Even if a code path forgot the household lock, the database allows one
    intent per household and key."""
    from sqlalchemy.exc import IntegrityError
    with transaction() as db:
        db.execute(text("INSERT INTO capability_decisions (id, household_id, member_id, device_id, feature, status, "
                        "blockers, inputs, state_revision, catalog_version, policy_version, expires_at) VALUES "
                        "('dec_x', 'hh_demo', 'mem_parent_a', 'dev_cam_demo', 'nextnap', 'BLOCKED', '[]', '{}', "
                        "0, 'c', 'p', now())"))
    row = ("INSERT INTO purchase_intents (id, household_id, member_id, decision_id, device_id, offer_id, "
           "idempotency_key, request_fingerprint, terms, state, provider_operation_key) VALUES "
           "(:id, 'hh_demo', 'mem_parent_a', 'dec_x', 'dev_cam_demo', 'o', 'same-key', 'f', '{}', 'FAILED', :op)")
    with transaction() as db:
        db.execute(text(row), {"id": "pi_1", "op": "op_1"})
    with pytest.raises(IntegrityError):
        with transaction() as db:
            db.execute(text(row), {"id": "pi_2", "op": "op_2"})


def test_a_purchase_being_confirmed_is_never_sold_again(client, demo):
    """While a camera has a pending store purchase that would unlock the
    feature, the answer is UNKNOWN with no offer: no paywall for what was just bought."""
    client.post("/v1/dev/claims", headers=PARENT, json={"channel": "apple", "external_ref": "apple-txn-pending-1",
                                                        "event_status": "pending", "plan_code": "milestones"})
    d = decide(client, "developmental_milestones")
    assert d["status"] == "UNKNOWN" and d["offer"] is None
    assert d["blockers"][0]["code"] == "PURCHASE_BEING_CONFIRMED"
    assert buy(client, d, "pending-buy-01").status_code == 409


def test_other_household_tokens_are_separate(client, demo):
    assert decide(client, headers=auth(seed.OTHER_OWNER))["offer"] is None   # flex stand household


def test_database_allows_one_live_upgrade_per_camera(demo):
    """Critic F-01 backstop: even past the application check, a second in-flight
    intent for the same camera cannot be stored; another camera may have its own."""
    from sqlalchemy.exc import IntegrityError
    with transaction() as db:
        db.execute(text("INSERT INTO capability_decisions (id, household_id, member_id, device_id, feature, status, "
                        "blockers, inputs, state_revision, catalog_version, policy_version, expires_at) VALUES "
                        "('dec_live', 'hh_demo', 'mem_parent_a', 'dev_cam_demo', 'nextnap', 'BLOCKED', '[]', '{}', "
                        "0, 'c', 'p', now())"))
    row = ("INSERT INTO purchase_intents (id, household_id, member_id, decision_id, device_id, offer_id, "
           "idempotency_key, request_fingerprint, terms, state, provider_operation_key) VALUES "
           "(:id, 'hh_demo', 'mem_parent_a', 'dec_live', :dev, 'o', :key, 'f', '{}', :state, :op)")
    with transaction() as db:
        db.execute(text(row), {"id": "pi_a", "dev": "dev_cam_demo", "key": "k-a", "state": "SUBMITTED", "op": "op_a"})
        db.execute(text(row), {"id": "pi_b", "dev": "dev_cam_demo", "key": "k-b", "state": "FAILED", "op": "op_b"})
        db.execute(text(row), {"id": "pi_p", "dev": "dev_cam_playroom", "key": "k-p", "state": "PREPARED",
                               "op": "op_p"})
    with pytest.raises(IntegrityError):
        with transaction() as db:
            db.execute(text(row), {"id": "pi_c", "dev": "dev_cam_demo", "key": "k-c", "state": "PREPARED",
                                   "op": "op_c"})
