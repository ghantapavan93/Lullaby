"""Scenario Studio: purchase-to-access situations, built through the
application's own code paths. Synthetic and simulated; no store is contacted.

Each scenario resets the demo data, then runs its steps one transaction each,
through the same functions the API uses (purchase evidence, connect, verify,
claims, purchase intents). Nothing is written straight into a table, so a
scenario can only produce states the application itself can reach.

Each step runs in its own transaction because order matters: a verification
check must commit after the connect it verifies.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from . import access, claims, clock, decisions, purchase_evidence, purchases, seed, video_evidence, worker
from .db import transaction
from .models import Device, Household, Member, OutboxJob
from .security import Principal, lock_household


Step = Callable[[Session, Principal, Household], Any]


def _parent(db: Session) -> Principal:
    m = db.get(Member, seed.PARENT_A)
    return Principal(m.id, m.household_id, m.role, m.can_bill, m.display_name)


def _device(db: Session, device_id: str) -> Device:
    return db.get(Device, device_id)


def evidence(channel: str, ref: str, plan: str, status: str, version: int = 1,
             period: tuple[int, int] | None = None) -> Step:
    """`period` is (start, end) in days relative to today; the default is a
    year that began ten days ago."""
    def run(db: Session, _: Principal, household: Household) -> Any:
        catalog_version, _v = decisions.active_versions(db)
        starts, ends = _period(period)
        return purchase_evidence.record(db, household, channel=channel, external_ref=ref, plan_code=plan,
                                        status=status, provider_version=version, starts_at=starts,
                                        ends_at=ends, catalog_version=catalog_version)
    run.__doc__ = f"{channel} purchase {ref}: {plan}, {status} (provider version {version})" + (
        f", period {period[0]:+d}d to {period[1]:+d}d" if period else "")
    return run


def store_event(channel: str, ref: str, event: str, version: int, period: tuple[int, int],
                plan: str = "memories") -> Step:
    """A store notification, translated by app.provider_lifecycle. Synthetic:
    nothing is received from a store and no signature is checked."""
    def run(db: Session, _: Principal, household: Household) -> Any:
        catalog_version, _v = decisions.active_versions(db)
        starts, ends = _period(period)
        return purchase_evidence.record(db, household, channel=channel, external_ref=ref, plan_code=plan,
                                        status=None, provider_event=event, provider_version=version,
                                        starts_at=starts, ends_at=ends, catalog_version=catalog_version)
    run.__doc__ = (f"{channel} sends {event} for {ref} (provider version {version}), "
                   f"period {period[0]:+d}d to {period[1]:+d}d")
    return run


def video_sample(device_id: str) -> Step:
    """Store the demo clip for a camera and queue its analysis. The worker, a
    separate process, does the analysis later; nothing waits for it here."""
    def run(db: Session, _: Principal, household: Household) -> Any:
        if os.environ.get("PROOF_LAYER_VIDEO", "1") != "1":
            return {"skipped": "the video module is switched off"}
        source, kind, provenance = video_evidence.sample_source()
        asset, _created = video_evidence.register(db, household, _device(db, device_id), source=source,
                                                  source_kind=kind, provenance=provenance)
        run_, _queued = video_evidence.request_analysis(db, household, asset)
        return {"asset_id": asset.id, "run_id": run_.id, "state": run_.state}
    run.__doc__ = f"the demo clip is stored for {device_id} and its analysis queued"
    return run


def _period(period: tuple[int, int] | None) -> tuple[datetime, datetime]:
    if period is None:
        period = (-10, 355)          # a year that began ten days ago: never a fixed date
    today = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=period[0]), today + timedelta(days=period[1])


def connected_last_year(device_id: str, channel: str, ref: str, plan: str, period: tuple[int, int]) -> Step:
    """The claim a connect wrote when the purchase began, a year ago."""
    def run(db: Session, _: Principal, household: Household) -> Any:
        catalog_version, _v = decisions.active_versions(db)
        starts, ends = _period(period)
        result = claims.record(db, household, _device(db, device_id), channel=channel, external_ref=ref,
                               event_status="active", plan_code=plan, starts_at=starts, ends_at=ends,
                               catalog_version=catalog_version)
        return {"claim_id": result["claim_id"]}
    run.__doc__ = f"{ref} was connected to {device_id} when it began"
    return run


def connect(device_id: str, channel: str, ref: str) -> Step:
    def run(db: Session, principal: Principal, household: Household) -> Any:
        return access.bind(db, principal, household, _device(db, device_id), channel=channel, external_ref=ref,
                           expected_state_revision=household.state_revision,
                           idempotency_key=f"scenario-{device_id}-{ref}")[0]
    run.__doc__ = f"parent connects {ref} to {device_id}"
    return run


def verify(device_id: str) -> Step:
    def run(db: Session, principal: Principal, household: Household) -> Any:
        return access.verify(db, principal, household, _device(db, device_id))["check"]
    run.__doc__ = f"access verified on {device_id}"
    return run


def mount(device_id: str, where: str) -> Step:
    def run(db: Session, _: Principal, household: Household) -> Any:
        _device(db, device_id).mount = where
        household.state_revision += 1
        return {"device_id": device_id, "mount": where}
    run.__doc__ = f"{device_id} moved to {where}"
    return run


def prepare_purchase(device_id: str, feature: str) -> Step:
    def run(db: Session, principal: Principal, household: Household) -> Any:
        decision = decisions.create(db, principal, household, feature, _device(db, device_id))
        intent, _ = purchases.prepare(db, principal, decision_id=decision.id, offer_id=decision.offer["offer_id"],
                                      idempotency_key=f"scenario-{device_id}-{feature}",
                                      expected_amount_minor=decision.offer["amount_minor"],
                                      expected_currency=decision.offer["currency"])
        return {"intent_id": intent.id, "state": intent.state, "operation_key": intent.provider_operation_key}
    run.__doc__ = f"in-app purchase prepared for {feature} on {device_id} (simulated)"
    return run


def hold_jobs() -> Step:
    def run(db: Session, principal: Principal, household: Household) -> Any:
        held = db.execute(update(OutboxJob).where(OutboxJob.household_id == household.id,
                                                  OutboxJob.state == "pending")
                          .values(available_at=func.now() + worker.HOLD)).rowcount
        return {"held_jobs": held}
    run.__doc__ = ("the purchase job is held for the demo worker, so an always-on worker "
                   "doesn't finish it before the crash can be shown")
    return run


NURSERY, PLAYROOM = seed.CAMERA, seed.PLAYROOM

SCENARIOS: dict[str, dict[str, Any]] = {
    "paid_not_active": {
        "title": "Paid, but not active",
        "story": "Paid for Memories on the website. The plan isn't on any camera yet.",
        "steps": [evidence("web", "web-ord-1001", "memories", "verified")],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "VERIFIED_UNBOUND"},
        "resolution": "Connect the purchase to the Nursery camera, then verify. Nothing is bought again.",
    },
    "paid_wrong_camera": {
        "title": "Paid, wrong camera",
        "story": "A Memories activation code was entered on the Playroom camera instead of the Nursery.",
        "steps": [evidence("activation_code", "CODE-MEM-2002", "memories", "verified"),
                  connect(PLAYROOM, "activation_code", "CODE-MEM-2002"), verify(PLAYROOM)],
        "focus": {"device_id": NURSERY, "channel": "activation_code", "external_ref": "CODE-MEM-2002"},
        "expected": {NURSERY: "ALREADY_ASSIGNED", PLAYROOM: "ACTIVE_HARDWARE_BLOCKED"},
        "resolution": "The code is not moved automatically. Support moves it; a second redemption is refused.",
    },
    "incompatible_hardware": {
        "title": "Paid, incompatible mount",
        "story": "Memories from the App Store is active on the Nursery camera, now on a Flex Stand.",
        "steps": [mount(NURSERY, "flex_stand"), evidence("apple", "apple-txn-3003", "memories", "verified"),
                  connect(NURSERY, "apple", "apple-txn-3003"), verify(NURSERY)],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "ACTIVE_HARDWARE_BLOCKED"},
        "resolution": "Move the camera above the crib. No plan is offered: a plan can't fix a mount.",
    },
    "purchase_uncertain": {
        "title": "Purchase not confirmed yet",
        "story": "Google Play has not confirmed a Memories purchase.",
        "steps": [evidence("google", "gpa-4004", "memories", "pending")],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "PURCHASE_PENDING"},
        "resolution": "Wait for the store. NextNap is not offered for sale meanwhile.",
    },
    "paid_twice": {
        "title": "Paid twice",
        "story": "Sleep was bought in the App Store and again on the website, both for the Nursery camera.",
        "steps": [evidence("apple", "apple-txn-5005", "sleep", "verified"),
                  connect(NURSERY, "apple", "apple-txn-5005"), verify(NURSERY),
                  evidence("web", "web-ord-5006", "sleep", "verified")],
        "focus": {"device_id": NURSERY, "channel": "web", "external_ref": "web-ord-5006"},
        "expected": {NURSERY: "DUPLICATE_PURCHASE"},
        "resolution": "The second purchase is not connected to the same camera. Support handles the charge.",
    },
    "access_lost_after_refund": {
        "title": "Access lost after a refund",
        "story": "Memories was connected and verified on the Nursery camera, then refunded.",
        "steps": [evidence("web", "web-ord-6006", "memories", "verified"),
                  connect(NURSERY, "web", "web-ord-6006"), verify(NURSERY),
                  evidence("web", "web-ord-6006", "memories", "refunded", version=2), verify(NURSERY)],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "REFUNDED_OR_REVOKED"},
        "resolution": "The refund writes a revoked claim; the camera falls back to its trial. History is kept.",
    },
    "renewal_reflected": {
        "title": "Renewed, still active",
        "story": "Memories from the App Store renewed after a year. The camera keeps it without a new connect.",
        "steps": [evidence("apple", "apple-txn-7007", "memories", "verified", period=(-370, -5)),
                  connected_last_year(NURSERY, "apple", "apple-txn-7007", "memories", (-370, -5)),
                  evidence("apple", "apple-txn-7007", "memories", "verified", version=2, period=(-5, 360)),
                  verify(NURSERY)],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "ACTIVE_FEATURE_USABLE"},
        "resolution": "The renewal is written to the same camera as a new period, in the same transaction.",
    },
    "plan_ended": {
        "title": "Plan ended, no renewal",
        "story": "Memories ended five days ago and the store has reported no renewal.",
        "steps": [evidence("apple", "apple-txn-8008", "memories", "verified", period=(-370, -5)),
                  connected_last_year(NURSERY, "apple", "apple-txn-8008", "memories", (-370, -5))],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "PLAN_EXPIRED"},
        "resolution": "Says the plan ended and when. Not a paywall: support can check a renewal on another account.",
    },
    "billing_grace": {
        "title": "Payment failed, grace period",
        "story": "The App Store couldn't take the Memories renewal payment. The store keeps the plan on for 16 days "
                 "while it retries.",
        "steps": [store_event("apple", "apple-txn-1101", "SUBSCRIBED/INITIAL_BUY", 1, (-370, -5)),
                  connected_last_year(NURSERY, "apple", "apple-txn-1101", "memories", (-370, -5)),
                  store_event("apple", "apple-txn-1101", "DID_FAIL_TO_RENEW/GRACE_PERIOD", 2, (-5, 11)),
                  verify(NURSERY)],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "BILLING_GRACE"},
        "resolution": "Features keep working until the grace end. The parent updates the payment method; nothing is "
                      "bought again.",
    },
    "billing_retry": {
        "title": "Payment failed, account hold",
        "story": "Google Play couldn't take the Memories renewal payment and put the subscription on hold.",
        "steps": [store_event("google", "gpa-1202", "SUBSCRIPTION_PURCHASED", 1, (-370, -5)),
                  connected_last_year(NURSERY, "google", "gpa-1202", "memories", (-370, -5)),
                  store_event("google", "gpa-1202", "SUBSCRIPTION_ON_HOLD", 2, (-5, 55))],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "BILLING_RETRY"},
        "resolution": "No access during the hold, as Google documents. The purchase stays on its camera, so recovery "
                      "needs no new connect and no new purchase.",
    },
    "billing_recovered": {
        "title": "Payment recovered",
        "story": "The App Store retried the failed Memories renewal and the payment went through.",
        "steps": [store_event("apple", "apple-txn-1303", "SUBSCRIBED/INITIAL_BUY", 1, (-370, -5)),
                  connected_last_year(NURSERY, "apple", "apple-txn-1303", "memories", (-370, -5)),
                  store_event("apple", "apple-txn-1303", "DID_FAIL_TO_RENEW", 2, (-5, 55)),
                  store_event("apple", "apple-txn-1303", "DID_RENEW/BILLING_RECOVERY", 3, (-1, 364)),
                  verify(NURSERY)],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "ACTIVE_FEATURE_USABLE"},
        "resolution": "The recovered period is written to the same camera in the same transaction. No new connect.",
    },
    "plan_paused": {
        "title": "Plan paused in the store",
        "story": "The parent paused Memories in Google Play for a month.",
        "steps": [store_event("google", "gpa-1404", "SUBSCRIPTION_PURCHASED", 1, (-370, -5)),
                  connected_last_year(NURSERY, "google", "gpa-1404", "memories", (-370, -5)),
                  store_event("google", "gpa-1404", "SUBSCRIPTION_PAUSED", 2, (-5, 25))],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "PLAN_PAUSED"},
        "resolution": "Says when the plan resumes. Not a payment problem and not a paywall.",
    },
    "evidence_journey": {
        "title": "Renewal, then recorded evidence",
        "story": "Milestones from the App Store ended five days ago, so 7-day video history is off and the clip "
                 "can't be reviewed. The store's renewal then arrives.",
        "steps": [store_event("apple", "apple-txn-1505", "SUBSCRIBED/INITIAL_BUY", 1, (-370, -5), plan="milestones"),
                  connected_last_year(NURSERY, "apple", "apple-txn-1505", "milestones", (-370, -5)),
                  video_sample(NURSERY)],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "PLAN_EXPIRED"},
        "resolution": "After the renewal reaches the same camera, video history is verified and the clip's measured "
                      "evidence opens. Analysis runs in the worker, separately from the access check.",
    },
    "upgrade_across_channels": {
        "title": "Upgrade from another channel",
        "story": "The Nursery camera has Memories from the App Store. A Milestones plan was then bought on the website.",
        "steps": [evidence("apple", "apple-txn-9009", "memories", "verified"),
                  connect(NURSERY, "apple", "apple-txn-9009"), verify(NURSERY),
                  evidence("web", "web-ord-9010", "milestones", "verified")],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "VERIFIED_UNBOUND"},
        "resolution": "The higher plan connects to the same camera; the plan and team limit move together.",
    },
    "worker_crash": {
        "title": "Worker crash mid-purchase",
        "story": "An in-app Memories purchase is prepared. Run the worker with a crash, then recover.",
        "steps": [prepare_purchase(NURSERY, "nextnap"), hold_jobs()],
        "focus": {"device_id": NURSERY},
        "expected": {NURSERY: "PURCHASE_PENDING"},
        "resolution": "The recovered worker reuses the same provider key: one provider operation, then the plan appears.",
    },
}


def catalog() -> list[dict[str, Any]]:
    return [{"key": key, "title": s["title"], "story": s["story"], "resolution": s["resolution"],
             "focus": s["focus"], "expected": s["expected"], "steps": [step.__doc__ for step in s["steps"]]}
            for key, s in SCENARIOS.items()]


def run(key: str) -> dict[str, Any]:
    """Reset the demo data and build one scenario. Caller is authorized."""
    scenario = SCENARIOS[key]
    seed.load_fresh()
    log = []
    for step in scenario["steps"]:
        with transaction() as db:
            household = lock_household(db, seed.HOUSEHOLD)
            result = step(db, _parent(db), household)
            log.append({"step": step.__doc__, "result": result})
    return {"scenario": key, "title": scenario["title"], "focus": scenario["focus"],
            "expected": scenario["expected"], "log": log, "synthetic": True}
