"""Store billing lifecycle: grace, billing retry, pause, recovery and expiry.

Provider semantics come from docs/PROVIDER_LIFECYCLE_FACTS.md (official Apple
and Google documentation, checked 2026-10-11). The rules under test:

- a grace period keeps access until the provider's grace end, and tells the
  parent the payment needs attention;
- billing retry outside grace (Apple) and account hold (Google) grant no
  access, keep the purchase on its camera, and never ask the parent to buy
  again;
- recovery restores access on the same camera without a new connect. Google
  keeps the renewal date after a grace recovery and resets it after a hold;
- a paused plan grants no access and says when it resumes;
- a store-reported expiry says the store reported it, instead of "no renewal
  reported";
- a refund or revoke ends access at once, grace or not;
- the provider's version decides: a late grace event cannot undo a recovery.

Synthetic purchases and a frozen clock; no store is contacted.
"""
import pytest

from app import provider_lifecycle
from app.engine import DomainError

from .conftest import PARENT
from .test_subscription_model import (NURSERY, actions_count, at, connect, day, entitlement, human,  # noqa: F401
                                      rows, status, verify)


def store(client, ref, event, start, end, *, version=1, plan="memories", channel="apple"):
    """A synthetic store notification, translated by app.provider_lifecycle."""
    return client.post("/v1/dev/purchase-evidence", headers=PARENT, json={
        "channel": channel, "external_ref": ref, "plan_code": plan, "provider_event": event,
        "provider_version": version, "starts_at": day(start).isoformat(), "days": int(end - start)})


def subscribed(client, ref, *, channel="apple", event="SUBSCRIBED/INITIAL_BUY"):
    """A year that ends on day 5, connected to the nursery camera and verified."""
    assert store(client, ref, event, -360, 5, channel=channel).status_code == 200
    assert connect(client, ref, channel=channel).status_code == 201
    verify(client)


# ---------------------------------------------------------------- grace

def test_apple_grace_keeps_access_until_the_grace_end(client, at):
    subscribed(client, "apple-grace")
    r = store(client, "apple-grace", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 21, version=2)
    assert r.status_code == 200 and r.json()["after"]["state"] == "grace"
    at(10)                                                     # the paid year ended; grace runs to day 21
    s = status(client)
    assert s["status"] == "BILLING_GRACE" and s["offer"] is None
    assert entitlement()["plan_code"] == "memories" and "nextnap" in s["usable_features"]
    assert f"through {human(day(20))}" in s["message"] and "nothing more to buy" in s["message"]
    assert s["allowed_actions"] == ["check_again"]
    assert [st for st in (stage["state"] for stage in s["stages"])] == ["action", "verified", "verified",
                                                                       "verified", "verified"]
    grace = [p for p in s["periods"] if p.get("grace")]
    assert len(grace) == 1 and grace[0]["state"] == "active" and not grace[0]["renewal"]

    at(22)                                                     # grace over and the store is still retrying
    s = status(client)
    assert s["status"] == "BILLING_RETRY" and entitlement()["plan_code"] != "memories"
    assert "nextnap" not in s["usable_features"] and s["offer"] is None


def test_a_purchase_in_grace_can_still_be_connected(client, at):
    store(client, "apple-late-connect", "SUBSCRIBED/INITIAL_BUY", -360, 5)
    store(client, "apple-late-connect", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 21, version=2)
    at(10)
    assert status(client)["status"] == "VERIFIED_UNBOUND"     # the store still grants access: connect it
    assert connect(client, "apple-late-connect").status_code == 201
    verify(client)
    assert status(client)["status"] == "BILLING_GRACE"
    assert {(s, e) for _, _, _, s, e in rows("apple-late-connect")} == {(day(-360), day(5)), (day(5), day(21))}


# ---------------------------------------------------------------- billing retry and recovery

def test_billing_retry_without_grace_removes_access_but_keeps_the_camera(client, at):
    subscribed(client, "apple-retry", event="DID_RENEW")
    r = store(client, "apple-retry", "DID_FAIL_TO_RENEW", 5, 65, version=2)   # no subtype: no grace
    assert r.json()["after"]["state"] == "billing_retry"
    assert store(client, "apple-retry", "DID_FAIL_TO_RENEW", 5, 65, version=2).json()["replayed"] is True
    at(6)
    s = status(client)
    assert s["status"] == "BILLING_RETRY" and s["offer"] is None
    assert entitlement()["plan_code"] != "memories" and "nextnap" not in s["usable_features"]
    assert "nothing more to buy" in s["message"] and "retrying" in s["message"]
    assert s["allowed_actions"] == ["check_again", "contact_support"]
    assert [st for _, st, _, _, _ in rows("apple-retry")] == ["active"]       # still on its camera, not revoked
    again = connect(client, "apple-retry", key="connect-again-retry")
    assert again.status_code == 409 and again.json()["code"] == "PURCHASE_PAYMENT_RETRYING"


def test_billing_recovery_restores_access_on_the_same_camera(client, at):
    subscribed(client, "apple-recover")
    store(client, "apple-recover", "DID_FAIL_TO_RENEW", 5, 65, version=2)
    at(8)
    assert status(client)["status"] == "BILLING_RETRY"
    r = store(client, "apple-recover", "DID_RENEW/BILLING_RECOVERY", 8, 373, version=3)
    assert r.status_code == 200 and r.json()["renewed_claim"]
    s = status(client)
    assert s["status"] == "ACTIVE_FEATURE_USABLE" and "nextnap" in s["usable_features"]
    assert actions_count() == 1                                # no second connect
    assert {(s, e) for _, _, _, s, e in rows("apple-recover")} == {(day(-360), day(5)), (day(8), day(373))}


def test_google_hold_recovery_resets_the_renewal_date(client, at):
    subscribed(client, "gpa-hold", channel="google", event="SUBSCRIPTION_PURCHASED")
    store(client, "gpa-hold", "SUBSCRIPTION_IN_GRACE_PERIOD", 5, 12, version=2, channel="google")
    at(13)
    assert status(client)["status"] == "BILLING_RETRY"        # grace ended before the hold notice arrived
    store(client, "gpa-hold", "SUBSCRIPTION_ON_HOLD", 12, 65, version=3, channel="google")
    assert status(client)["status"] == "BILLING_RETRY"
    at(20)
    store(client, "gpa-hold", "SUBSCRIPTION_RECOVERED", 20, 385, version=4, channel="google")
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"
    covering = next(t for t in entitlement()["transactions"] if t["external_ref"] == "gpa-hold")
    assert covering["ends_at"] == day(385).isoformat()         # billed from the recovery date


def test_google_grace_recovery_keeps_the_renewal_date(client, at):
    subscribed(client, "gpa-grace", channel="google", event="SUBSCRIPTION_PURCHASED")
    store(client, "gpa-grace", "SUBSCRIPTION_IN_GRACE_PERIOD", 5, 12, version=2, channel="google")
    at(8)
    store(client, "gpa-grace", "SUBSCRIPTION_RENEWED", 5, 370, version=3, channel="google")
    s = status(client)
    assert s["status"] == "ACTIVE_FEATURE_USABLE"
    assert (day(5), day(370)) in {(s_, e) for _, _, _, s_, e in rows("gpa-grace")}   # the original renewal date


def test_a_late_grace_event_does_not_undo_a_recovery(client, at):
    subscribed(client, "apple-order")
    at(8)
    store(client, "apple-order", "DID_RENEW/BILLING_RECOVERY", 8, 373, version=3)
    late = store(client, "apple-order", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 21, version=2)
    assert late.status_code == 200 and late.json()["after"]["state"] == "verified"
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"
    assert (day(5), day(21)) not in {(s, e) for _, _, _, s, e in rows("apple-order")}


# ---------------------------------------------------------------- pause, expiry, refund

def test_google_pause_has_no_access_and_says_when_it_resumes(client, at):
    subscribed(client, "gpa-pause", channel="google", event="SUBSCRIPTION_PURCHASED")
    store(client, "gpa-pause", "SUBSCRIPTION_PAUSED", 5, 35, version=2, channel="google")
    at(10)
    s = status(client)
    assert s["status"] == "PLAN_PAUSED" and s["offer"] is None and "nextnap" not in s["usable_features"]
    assert f"resumes on {human(day(35))}" in s["message"] and "payment" not in s["message"]
    assert s["allowed_actions"] == ["check_again"]
    at(35)
    store(client, "gpa-pause", "SUBSCRIPTION_RECOVERED", 35, 400, version=3, channel="google")
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"


def test_store_reported_expiry_is_not_called_a_missing_renewal(client, at):
    subscribed(client, "apple-expired")
    store(client, "apple-expired", "EXPIRED/VOLUNTARY", -360, 5, version=2)
    at(10)
    s = status(client)
    assert s["status"] == "PLAN_EXPIRED" and s["reason"]["store_reported"] is True
    assert s["message"].startswith(f"The App Store reports that Memories on Nursery camera ended on {human(day(5))}")
    assert "hasn't reported a renewal" not in s["message"]
    assert s["allowed_actions"] == ["contact_support"]
    again = connect(client, "apple-expired", key="connect-again-expired")
    assert again.status_code == 409 and again.json()["code"] == "PURCHASE_EXPIRED"


def test_refund_during_grace_ends_access_now(client, at):
    subscribed(client, "apple-refund-grace")
    store(client, "apple-refund-grace", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 21, version=2)
    at(10)
    assert status(client)["status"] == "BILLING_GRACE"
    store(client, "apple-refund-grace", "REFUND", -360, 5, version=3)
    s = status(client)
    assert s["status"] == "REFUNDED_OR_REVOKED" and entitlement()["plan_code"] != "memories"


# ---------------------------------------------------------------- the provider map

def test_billing_retry_never_maps_to_access():
    for channel, table in provider_lifecycle.EVENTS.items():
        for event, mapped in table.items():
            if mapped is not None:
                assert provider_lifecycle.ACCESS[mapped] == (mapped in ("verified", "grace")), (channel, event)
    assert provider_lifecycle.translate("apple", "DID_FAIL_TO_RENEW") == "billing_retry"
    assert provider_lifecycle.translate("apple", "GRACE_PERIOD_EXPIRED") == "billing_retry"
    assert provider_lifecycle.translate("apple", "DID_FAIL_TO_RENEW/GRACE_PERIOD") == "grace"
    assert provider_lifecycle.translate("google", "SUBSCRIPTION_ON_HOLD") == "billing_retry"
    assert provider_lifecycle.translate("google", "SUBSCRIPTION_PAUSED") == "paused"
    assert provider_lifecycle.translate("google", "SUBSCRIPTION_REVOKED") == "refunded"
    with pytest.raises(DomainError) as unknown:
        provider_lifecycle.translate("apple", "SOMETHING_NEW")
    assert unknown.value.code == "UNKNOWN_PROVIDER_EVENT"


def test_events_that_change_nothing_are_refused_with_the_reason(client, at):
    canceled = store(client, "gpa-cancel", "SUBSCRIPTION_CANCELED", -10, 355, channel="google")
    assert canceled.status_code == 422 and canceled.json()["code"] == "NOT_A_STATE_CHANGE"
    assert "until" in canceled.json()["message"]               # access lasts until the period ends
    mismatch = client.post("/v1/dev/purchase-evidence", headers=PARENT, json={
        "channel": "apple", "external_ref": "apple-mismatch", "plan_code": "memories", "status": "verified",
        "provider_event": "DID_FAIL_TO_RENEW", "provider_version": 1})
    assert mismatch.status_code == 422 and mismatch.json()["code"] == "PROVIDER_EVENT_MISMATCH"
    code = client.post("/v1/dev/purchase-evidence", headers=PARENT, json={
        "channel": "activation_code", "external_ref": "CODE-GRACE", "plan_code": "memories", "status": "grace",
        "provider_version": 1})
    assert code.status_code == 422 and code.json()["code"] == "NOT_A_SUBSCRIPTION"


# ---------------------------------------------------------------- adversarial review (2026-10-11)

def decide(client, feature="nextnap"):
    return client.post("/v1/capability-decisions", headers=PARENT, json={"feature": feature}).json()


def look_up(client, ref, channel="apple"):
    return client.post("/v1/access/check", headers=PARENT,
                       json={"device_id": NURSERY, "channel": channel, "external_ref": ref}).json()


def subscription_blockers(decision):
    return [b for b in decision["blockers"] if b["kind"] == "subscription"]


@pytest.mark.parametrize("channel,first,event,code", [
    ("apple", "DID_RENEW", "DID_FAIL_TO_RENEW", "PAYMENT_RETRYING"),
    ("google", "SUBSCRIPTION_PURCHASED", "SUBSCRIPTION_PAUSED", "PLAN_PAUSED")])
def test_a_plan_whose_payment_is_retrying_or_paused_is_never_sold_again(client, at, channel, first, event, code):
    subscribed(client, "held-plan", channel=channel, event=first)
    store(client, "held-plan", event, 5, 35, version=2, channel=channel)
    at(10)
    d = decide(client)
    assert d["offer"] is None
    blockers = subscription_blockers(d)
    assert [b["code"] for b in blockers] == [code] and "nothing more to buy" in blockers[0]["parent_message"]


def test_an_unconnected_purchase_in_grace_is_never_sold_again(client, at):
    store(client, "apple-unbound-grace", "SUBSCRIBED/INITIAL_BUY", -360, 5)
    store(client, "apple-unbound-grace", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 21, version=2)
    at(10)
    d = decide(client)
    assert d["offer"] is None and [b["code"] for b in subscription_blockers(d)] == ["PURCHASE_NOT_CONNECTED"]
    s = status(client)
    assert s["status"] == "VERIFIED_UNBOUND" and human(day(20)) in s["message"] and "payment" in s["message"]


@pytest.mark.parametrize("later", [("DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 12), ("DID_FAIL_TO_RENEW", 8, 60)])
def test_a_later_store_word_inside_a_written_grace_window_is_not_hidden(client, at, later):
    subscribed(client, "apple-cut")
    store(client, "apple-cut", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 21, version=2)
    store(client, "apple-cut", later[0], later[1], later[2], version=3)     # the store's newer word ends it sooner
    at(14)
    camera, purchase = status(client), look_up(client, "apple-cut")
    assert camera["status"] == purchase["status"] == "CONFLICTING_EVIDENCE"
    assert camera["reason"]["code"] == "STORE_ENDED_CAMERA_ACTIVE"
    assert (day(5), day(21)) in {(s, e) for _, _, _, s, e in rows("apple-cut")}   # history is kept


def test_only_the_latest_grace_window_is_written(client, at):
    subscribed(client, "apple-shorter")
    store(client, "apple-shorter", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 12, version=3)
    store(client, "apple-shorter", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 21, version=2)   # older, arrives late
    assert (day(5), day(21)) not in {(s, e) for _, _, _, s, e in rows("apple-shorter")}


def test_a_grace_event_needs_its_end_date(client, at):
    r = client.post("/v1/dev/purchase-evidence", headers=PARENT, json={
        "channel": "apple", "external_ref": "apple-no-end", "plan_code": "memories",
        "provider_event": "DID_FAIL_TO_RENEW/GRACE_PERIOD", "provider_version": 2})
    assert r.status_code == 422 and r.json()["code"] == "GRACE_NEEDS_END"
    paused = client.post("/v1/dev/purchase-evidence", headers=PARENT, json={
        "channel": "google", "external_ref": "gpa-no-end", "plan_code": "memories",
        "provider_event": "SUBSCRIPTION_PAUSED", "provider_version": 2})
    assert paused.status_code == 200 and paused.json()["after"]["ends_at"] is None   # resume date unknown


def test_an_unconnected_purchase_in_billing_retry_is_not_promised_a_camera(client, at):
    store(client, "gpa-unbound", "SUBSCRIPTION_PURCHASED", -360, 5, channel="google")
    store(client, "gpa-unbound", "SUBSCRIPTION_ON_HOLD", 5, 60, version=2, channel="google")
    at(10)
    s = look_up(client, "gpa-unbound", channel="google")
    assert s["status"] == "BILLING_RETRY" and "by itself" not in s["message"] and "connect" in s["message"]
    store(client, "gpa-unbound", "SUBSCRIPTION_RECOVERED", 10, 375, version=3, channel="google")
    assert look_up(client, "gpa-unbound", channel="google")["status"] == "VERIFIED_UNBOUND"


def test_a_higher_plan_on_the_camera_is_not_hidden_by_a_lower_plans_billing(client, at):
    subscribed(client, "apple-lower")                                    # Memories on the Nursery camera
    store(client, "apple-higher", "SUBSCRIBED/INITIAL_BUY", -10, 355, plan="milestones")
    assert connect(client, "apple-higher").status_code == 201
    verify(client)
    store(client, "apple-lower", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 21, version=2)
    at(10)
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"
    at(22)
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"
    s = look_up(client, "apple-lower")
    assert s["status"] == "BILLING_RETRY" and "features are off" not in s["message"] and "Milestones" in s["message"]


def test_a_lost_higher_plan_outranks_an_unconnected_lower_purchase(client, at):
    subscribed(client, "apple-held")
    store(client, "apple-held", "DID_FAIL_TO_RENEW", 5, 65, version=2)
    store(client, "apple-sleep", "SUBSCRIBED/INITIAL_BUY", 0, 365, plan="sleep")
    at(60)                                                               # after the Sleep trial
    assert status(client)["status"] == "BILLING_RETRY"


def test_grace_names_the_last_day_with_access(client, at):
    subscribed(client, "apple-day")
    store(client, "apple-day", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 21, version=2)   # ends at midnight UTC, day 21
    at(10)
    s = status(client)
    assert f"through {human(day(20))}" in s["message"] and human(day(21)) not in s["message"]


def test_a_pause_past_its_resume_date_says_the_store_hasnt_confirmed(client, at):
    subscribed(client, "gpa-overdue", channel="google", event="SUBSCRIPTION_PURCHASED")
    store(client, "gpa-overdue", "SUBSCRIPTION_PAUSED", 5, 35, version=2, channel="google")
    at(36)
    s = status(client)
    assert s["status"] == "PLAN_PAUSED" and "hasn't confirmed" in s["message"]


def test_the_home_list_agrees_with_the_camera_after_grace_ends(client, at):
    subscribed(client, "apple-home")
    store(client, "apple-home", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 5, 12, version=2)
    at(13)
    home = client.get("/v1/access", headers=PARENT).json()
    assert next(p for p in home["purchases"] if p["external_ref"] == "apple-home")["state"] == "billing_retry"


def test_deferred_and_restarted_keep_access(client, at):
    subscribed(client, "gpa-defer", channel="google", event="SUBSCRIPTION_PURCHASED")
    store(client, "gpa-defer", "SUBSCRIPTION_DEFERRED", -360, 35, version=2, channel="google")   # renewal date moves
    at(20)
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"
    store(client, "gpa-defer", "SUBSCRIPTION_RESTARTED", -360, 35, version=3, channel="google")
    assert status(client)["status"] == "ACTIVE_FEATURE_USABLE"


def test_a_paused_purchase_cant_be_connected_again(client, at):
    subscribed(client, "gpa-paused-bind", channel="google", event="SUBSCRIPTION_PURCHASED")
    store(client, "gpa-paused-bind", "SUBSCRIPTION_PAUSED", 5, 35, version=2, channel="google")
    at(10)
    again = connect(client, "gpa-paused-bind", channel="google", key="connect-again-paused")
    assert again.status_code == 409 and again.json()["code"] == "PURCHASE_PAUSED"
