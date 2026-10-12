"""Same symptom, two causes: build two scenarios, one after the other, and
show what the backend decided in each and what it refused.

For each scenario this:
1. resets the demo and builds it through the real scenario runner;
2. reads the camera's access decision (access.assess, read-only);
3. tries the two things a frustrated parent might do. Both run the real
   domain code in a transaction that is always rolled back, so the backend's
   answer is real and nothing is kept:
   - buying the feature again (decisions.evaluate_now: is an offer made?);
   - connecting the purchase again (access.bind: does it refuse, and with
     which code?).

The two runs are separate and reproducible. They aren't simultaneous
branches: the demo has one database, so the second build replaces the
first. Each snapshot is taken before the next build starts.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from . import access, decisions, purchase_evidence, scenarios
from .db import SessionLocal, discard_kept
from .engine import DomainError
from .models import Device, Household, Member
from .security import Principal

# Pairs that look the same to a parent ("NextNap doesn't work") and differ underneath.
COMPARABLE = ("billing_retry", "incompatible_hardware", "plan_ended", "paid_not_active", "billing_grace",
              "access_lost_after_refund", "purchase_uncertain", "paid_wrong_camera", "renewal_reflected")
FEATURE = "nextnap"


def _parent(db) -> Principal:
    m = db.get(Member, scenarios.seed.PARENT_A)
    return Principal(m.id, m.household_id, m.role, m.can_bill, m.display_name)


def _probe_buy(db, household: Household, device: Device) -> dict[str, Any]:
    result, _snapshot, _c, _p = decisions.evaluate_now(db, household, FEATURE, device)
    return {"action": f"Buy a plan for {FEATURE}", "status": result["status"],
            "offer_made": result["offer"] is not None,
            "blockers": [b["code"] for b in result["blockers"]],
            "answer": ("an offer is made" if result["offer"] else
                       "no offer: " + ", ".join(b["code"] for b in result["blockers"]))}


def _probe_connect(db, principal: Principal, household: Household, device: Device,
                   channel: str, ref: str) -> dict[str, Any]:
    try:
        outcome, _replayed = access.bind(db, principal, household, device, channel=channel, external_ref=ref,
                                         expected_state_revision=household.state_revision,
                                         idempotency_key=f"compare-probe-{ref}")
        answer = "already on this camera; nothing changes" if outcome["already_connected"] else "connected"
        return {"action": f"Connect the {channel} purchase to this camera", "refused": False, "code": None,
                "answer": answer}
    except DomainError as exc:
        return {"action": f"Connect the {channel} purchase to this camera", "refused": True, "code": exc.code,
                "answer": f"refused: {exc.code}"}


def snapshot(key: str) -> dict[str, Any]:
    """Build one scenario and read the backend's decision and refusals."""
    scenarios.run(key)
    device_id = scenarios.SCENARIOS[key]["focus"]["device_id"]
    with SessionLocal() as db:
        household = db.get(Household, scenarios.seed.HOUSEHOLD)
        device = db.get(Device, device_id)
        principal = _parent(db)
        a = access.assess(db, household, device, member_can_bind=True)
        purchases = purchase_evidence.household_purchases(db, household.id)
    probes = []
    with SessionLocal() as db:          # every probe is rolled back below
        try:
            household = db.execute(select(Household).where(Household.id == scenarios.seed.HOUSEHOLD)
                                   .with_for_update()).scalar_one()
            device = db.get(Device, device_id)
            probes.append(_probe_buy(db, household, device))
            for p in purchases[:2]:
                with db.begin_nested():
                    probes.append(_probe_connect(db, principal, household, device, p["channel"], p["external_ref"]))
        finally:
            db.rollback()
            discard_kept()               # bind's detached analytics would otherwise outlive the rollback
    return {"scenario": key, "title": scenarios.SCENARIOS[key]["title"], "story": scenarios.SCENARIOS[key]["story"],
            "status": a["status"], "headline": a["headline"], "message": a["message"],
            "reason": a["reason"].get("code"), "allowed_actions": a["allowed_actions"], "offer": a["offer"],
            "stages": [{"label": s["label"], "state": s["state"], "fact": s["fact"]} for s in a["stages"]],
            "usable_features": a["usable_features"], "fingerprint": a["fingerprint"][:12],
            "state_revision": a["state_revision"], "probes": probes,
            "probes_persisted": False}


def compare(a: str, b: str) -> dict[str, Any]:
    if a not in COMPARABLE or b not in COMPARABLE or a == b:
        raise DomainError(422, "NOT_COMPARABLE", "choose two different scenarios from the list",
                          allowed=list(COMPARABLE))
    first, second = snapshot(a), snapshot(b)
    differs = [k for k in ("status", "reason", "allowed_actions", "usable_features") if first[k] != second[k]]
    return {"symptom": "NextNap doesn't work on the Nursery camera", "a": first, "b": second, "differs_in": differs,
            "note": "Two separate, reproducible runs on one database: the second build replaced the first. "
                    "Probes ran the real domain code inside a transaction that was rolled back."}
