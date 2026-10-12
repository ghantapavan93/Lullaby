"""Purchase to access.

Invariants
- One canonical status per camera, chosen by fixed precedence from existing
  facts (claims, plans, intents, feature rules) plus purchase evidence.
  Reading it writes nothing.
- A purchase's state depends only on its set of provider events: the highest
  version wins, late older events change nothing, a same-version
  contradiction is CONFLICTING and is never acted on.
- A paid purchase is never sold again: no access status carries an offer or a
  buy action, and capability decisions withhold the offer while a family's
  unconnected purchase would unlock the feature.
- Connecting is safe: household lock, expected state revision, idempotency
  key; a purchase on another camera is never moved; a camera that already has
  the plan from another payment is not given a duplicate; another family's
  purchase reads as missing.
- A refund of a connected purchase revokes it on that camera through the
  normal claim derivation, and the history stays.
- Operation completed, capability verified and customer outcome observed are
  three outcomes with three kinds of evidence, never inferred from each other.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from hypothesis import given, settings, strategies as st
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from types import SimpleNamespace
from app import access, claims, provider, purchase_evidence, scenarios, seed, worker
from app.db import SessionLocal, transaction
from app.models import (EVIDENCE_STATUSES, AccessAction, AccessCheck, AnalyticsEvent, EntitlementClaim, PurchaseEvidence,
                        PurchaseIntent, PurchaseRef)

from .conftest import CAREGIVER, OTHER, PARENT, auth

NURSERY, PLAYROOM, OTHER_CAMERA = seed.CAMERA, seed.PLAYROOM, seed.OTHER_CAMERA
# Relative to today: a fixed start date made every test purchase expire on a calendar date.
START_ISO = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
             - timedelta(days=10)).isoformat()


# ---------------------------------------------------------------- helpers

def evidence(client, ref, *, channel="web", plan="memories", status="verified", version=1, headers=PARENT):
    return client.post("/v1/dev/purchase-evidence", headers=headers, json={
        "channel": channel, "external_ref": ref, "plan_code": plan, "status": status,
        "provider_version": version, "starts_at": START_ISO})


def revision(client, headers=PARENT):
    return client.get("/v1/access", headers=headers).json()["state_revision"]


def bind(client, ref, device=NURSERY, *, channel="web", key=None, rev=None, headers=PARENT):
    return client.post("/v1/access/bind", headers=headers | {"Idempotency-Key": key or f"bind-{device}-{ref}"},
                       json={"device_id": device, "channel": channel, "external_ref": ref,
                             "expected_state_revision": revision(client) if rev is None else rev})


def status(client, device=NURSERY, headers=PARENT, **params):
    return client.get(f"/v1/access/cameras/{device}", headers=headers, params=params).json()


def check(client, ref, device=NURSERY, channel="web", headers=PARENT):
    return client.post("/v1/access/check", headers=headers,
                       json={"device_id": device, "channel": channel, "external_ref": ref})


def verify(client, device=NURSERY, headers=PARENT):
    return client.post("/v1/access/verify", headers=headers, json={"device_id": device})


def counts():
    with SessionLocal() as db:
        return {m.__tablename__: db.execute(select(func.count()).select_from(m)).scalar_one()
                for m in (AccessAction, AccessCheck, PurchaseEvidence, PurchaseRef, EntitlementClaim,
                          PurchaseIntent, AnalyticsEvent)}


def run_scenario(client, key):
    r = client.post("/v1/dev/access-scenario", headers=PARENT, json={"scenario": key})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- the journey

def test_seeded_cameras_have_honest_statuses(client, demo):
    view = client.get("/v1/access", headers=PARENT).json()
    by_id = {c["device"]["id"]: c for c in view["cameras"]}
    assert by_id[NURSERY]["status"] == "ACTIVE_FEATURE_USABLE"          # Sleep trial, Floor Stand
    assert by_id[PLAYROOM]["status"] == "NO_PURCHASE"
    assert [s["key"] for s in by_id[NURSERY]["stages"]] == ["purchase", "account", "camera", "plan", "feature"]
    # A trial is not a purchase, and nothing has checked the feature on this camera yet.
    assert [s["state"] for s in by_id[NURSERY]["stages"]] == ["not_needed", "verified", "verified", "verified",
                                                              "unchecked"]
    assert by_id[NURSERY]["verification"]["last_check"] is None
    client.post("/v1/access/verify", headers=PARENT, json={"device_id": NURSERY}).raise_for_status()
    nursery = next(c for c in client.get("/v1/access", headers=PARENT).json()["cameras"] if c["device"]["id"] == NURSERY)
    assert nursery["stages"][4]["state"] == "verified"                  # a matching check makes it verified


def test_paid_not_active_connects_without_a_new_purchase(client, demo):
    assert evidence(client, "web-ord-1").status_code == 200
    before = status(client)
    assert before["status"] == "VERIFIED_UNBOUND" and before["allowed_actions"] == ["connect"]
    assert [s["state"] for s in before["stages"]][:3] == ["verified", "verified", "action"]

    r = bind(client, "web-ord-1")
    assert r.status_code == 201, r.text
    assert r.json()["operation"] == "completed" and r.json()["capability"] == "not_yet_verified"
    assert status(client)["status"] == "ASSIGNED_PENDING_CONFIRMATION"   # done is not yet verified

    v = verify(client).json()
    assert v["check"]["kind"] == "activation" and v["check"]["status"] == "ACTIVE_FEATURE_USABLE"
    after = v["access"]
    assert after["status"] == "ACTIVE_FEATURE_USABLE" and after["plan"]["plan_code"] == "memories"
    assert "nextnap" in after["usable_features"]
    assert counts()["purchase_intents"] == 0                             # nothing was bought


def test_active_plan_on_wrong_mount_is_not_a_billing_problem(client, demo):
    run_scenario(client, "incompatible_hardware")
    s = status(client)
    assert s["status"] == "ACTIVE_HARDWARE_BLOCKED" and s["offer"] is None
    assert "mount_guide" in s["allowed_actions"] and "connect" not in s["allowed_actions"]
    assert s["stages"][3]["state"] == "verified" and s["stages"][4]["state"] == "blocked"
    assert "wouldn't change this" in s["message"]
    d = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    assert d["offer"] is None and {b["kind"] for b in d["blockers"]} == {"hardware"}


def test_pending_evidence_waits_and_never_offers_a_purchase(client, demo):
    evidence(client, "gpa-1", channel="google", status="pending")
    s = status(client)
    assert s["status"] == "PURCHASE_PENDING" and s["allowed_actions"] == ["check_again"]
    d = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    assert d["offer"] is None
    assert [b["code"] for b in d["blockers"]] == ["PURCHASE_NOT_CONNECTED"]
    assert bind(client, "gpa-1", channel="google").json()["code"] == "PURCHASE_NOT_CONFIRMED"


def test_unconnected_purchase_withholds_the_offer_for_what_it_unlocks_only(client, demo):
    evidence(client, "web-ord-2", plan="memories")
    nextnap = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    assert nextnap["offer"] is None and nextnap["blockers"][0]["resolution"] == "CONNECT"
    # Memories doesn't include developmental milestones: that offer is still honest.
    milestones = client.post("/v1/capability-decisions", headers=PARENT,
                             json={"feature": "developmental_milestones"}).json()
    assert milestones["offer"]["plan_code"] == "milestones"


def test_purchase_bound_to_another_camera_is_not_moved(client, demo):
    run_scenario(client, "paid_wrong_camera")
    s = check(client, "CODE-MEM-2002", channel="activation_code").json()
    assert s["status"] == "ALREADY_ASSIGNED" and s["allowed_actions"] == ["contact_support"]
    r = bind(client, "CODE-MEM-2002", channel="activation_code", key="move-attempt-1")
    assert r.status_code == 409 and r.json()["code"] == "CODE_ALREADY_REDEEMED"
    with SessionLocal() as db:
        rows = db.execute(select(EntitlementClaim.device_id, EntitlementClaim.event_status).where(
            EntitlementClaim.external_ref == "CODE-MEM-2002")).all()
    assert rows == [(PLAYROOM, "active")]


def test_concurrent_connects_of_one_purchase_bind_exactly_once(client, demo):
    evidence(client, "web-ord-race")
    rev = revision(client)
    gate = Barrier(6)

    def go(i):
        gate.wait(timeout=10)
        return bind(client, "web-ord-race", NURSERY if i % 2 else PLAYROOM, key=f"race-key-{i}", rev=rev)

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(go, range(6)))
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1, [r.text for r in results]
    assert set(codes) <= {201, 409}
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(EntitlementClaim).where(
            EntitlementClaim.external_ref == "web-ord-race")).scalar_one() == 1


def test_duplicate_purchase_is_not_connected_to_the_same_camera(client, demo):
    run_scenario(client, "paid_twice")
    s = check(client, "web-ord-5006").json()
    assert s["status"] == "DUPLICATE_PURCHASE" and s["allowed_actions"] == ["contact_support"]
    r = bind(client, "web-ord-5006", key="dup-key-1")
    assert r.status_code == 409 and r.json()["code"] == "CAMERA_ALREADY_HAS_PLAN"


def test_a_trial_does_not_make_a_paid_purchase_a_duplicate(client, demo):
    evidence(client, "web-ord-sleep", plan="sleep")                     # nursery is on the Sleep trial
    assert check(client, "web-ord-sleep").json()["status"] == "VERIFIED_UNBOUND"
    assert bind(client, "web-ord-sleep").status_code == 201


def test_refund_after_activation_revokes_access_and_keeps_history(client, demo):
    run_scenario(client, "access_lost_after_refund")
    s = status(client)
    assert s["status"] == "REFUNDED_OR_REVOKED" and s["offer"] is None
    assert s["allowed_actions"] == ["contact_support"]
    assert s["plan"]["plan_code"] == "sleep"                             # the trial still stands
    with SessionLocal() as db:
        statuses = db.execute(select(EntitlementClaim.event_status).where(
            EntitlementClaim.external_ref == "web-ord-6006").order_by(EntitlementClaim.received_at)).scalars().all()
        events = db.execute(select(func.count()).select_from(PurchaseEvidence).where(
            PurchaseEvidence.external_ref == "web-ord-6006")).scalar_one()
    assert statuses == ["active", "revoked"] and events == 2
    w = client.get(f"/v1/access/cameras/{NURSERY}/window", headers=PARENT).json()
    assert w["decision"]["state"] == "ACCESS_LOST" and "nextnap" in w["live"]["verdict"]["lost"]


def test_store_active_after_a_refund_is_a_conflict_not_a_reactivation(client, demo):
    run_scenario(client, "access_lost_after_refund")
    evidence(client, "web-ord-6006", status="verified", version=3)
    assert status(client)["status"] == "CONFLICTING_EVIDENCE"
    r = bind(client, "web-ord-6006", key="reactivate-1")
    assert r.status_code == 409 and r.json()["code"] == "PURCHASE_EVIDENCE_CONFLICT"
    assert status(client)["plan"]["plan_code"] == "sleep"


# ---------------------------------------------------------------- provider evidence

def test_late_older_provider_event_changes_nothing(client, demo):
    evidence(client, "web-ord-3", status="verified", version=2)
    rev = revision(client)
    evidence(client, "web-ord-3", status="pending", version=1)          # delivered late
    assert status(client)["status"] == "VERIFIED_UNBOUND"
    assert revision(client) == rev                                       # nothing a parent sees changed


def test_same_version_contradiction_is_conflicting_and_cannot_be_connected(client, demo):
    evidence(client, "web-ord-4", status="verified", version=1)
    evidence(client, "web-ord-4", status="refunded", version=1)
    s = status(client)
    assert s["status"] == "CONFLICTING_EVIDENCE" and s["stages"][0]["state"] == "conflicting"
    assert bind(client, "web-ord-4").json()["code"] == "PURCHASE_EVIDENCE_CONFLICT"


def test_provider_event_replay_is_a_noop_and_changed_replay_is_refused(client, demo):
    first = evidence(client, "web-ord-5").json()
    again = evidence(client, "web-ord-5").json()
    assert again["replayed"] and again["evidence_id"] == first["evidence_id"]
    changed = evidence(client, "web-ord-5", plan="milestones")
    assert changed.status_code == 409 and changed.json()["code"] == "EVIDENCE_CONFLICT"


def test_unverified_purchase_is_explained_and_cannot_be_connected(client, demo):
    evidence(client, "apple-6", channel="apple", status="unverified")
    s = status(client)
    assert s["status"] == "PURCHASE_UNVERIFIED" and "contact_support" in s["allowed_actions"]
    assert bind(client, "apple-6", channel="apple").json()["code"] == "PURCHASE_UNVERIFIED"


def test_another_familys_purchase_cannot_be_recorded_or_seen(client, demo):
    evidence(client, "web-ord-theirs", headers=OTHER)
    r = evidence(client, "web-ord-theirs")
    assert r.status_code == 409 and r.json()["code"] == "PURCHASE_OWNED_ELSEWHERE"
    missing = check(client, "web-ord-never-existed").json()
    theirs = check(client, "web-ord-theirs").json()
    assert theirs["status"] == missing["status"] == "PURCHASE_NOT_FOUND"
    assert theirs["message"] == missing["message"]
    assert bind(client, "web-ord-theirs").status_code == 404


def test_database_refuses_evidence_under_a_household_that_does_not_own_the_purchase(demo):
    with transaction() as db:
        db.add(PurchaseRef(channel="web", external_ref="web-owned-a", household_id=seed.HOUSEHOLD))
    with pytest.raises(IntegrityError), transaction() as db:
        db.add(PurchaseEvidence(id="pev_x", household_id=seed.OTHER, channel="web", external_ref="web-owned-a",
                                plan_code="sleep", status="verified", provider_version=1,
                                starts_at=datetime(2026, 10, 1, tzinfo=timezone.utc), request_hash="x"))


@pytest.mark.parametrize("table", ["purchase_evidence", "access_actions", "access_checks"])
def test_access_records_are_append_only(client, demo, table):
    evidence(client, "web-ord-7")
    bind(client, "web-ord-7")
    verify(client)
    with pytest.raises(DBAPIError), transaction() as db:
        db.execute(text(f"UPDATE {table} SET household_id = household_id"))
    with pytest.raises(DBAPIError), transaction() as db:
        db.execute(text(f"DELETE FROM {table}"))


class Event:
    def __init__(self, status, version, plan="memories"):
        self.channel, self.external_ref, self.plan_code = "web", "r1", plan
        self.status, self.provider_version, self.provider_event = status, version, None
        self.starts_at, self.ends_at = datetime(2026, 10, 1, tzinfo=timezone.utc), None


provider_events = st.lists(st.builds(Event, st.sampled_from(EVIDENCE_STATUSES),
                                     st.integers(1, 4)), min_size=1, max_size=8)


@settings(max_examples=300, deadline=None)
@given(provider_events, st.randoms())
def test_purchase_state_depends_only_on_the_set_of_events(events, rnd):
    shuffled = list(events) + list(events[: len(events) // 2])          # duplicates of a prefix
    rnd.shuffle(shuffled)
    a, b = purchase_evidence.purchase_state(events), purchase_evidence.purchase_state(shuffled)
    assert {k: a[k] for k in ("state", "provider_version", "conflicting_statuses")} == \
           {k: b[k] for k in ("state", "provider_version", "conflicting_statuses")}
    top = max(e.provider_version for e in events)
    at_top = {e.status for e in events if e.provider_version == top}
    assert a["state"] == (at_top.pop() if len(at_top) == 1 else "conflicting")


# ---------------------------------------------------------------- command safety

def test_stale_view_is_refused_with_the_current_revision(client, demo):
    evidence(client, "web-ord-8")
    seen = revision(client)
    client.post("/v1/dev/mount", headers=PARENT, json={"mount": "wall_mount"})   # the family changed something
    r = bind(client, "web-ord-8", rev=seen)
    assert r.status_code == 409 and r.json()["code"] == "STALE_ACCESS_VIEW"
    assert r.json()["detail"]["current_state_revision"] == revision(client)
    assert counts()["access_actions"] == 0


def test_replay_returns_the_same_outcome_and_key_reuse_is_refused(client, demo):
    evidence(client, "web-ord-9")
    evidence(client, "web-ord-10", plan="milestones")
    rev = revision(client)
    first = bind(client, "web-ord-9", key="replay-key-1", rev=rev)
    again = bind(client, "web-ord-9", key="replay-key-1", rev=rev)
    assert first.status_code == 201 and again.status_code == 200 and again.json()["replayed"]
    assert again.json()["claim_id"] == first.json()["claim_id"]
    other = bind(client, "web-ord-10", key="replay-key-1", rev=revision(client))
    assert other.status_code == 409 and other.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert counts()["access_actions"] == 1


def test_caregiver_sees_status_but_cannot_connect_or_see_purchases(client, demo):
    evidence(client, "web-ord-11")
    view = client.get("/v1/access", headers=CAREGIVER).json()
    nursery = next(c for c in view["cameras"] if c["device"]["id"] == NURSERY)
    assert nursery["status"] == "VERIFIED_UNBOUND" and nursery["allowed_actions"] == ["ask_owner"]
    assert nursery["stages"][1]["state"] == "action"
    assert view["purchases"] == [] and "focus_purchase" not in nursery and "trace" not in nursery
    assert bind(client, "web-ord-11", headers=CAREGIVER).status_code == 403
    assert check(client, "web-ord-11", headers=CAREGIVER).status_code == 403


def test_revoked_caregiver_cannot_read_or_verify(client, demo):
    with transaction() as db:
        db.execute(text("UPDATE members SET revoked_at = now() WHERE id = :m"), {"m": seed.CAREGIVER_B})
    assert client.get("/v1/access", headers=CAREGIVER).status_code == 403
    assert verify(client, headers=CAREGIVER).status_code == 403


def test_another_familys_camera_is_not_found(client, demo):
    evidence(client, "web-ord-12")
    assert client.get(f"/v1/access/cameras/{OTHER_CAMERA}", headers=PARENT).status_code == 404
    assert bind(client, "web-ord-12", device=OTHER_CAMERA).status_code == 404
    assert verify(client, device=OTHER_CAMERA).status_code == 404


def test_reading_access_writes_nothing_but_the_authorization_record(client, demo):
    run_scenario(client, "paid_not_active")
    before = counts()
    for _ in range(3):
        client.get("/v1/access", headers=PARENT)
        status(client)
        status(client, feature="nextnap")
        check(client, "web-ord-1001")
        client.get(f"/v1/access/cameras/{NURSERY}/window", headers=PARENT)
        client.get(f"/v1/access/cameras/{NURSERY}/outcome", headers=PARENT)
    assert counts() == before


# ---------------------------------------------------------------- commercial protection across every state

@pytest.mark.parametrize("key", list(scenarios.SCENARIOS))
def test_every_scenario_reaches_its_expected_status_and_never_sells(client, demo, key):
    built = run_scenario(client, key)
    focus = built["focus"]
    for device, expected in built["expected"].items():
        if device == focus["device_id"] and "external_ref" in focus:
            got = check(client, focus["external_ref"], device, focus["channel"]).json()
        else:
            got = status(client, device)
        assert got["status"] == expected, (key, device, got["status"], got["reason"])
    for camera in client.get("/v1/access", headers=PARENT).json()["cameras"]:
        full = status(client, camera["device"]["id"])
        assert full["offer"] is None
        assert not {"buy", "purchase", "upgrade"} & set(full["allowed_actions"])
        assert "buy" not in full["message"].lower() or "nothing more to buy" in full["message"].lower()


def test_trace_follows_the_twelve_documented_steps(client, demo):
    run_scenario(client, "paid_not_active")
    trace = status(client)["trace"]
    assert [t["step"] for t in trace] == list(range(1, 13))
    assert trace[-1]["rule"] == "returned; nothing written"


# ---------------------------------------------------------------- three outcomes

def test_three_outcomes_are_never_inferred_from_one_another(client, demo):
    def tiers():
        body = client.get(f"/v1/access/cameras/{NURSERY}/outcome", headers=PARENT).json()
        return {t["key"]: t["state"] for t in body["tiers"]}

    def opened(event_id):
        return client.post("/v1/analytics", headers=PARENT, json={
            "event_id": event_id, "name": "access_feature_opened",
            "properties": {"device_id": NURSERY, "feature": "nextnap"}})

    evidence(client, "web-ord-13")
    assert set(tiers().values()) == {"not_observed"}
    bind(client, "web-ord-13")
    opened("open-before-verify-1")                         # a click before verification proves nothing
    assert tiers() == {"operation_completed": "observed", "capability_verified": "not_observed",
                       "customer_outcome_observed": "not_observed"}
    verify(client)
    assert tiers()["capability_verified"] == "observed"
    assert tiers()["customer_outcome_observed"] == "not_observed"
    opened("open-after-verify-1")
    assert set(tiers().values()) == {"observed"}


def test_opening_a_feature_after_access_was_lost_is_not_a_customer_outcome(client, demo):
    run_scenario(client, "access_lost_after_refund")                    # connected, verified, then refunded
    assert "nextnap" not in status(client)["usable_features"]
    client.post("/v1/analytics", headers=PARENT, json={
        "event_id": "open-after-refund-1", "name": "access_feature_opened",
        "properties": {"device_id": NURSERY, "feature": "nextnap"}})
    tiers = {t["key"]: t["state"] for t in
             client.get(f"/v1/access/cameras/{NURSERY}/outcome", headers=PARENT).json()["tiers"]}
    assert tiers["customer_outcome_observed"] == "not_observed"
    forged = client.post("/v1/analytics", headers=PARENT, json={         # the stamp is the server's, never the client's
        "event_id": "open-forged-1", "name": "access_feature_opened",
        "properties": {"device_id": NURSERY, "feature": "nextnap", "usable_when_opened": True}})
    assert forged.status_code == 422 and forged.json()["code"] == "PROPERTY_NOT_ALLOWED"


def test_server_access_events_cannot_be_posted_by_clients(client, demo):
    for name in ("camera_entitlement_resolved", "feature_access_verified", "activation_action_submitted"):
        r = client.post("/v1/analytics", headers=PARENT, json={
            "event_id": f"forged-{name}", "name": name, "properties": {"device_id": NURSERY}})
        assert r.status_code == 422 and r.json()["code"] == "SERVER_ONLY_EVENT"


def test_connect_and_verify_emit_server_events_once(client, demo):
    evidence(client, "web-ord-14")
    bind(client, "web-ord-14", key="events-key-1")
    bind(client, "web-ord-14", key="events-key-1")         # replay: no second event
    verify(client)
    with SessionLocal() as db:
        names = db.execute(select(AnalyticsEvent.name).where(
            AnalyticsEvent.name.in_(["activation_action_submitted", "camera_entitlement_resolved",
                                     "feature_access_verified"]))).scalars().all()
    assert sorted(names) == ["activation_action_submitted", "camera_entitlement_resolved", "feature_access_verified"]


# ---------------------------------------------------------------- verification window

def test_window_holds_until_the_mount_changes(client, demo):
    evidence(client, "web-ord-15")
    bind(client, "web-ord-15")
    verify(client)
    verify(client)
    w = client.get(f"/v1/access/cameras/{NURSERY}/window", headers=PARENT).json()
    assert w["decision"]["state"] == "HOLDING" and len(w["observations"]) == 2
    client.post("/v1/dev/mount", headers=PARENT, json={"mount": "flex_stand"})
    w = client.get(f"/v1/access/cameras/{NURSERY}/window", headers=PARENT).json()
    assert w["live"]["changed_since_last_check"] and w["decision"]["state"] == "ACCESS_LOST"
    assert status(client)["status"] == "ACTIVE_HARDWARE_BLOCKED"


def test_repeated_activation_is_recorded_but_does_not_reopen_verification(client, demo):
    evidence(client, "web-ord-17")
    bind(client, "web-ord-17")
    verify(client)
    again = bind(client, "web-ord-17", key="repeat-activation-1")
    assert again.status_code == 201 and again.json()["already_connected"]
    assert counts()["access_actions"] == 2
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"
    w = client.get(f"/v1/access/cameras/{NURSERY}/window", headers=PARENT).json()
    assert w["decision"]["state"] == "HOLDING"


def test_reassignment_attempt_leaves_verified_access_unchanged(client, demo):
    evidence(client, "web-ord-16")
    bind(client, "web-ord-16")
    verify(client)
    assert bind(client, "web-ord-16", device=PLAYROOM, key="reassign-1").status_code == 409
    w = client.get(f"/v1/access/cameras/{NURSERY}/window", headers=PARENT).json()
    assert w["decision"]["state"] == "HOLDING"


# ---------------------------------------------------------------- recovery

def test_worker_crash_recovers_to_one_charge_and_usable_access(client, demo, monkeypatch):
    run_scenario(client, "worker_crash")
    # An always-on worker leaves the held job alone, so the crash can still be shown
    # (bug hunt, 2026-10-12: the deployed worker finished it first, 1 run in 4).
    assert worker.run_once("always-on") == 0
    monkeypatch.setenv("PROOF_LAYER_INPROCESS_CRASH", "1")
    with pytest.raises(worker.SimulatedCrash):
        worker.run_once("t", crash_after_provider=True, include_held=True)
    pending = status(client)
    assert pending["status"] == "PURCHASE_PENDING"                       # paid at the provider, not yet here
    # The copy names the plan being bought, not the camera's current one (bug hunt, 2026-10-12).
    assert "Memories purchase" in pending["message"] and "Sleep purchase" not in pending["message"]
    assert "A Memories purchase" in next(s["fact"] for s in pending["stages"] if s["key"] == "purchase")
    with transaction() as db:
        db.execute(text("UPDATE outbox SET lease_until = now() - interval '1 second', available_at = now() "
                        "WHERE state IN ('in_progress', 'pending')"))
    worker.reconcile_intents("t")
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"
    assert status(client)["plan"]["plan_code"] == "memories"
    assert len(provider.ledger(seed.HOUSEHOLD)) == 1


# ---------------------------------------------------------------- renewals and ended plans
# A customer-reported symptom: "renewed, but the app says I need to subscribe".
# These are synthetic representations of the symptom, not of Nanit's internal
# cause, which is not public.

def days(n):
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (today + timedelta(days=n)).isoformat()


def evidence_period(client, ref, start, end, *, version=1, status="verified", channel="apple", plan="memories",
                    headers=PARENT):
    span = (datetime.fromisoformat(days(end)) - datetime.fromisoformat(days(start))).days
    return client.post("/v1/dev/purchase-evidence", headers=headers, json={
        "channel": channel, "external_ref": ref, "plan_code": plan, "status": status,
        "provider_version": version, "starts_at": days(start), "days": span})


def claims_for(ref):
    with SessionLocal() as db:
        return db.execute(select(EntitlementClaim.device_id, EntitlementClaim.event_status,
                                 EntitlementClaim.starts_at).where(EntitlementClaim.external_ref == ref)
                          .order_by(EntitlementClaim.starts_at)).all()


def test_renewal_of_a_connected_purchase_keeps_the_camera_active(client, demo):
    run_scenario(client, "renewal_reflected")
    s = status(client)
    assert s["status"] == "ACTIVE_FEATURE_USABLE" and s["plan"]["plan_code"] == "memories"
    rows = claims_for("apple-txn-7007")
    assert [(d, st) for d, st, _ in rows] == [(NURSERY, "active"), (NURSERY, "active")]   # two periods, one camera
    assert counts()["access_actions"] == 0                                              # no new connect needed


def test_a_renewal_delivered_twice_adds_one_period(client, demo):
    run_scenario(client, "renewal_reflected")
    again = evidence_period(client, "apple-txn-7007", -5, 360, version=2)
    assert again.json()["replayed"] and len(claims_for("apple-txn-7007")) == 2


def test_an_ended_plan_without_renewal_says_so_and_never_sells(client, demo):
    run_scenario(client, "plan_ended")
    s = status(client)
    assert s["status"] == "PLAN_EXPIRED" and s["offer"] is None
    assert s["allowed_actions"] == ["check_again", "contact_support"]
    assert "ended" in s["message"] and "renewal" in s["message"]
    assert s["plan"]["plan_code"] == "sleep"                       # the trial is unaffected


def test_a_late_renewal_brings_an_ended_plan_back_without_a_connect(client, demo):
    run_scenario(client, "plan_ended")
    r = evidence_period(client, "apple-txn-8008", -5, 360, version=2)
    assert r.status_code == 200 and r.json()["renewed_claim"]
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"


def test_an_ended_purchase_cannot_be_connected(client, demo):
    evidence_period(client, "web-ord-ended", -400, -30, channel="web")
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"     # history, not a plan to connect
    assert check(client, "web-ord-ended").json()["status"] == "PLAN_EXPIRED"
    r = bind(client, "web-ord-ended")
    assert r.status_code == 409 and r.json()["code"] == "PURCHASE_EXPIRED"


def test_a_refunded_purchase_is_not_renewed(client, demo):
    run_scenario(client, "access_lost_after_refund")
    before = len(claims_for("web-ord-6006"))
    r = client.post("/v1/dev/purchase-evidence", headers=PARENT, json={
        "channel": "web", "external_ref": "web-ord-6006", "plan_code": "memories", "status": "verified",
        "provider_version": 3, "starts_at": days(300), "days": 365})
    assert r.status_code == 200 and r.json()["renewed_claim"] is None
    assert len(claims_for("web-ord-6006")) == before
    assert status(client)["status"] == "CONFLICTING_EVIDENCE"


@pytest.mark.parametrize("channel", ["web", "apple", "activation_code"])
def test_database_keeps_one_purchase_on_one_camera_in_every_channel(demo, channel):
    """Without the application's checks, the exclusion constraint alone refuses
    a second camera for the same purchase, whatever the period."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with transaction() as db:
        db.add(EntitlementClaim(id="clm_x1", household_id=seed.HOUSEHOLD, device_id=NURSERY, channel=channel,
                                external_ref="ref-one-camera", event_status="active", plan_code="sleep",
                                starts_at=start, ends_at=start + timedelta(days=30), request_hash="a"))
    with pytest.raises(IntegrityError), transaction() as db:
        db.add(EntitlementClaim(id="clm_x2", household_id=seed.HOUSEHOLD, device_id=PLAYROOM, channel=channel,
                                external_ref="ref-one-camera", event_status="active", plan_code="sleep",
                                starts_at=start + timedelta(days=30), ends_at=start + timedelta(days=60),
                                request_hash="b"))


def test_a_higher_plan_from_another_channel_is_connected_not_refused(client, demo):
    """A reported symptom: a camera on an Apple Memories plan couldn't take a web
    Milestones plan. Here the higher plan connects and both purchases stay on record."""
    run_scenario(client, "upgrade_across_channels")
    assert status(client)["status"] == "VERIFIED_UNBOUND"
    r = bind(client, "web-ord-9010", key="upgrade-web-1")
    assert r.status_code == 201 and r.json()["camera_plan_after"] == "milestones"
    plan = client.get("/v1/household/commercial", headers=PARENT).json()["devices"][0]["plan"]
    assert plan["plan_code"] == "milestones" and plan["member_cap"] == 50
    assert {t["external_ref"] for t in plan["transactions"] if t["state"] == "active"} >= {"apple-txn-9009", "web-ord-9010"}


# ---------------------------------------------------------------- found by the 2026-10-11 critique
# (audits/RESEARCH_AND_ENGINEERING_CRITIQUE_2026-10-11.md)

def test_a_cheaper_plan_at_renewal_takes_effect_when_its_period_starts():
    """A Milestones year renewed as Sleep: once the Sleep period covers now, the
    camera has Sleep (and its team limit), not the highest plan ever held."""
    from app import entitlements
    from types import SimpleNamespace as C
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    year1 = C(channel="apple", external_ref="r", event_status="active", plan_code="milestones",
              starts_at=now - timedelta(days=400), ends_at=now - timedelta(days=35))
    year2 = C(channel="apple", external_ref="r", event_status="active", plan_code="sleep",
              starts_at=now - timedelta(days=35), ends_at=now + timedelta(days=330))
    plan = entitlements.effective_plan([year1, year2], now, "cat-2026-10-public")
    assert plan["plan_code"] == "sleep" and plan["member_cap"] == 2


def test_a_period_that_has_not_started_is_scheduled_not_ended():
    from app import entitlements
    from types import SimpleNamespace as C
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    later = C(channel="apple", external_ref="r", event_status="active", plan_code="memories",
              starts_at=now + timedelta(days=3), ends_at=now + timedelta(days=368))
    t = entitlements.effective_plan([later], now, "cat-2026-10-public")["transactions"][0]
    assert t["state"] == "scheduled" and t["last_ended"] is None


def test_an_extension_with_the_same_start_reaches_the_camera(client, demo):
    run_scenario(client, "plan_ended")                        # period (-370, -5), connected a year ago
    r = evidence_period(client, "apple-txn-8008", -370, 360, version=2)
    assert r.status_code == 200 and r.json()["renewed_claim"]
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"


def test_an_older_period_that_arrives_late_is_still_written(client, demo):
    evidence_period(client, "apple-late-1", 355, 720, version=2)        # next year first
    assert bind(client, "apple-late-1", channel="apple").status_code == 201
    evidence_period(client, "apple-late-1", -10, 355, version=1)        # the current year, late
    verify(client)
    s = status(client)
    assert s["status"] == "ACTIVE_FEATURE_USABLE" and s["plan"]["plan_code"] == "memories"


def test_another_household_cannot_put_this_familys_purchase_on_its_camera(client, demo):
    evidence(client, "web-ord-mine")
    r = client.post("/v1/dev/claims", headers=OTHER, json={"channel": "web", "external_ref": "web-ord-mine",
                                                           "event_status": "active", "plan_code": "memories"})
    assert r.status_code == 409 and r.json()["code"] == "PURCHASE_OWNED_ELSEWHERE"
    assert bind(client, "web-ord-mine").status_code == 201


def test_the_same_instant_in_another_offset_is_a_replay(client, demo):
    first = client.post("/v1/dev/purchase-evidence", headers=PARENT, json={
        "channel": "web", "external_ref": "web-ord-tz", "plan_code": "sleep", "status": "verified",
        "provider_version": 1, "starts_at": "2026-10-01T04:00:00+00:00"}).json()
    again = client.post("/v1/dev/purchase-evidence", headers=PARENT, json={
        "channel": "web", "external_ref": "web-ord-tz", "plan_code": "sleep", "status": "verified",
        "provider_version": 1, "starts_at": "2026-10-01T00:00:00-04:00"})
    assert again.status_code == 200 and again.json()["replayed"] and again.json()["evidence_id"] == first["evidence_id"]


def test_a_refund_after_connect_is_not_hidden_behind_checking(client, demo):
    evidence(client, "web-ord-ref")
    bind(client, "web-ord-ref")
    assert status(client)["status"] == "ASSIGNED_PENDING_CONFIRMATION"
    evidence(client, "web-ord-ref", status="refunded", version=2)
    assert status(client)["status"] == "REFUNDED_OR_REVOKED"


def test_checking_names_the_plan_that_was_connected(client, demo):
    evidence(client, "web-ord-name")                          # Memories, while the camera is on a Sleep trial
    bind(client, "web-ord-name")
    s = status(client)
    assert s["status"] == "ASSIGNED_PENDING_CONFIRMATION" and s["message"].startswith("Memories is connected")


def test_an_ended_plan_without_a_store_record_names_its_own_plan_and_date(client, demo):
    start = days(-370)
    client.post("/v1/dev/claims", headers=PARENT, json={"channel": "apple", "external_ref": "apple-claim-only",
                                                        "event_status": "active", "plan_code": "memories",
                                                        "starts_at": start, "days": 365})
    s = status(client)
    assert s["status"] == "PLAN_EXPIRED"
    ended = datetime.fromisoformat(days(-5))
    assert s["message"].startswith(f"Memories on Nursery camera ended on {ended:%b} {ended.day}, {ended.year}")


def test_an_old_ending_is_history_not_the_headline(client, demo):
    client.post("/v1/dev/claims", headers=PARENT, json={"channel": "apple", "external_ref": "apple-old-end",
                                                        "event_status": "active", "plan_code": "memories",
                                                        "starts_at": days(-425), "days": 365})   # ended 60 days ago
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"


def test_a_trial_does_not_hide_a_paid_plan_waiting_to_be_connected(client, demo):
    evidence(client, "web-ord-sleep-paid", plan="sleep")      # the camera is on a Sleep trial
    s = status(client)
    assert s["status"] == "VERIFIED_UNBOUND" and s["allowed_actions"] == ["connect"]


def test_a_window_whose_activation_found_nothing_usable_is_not_holding():
    """Blind review, 2026-10-12: Growth Lab showed a green "what was verified still
    holds" when the activation check had found nothing usable on the camera."""
    base = SimpleNamespace(status="ACTIVE_HARDWARE_BLOCKED", usable_features=[], fingerprint="f1", plan_code="memories")
    still = {"state": "still_verified", "lost": [], "gained": []}
    decision = access._window_decision(base, [], still, {})
    assert decision["state"] == "NOTHING_USABLE" and "nothing usable" in decision["reason"]


def test_the_headline_says_plan_active_until_a_check_verifies_the_camera(client, demo):
    """Final deployed review, 2026-10-12: Home said "Ready to use" while the same
    card's feature stage said no check had run. Plan active, feature eligible and
    feature verified are different facts; the headline names only what is known."""
    before = status(client)
    assert before["status"] == "ACTIVE_FEATURE_USABLE"
    assert before["headline"] == "Plan active" and "erified" not in before["headline"]
    assert verify(client).status_code == 200
    after = status(client)
    assert after["headline"] == "Verified on this camera"
