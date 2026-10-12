"""Capability decisions: immutable, versioned snapshots of eligibility.

Decisions are per camera: Nanit attaches plans to a camera, and a feature's
mount requirement is about that camera, not any camera in the household.

Invariant: **no stale offers.** A decision records the household's
commercial-state revision and the catalog and policy versions it was
evaluated under, plus an expiry. If any of those move, the decision is stale
and nothing may be bought against it.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import claims, clock, entitlements, purchase_evidence
from .models import CapabilityDecision, CommercialConfig, Device, Household, PurchaseIntent
from .security import Principal, current_trace_id, new_id

DECISION_TTL = timedelta(minutes=15)
# Surfaces where a parent is watching their child or responding to an alert.
# Eligibility is still answered there, but nothing is ever sold on them.
SAFETY_SURFACES = {"live_view", "safety_alert"}
LIVE_INTENT_STATES = ("PREPARED", "SUBMITTED", "UNKNOWN")


def utcnow() -> datetime:
    return clock.now()


def active_versions(db: Session) -> tuple[str, str]:
    config = db.get(CommercialConfig, 1)
    return config.catalog_version, config.policy_version


def camera_snapshot(db: Session, household_id: str, device: Device, catalog_version: str) -> dict[str, Any]:
    plan = claims.camera_plan(db, household_id, device.id, catalog_version)
    return {"device_id": device.id, "device_label": device.label, "mounts": [device.mount],
            "plan_code": plan["plan_code"], "plan_state": plan["state"], "pending_plans": plan["pending_plans"],
            "member_cap": plan["member_cap"]}


def unconfirmed_plans(db: Session, household_id: str, device: Device, catalog_version: str,
                      pending_claims: list[str], ignore_intent_id: Optional[str] = None) -> list[str]:
    """Plans this camera may already have bought: pending store claims plus this
    app's own purchases that are not yet confirmed or failed. Lowest rank first."""
    plans = entitlements.CATALOGS[catalog_version]["plans"]
    in_flight = db.execute(select(PurchaseIntent.terms).where(
        PurchaseIntent.household_id == household_id, PurchaseIntent.device_id == device.id,
        PurchaseIntent.state.in_(LIVE_INTENT_STATES),
        PurchaseIntent.id != (ignore_intent_id or ""))).scalars()
    found = set(pending_claims) | {terms["plan_code"] for terms in in_flight}
    return sorted(found, key=lambda code: plans.get(code, {"rank": 0})["rank"])


def evaluate_now(db: Session, household: Household, feature: str, device: Device,
                 ignore_intent_id: Optional[str] = None) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Eligibility of one feature on one camera, from that camera's own plan and
    mount. While a purchase for this camera is unconfirmed (a pending store event
    or this app's own purchase in flight) and would unlock the feature, the answer
    is UNKNOWN with no offer: never sell what was just bought. The submission
    check passes its own intent as `ignore_intent_id`."""
    catalog_version, policy_version = active_versions(db)
    snapshot = camera_snapshot(db, household.id, device, catalog_version)
    result = entitlements.evaluate(feature=feature, plan_code=snapshot["plan_code"],
                                   mounts=snapshot["mounts"], catalog_version=catalog_version,
                                   policy_version=policy_version)
    unconfirmed = unconfirmed_plans(db, household.id, device, catalog_version, snapshot["pending_plans"],
                                    ignore_intent_id)
    if result["status"] == "BLOCKED" and unconfirmed:
        with_pending = entitlements.evaluate(feature=feature, plan_code=unconfirmed[-1],
                                             mounts=snapshot["mounts"], catalog_version=catalog_version,
                                             policy_version=policy_version, with_offer=False)
        if with_pending["status"] == "AVAILABLE":
            result = {**result, "status": "UNKNOWN", "offer": None, "blockers": [{
                "code": "PURCHASE_BEING_CONFIRMED", "kind": "subscription", "resolution": "WAIT",
                "parent_message": "We're confirming a recent purchase for this camera. "
                                  "There's nothing more to buy; this updates when the store confirms it."}]}
    if result["status"] == "BLOCKED" and result["offer"]:
        result = _withhold_if_already_bought(db, household, feature, snapshot, result, catalog_version,
                                             policy_version)
    return result, snapshot, catalog_version, policy_version


def _withhold_if_already_bought(db: Session, household: Household, feature: str, snapshot: dict[str, Any],
                                result: dict[str, Any], catalog_version: str, policy_version: str) -> dict[str, Any]:
    """A purchase the family already made would unlock this feature here: don't
    sell it again. Either it isn't connected to a camera yet (paid on the
    website, an unused code, a store purchase still confirming or in a grace
    period), or the store holds it (a renewal payment being retried, a pause):
    point to connecting it, fixing the payment, or waiting for the resume."""
    plans = entitlements.CATALOGS[catalog_version]["plans"]
    now = utcnow()
    current = [purchase_evidence.as_of(p, now) for p in purchase_evidence.household_purchases(db, household.id)]
    owned = [p for p in current
             if (not p["binding"]["bound"] and p["state"] in ("verified", "pending", "conflicting", "grace"))
             or p["state"] in ("billing_retry", "paused")]
    for p in sorted(owned, key=lambda p: -plans.get(p["plan_code"], {"rank": 0})["rank"]):
        unlocked = entitlements.evaluate(feature=feature, plan_code=p["plan_code"], mounts=snapshot["mounts"],
                                         catalog_version=catalog_version, policy_version=policy_version,
                                         with_offer=False)
        if unlocked["status"] == "AVAILABLE":
            label = entitlements.plan_label(catalog_version, p["plan_code"])
            if p["state"] == "billing_retry":
                code, resolution, message = ("PAYMENT_RETRYING", "UPDATE_PAYMENT",
                                             f"Your {label} renewal payment didn't go through and the store is "
                                             "retrying it. Update the payment method where you bought it. "
                                             "There's nothing more to buy.")
            elif p["state"] == "paused":
                code, resolution, message = ("PLAN_PAUSED", "WAIT", f"Your {label} plan is paused in the store and "
                                             "comes back when it resumes. There's nothing more to buy.")
            else:
                code, resolution, message = ("PURCHASE_NOT_CONNECTED", "CONNECT",
                                             f"You already have a {label} purchase that isn't connected to a "
                                             "camera. Connect it to this camera. There's nothing more to buy.")
            return {**result, "offer": None, "blockers": [b for b in result["blockers"] if b["kind"] != "subscription"] + [{
                "code": code, "kind": "subscription", "resolution": resolution,
                "detail": {"channel": p["channel"], "plan_code": p["plan_code"], "purchase_state": p["state"]},
                "parent_message": message}]}
    return result


def create(db: Session, principal: Principal, household: Household, feature: str,
           device: Device, surface: str = "feature_availability") -> CapabilityDecision:
    """Caller holds the household lock (so the snapshot is coherent)."""
    result, snapshot, catalog_version, policy_version = evaluate_now(db, household, feature, device)
    snapshot = {**snapshot, "surface": surface}
    if surface in SAFETY_SURFACES and result["offer"]:
        result = {**result, "offer": None}
        snapshot["offer_suppressed"] = "no offers on safety surfaces"
    decision = CapabilityDecision(
        id=new_id("dec"), household_id=household.id, member_id=principal.member_id, device_id=device.id,
        feature=feature, status=result["status"], blockers=result["blockers"], offer=result["offer"],
        inputs={**snapshot, "requirements": result["requirements"]},
        state_revision=household.state_revision, catalog_version=catalog_version,
        policy_version=policy_version, expires_at=utcnow() + DECISION_TTL, trace_id=current_trace_id())
    db.add(decision)
    db.flush()
    return decision


def staleness(db: Session, decision: CapabilityDecision, household: Household,
              now: Optional[datetime] = None) -> list[str]:
    reasons = []
    catalog_version, policy_version = active_versions(db)
    if household.state_revision != decision.state_revision:
        reasons.append("HOUSEHOLD_STATE_CHANGED")
    if decision.catalog_version != catalog_version:
        reasons.append("CATALOG_CHANGED")
    if decision.policy_version != policy_version:
        reasons.append("POLICY_CHANGED")
    if decision.expires_at <= (now or utcnow()):
        reasons.append("DECISION_EXPIRED")
    return reasons


def view(db: Session, decision: CapabilityDecision, household: Household) -> dict[str, Any]:
    reasons = staleness(db, decision, household)
    return {
        "decision_id": decision.id, "device_id": decision.device_id, "feature": decision.feature,
        "status": decision.status,
        "blockers": decision.blockers, "offer": decision.offer, "inputs": decision.inputs,
        "state_revision": decision.state_revision, "catalog_version": decision.catalog_version,
        "policy_version": decision.policy_version, "expires_at": decision.expires_at.isoformat(),
        "current": not reasons, "stale_reasons": reasons, "current_state_revision": household.state_revision,
        "trace_id": decision.trace_id,
    }
