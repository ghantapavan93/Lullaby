"""Simulated purchase intents: prepare, submit, confirm, recover.

State machine (build spec §9)::

    PREPARED -> CANCELLED_BEFORE_SUBMISSION
    PREPARED -> SUBMITTED -> CONFIRMED
                          -> FAILED
                          -> UNKNOWN -> CONFIRMED / FAILED / UNKNOWN

Guarantees and where they live
------------------------------
- One intent per (household, idempotency key): unique constraint
  ``uq_intents_idempotency`` plus the household lock. A matching retry returns
  the original intent *before* any freshness check; different content under
  the same key is ``IDEMPOTENCY_KEY_REUSED``.
- At most one upgrade in flight per camera (PREPARED, SUBMITTED or UNKNOWN):
  partial unique index ``uq_intents_one_live_upgrade``. A second tab or a
  second offer gets ``UPGRADE_IN_PROGRESS`` instead of a second charge.
- A confirmed purchase becomes a claim on its camera (``claims.record``). The
  camera's plan is the highest active claim, so a purchase never lowers it and
  never changes another camera.
- Nothing is prepared from a stale, foreign, offer-less or mismatched decision,
  and the price is always the server's snapshot, never the client's.
- The provider operation key (``op_<intent id>``) is fixed when the intent is
  prepared and reused on every retry; it is never regenerated because a call
  failed.
- PREPARED -> SUBMITTED is the **submission authorization boundary**. The
  worker rechecks eligibility under the household lock, commits SUBMITTED, and
  only then calls the provider. Changes to the household after that commit
  cannot recall the submission; at most they can lead to a later refund,
  which this prototype does not model.
- Provider success and application confirmation are separate commits in
  separate databases. If the process dies between them, the intent stays
  SUBMITTED; recovery asks the provider's ledger and records what it says.
  FAILED stays FAILED, PENDING becomes UNKNOWN, and UNKNOWN never becomes
  CONFIRMED without the provider saying SUCCEEDED.
"""
import hashlib
import json
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import claims, decisions, entitlements, provider
from .engine import DomainError
from .growth import record_detached
from .models import CapabilityDecision, Device, Household, IntentTransition, OutboxJob, PurchaseIntent
from .security import Principal, current_trace_id, lock_household, new_id

TERMINAL = {"CONFIRMED", "FAILED", "CANCELLED_BEFORE_SUBMISSION"}


def _fingerprint(**fields: Any) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def transition(db: Session, intent: PurchaseIntent, to_state: str, reason: str, actor: str,
               detail: Optional[dict[str, Any]] = None, *, initial: bool = False) -> None:
    db.add(IntentTransition(household_id=intent.household_id, intent_id=intent.id,
                            from_state=None if initial else intent.state, to_state=to_state,
                            reason=reason, detail=detail or {}, actor=actor, trace_id=current_trace_id()))
    intent.state = to_state


def view(intent: PurchaseIntent) -> dict[str, Any]:
    return {"intent_id": intent.id, "state": intent.state, "decision_id": intent.decision_id,
            "offer_id": intent.offer_id, "terms": intent.terms,
            "provider_operation_key": intent.provider_operation_key,
            "validated_state_revision": intent.validated_state_revision,
            "outcome": intent.outcome, "trace_id": intent.trace_id,
            "created_at": intent.created_at.isoformat() if intent.created_at else None,
            "simulated": True}


def _reject(principal: Principal, code: str, message: str, status: int = 409, **detail: Any) -> DomainError:
    record_detached("purchase_rejected", principal.household_id,
                    {"code": code, "reasons": detail.get("reasons", [])})
    return DomainError(status, code, message, **detail)


def prepare(db: Session, principal: Principal, *, decision_id: str, offer_id: str, idempotency_key: str,
            expected_amount_minor: Optional[int], expected_currency: Optional[str]) -> tuple[PurchaseIntent, bool]:
    """Caller holds the household lock, rechecked membership and authorized
    purchase.create. Returns (intent, replayed)."""
    household = db.get(Household, principal.household_id)
    fingerprint = _fingerprint(member_id=principal.member_id, decision_id=decision_id, offer_id=offer_id,
                               expected_amount_minor=expected_amount_minor, expected_currency=expected_currency)

    existing = db.execute(select(PurchaseIntent).where(
        PurchaseIntent.household_id == principal.household_id,
        PurchaseIntent.idempotency_key == idempotency_key)).scalar_one_or_none()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise _reject(principal, "IDEMPOTENCY_KEY_REUSED",
                          "this idempotency key was already used for a different purchase request",
                          original_intent_id=existing.id)
        return existing, True

    decision = db.execute(select(CapabilityDecision).where(
        CapabilityDecision.id == decision_id,
        CapabilityDecision.household_id == principal.household_id)).scalar_one_or_none()
    if decision is None:
        raise _reject(principal, "NOT_FOUND", "decision not found", status=404)

    reasons = decisions.staleness(db, decision, household)
    if reasons:
        raise _reject(principal, "STALE_DECISION",
                      "Things changed since this offer was shown. Nothing was charged. Check again for a current answer.",
                      reasons=reasons)
    if decision.status != "BLOCKED" or not decision.offer:
        raise _reject(principal, "NO_ELIGIBLE_OFFER",
                      "There is no upgrade that would unlock this feature for your current setup.",
                      reasons=[b["code"] for b in decision.blockers])
    offer = decision.offer
    if offer["offer_id"] != offer_id:
        raise _reject(principal, "OFFER_MISMATCH", "that offer is not the one attached to this decision")
    if (expected_amount_minor is not None and expected_amount_minor != offer["amount_minor"]) or \
            (expected_currency is not None and expected_currency != offer["currency"]):
        raise _reject(principal, "TERMS_MISMATCH",
                      "the price shown does not match the server's terms; nothing was charged",
                      server_terms={"amount_minor": offer["amount_minor"], "currency": offer["currency"]})

    live = db.execute(select(PurchaseIntent.id).where(
        PurchaseIntent.household_id == principal.household_id, PurchaseIntent.device_id == decision.device_id,
        PurchaseIntent.state.in_(("PREPARED", "SUBMITTED", "UNKNOWN")))).scalar_one_or_none()
    if live is not None:
        raise _reject(principal, "UPGRADE_IN_PROGRESS",
                      "Another upgrade for this camera is still being processed. Nothing new was charged.",
                      in_progress_intent_id=live)

    # Re-run the rules against this camera as it is now, not just the snapshot.
    device = db.get(Device, decision.device_id)
    result, _, _, _ = decisions.evaluate_now(db, household, decision.feature, device)
    live_offer = result["offer"]
    if result["status"] != "BLOCKED" or not live_offer or live_offer["offer_id"] != offer_id:
        raise _reject(principal, "STALE_DECISION", "eligibility changed; nothing was charged",
                      reasons=["ELIGIBILITY_CHANGED"])

    intent_id = new_id("pi")
    intent = PurchaseIntent(
        id=intent_id, household_id=principal.household_id, member_id=principal.member_id,
        decision_id=decision.id, device_id=decision.device_id, offer_id=offer_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        terms={k: offer[k] for k in ("plan_code", "amount_minor", "currency", "billing_period",
                                     "catalog_version", "policy_version", "feature")}
              | {"simulated": True, "device_id": decision.device_id},
        state="PREPARED", provider_operation_key=f"op_{intent_id}",
        validated_state_revision=household.state_revision, trace_id=current_trace_id())
    db.add(intent)
    db.flush()
    transition(db, intent, "PREPARED", "prepared", f"member:{principal.member_id}",
               {"decision_id": decision.id, "state_revision": household.state_revision}, initial=True)
    db.add(OutboxJob(household_id=principal.household_id, kind="submit_purchase",
                     dedupe_key=f"submit:{intent.id}", payload={"intent_id": intent.id},
                     trace_id=current_trace_id()))
    db.flush()
    return intent, False


# ------------------------------------------------------------ worker side

def authorize_submission(db: Session, intent_id: str, household_id: str, actor: str) -> Optional[PurchaseIntent]:
    """Phase 1 (app transaction). Returns the intent if the provider may be
    called, or None if it was cancelled or is already terminal."""
    household = lock_household(db, household_id)
    intent = db.execute(select(PurchaseIntent).where(
        PurchaseIntent.id == intent_id, PurchaseIntent.household_id == household_id)
        .with_for_update()).scalar_one()
    if intent.state in TERMINAL:
        return None
    if intent.state in ("SUBMITTED", "UNKNOWN"):
        return intent  # already authorized; a retry reuses the same operation key
    # PREPARED: recheck eligibility before crossing the boundary.
    device = db.get(Device, intent.device_id)
    result, _, _, _ = decisions.evaluate_now(db, household, intent.terms["feature"], device,
                                             ignore_intent_id=intent.id)
    still_valid = (household.state_revision == intent.validated_state_revision
                   and result["status"] == "BLOCKED" and result["offer"]
                   and result["offer"]["plan_code"] == intent.terms["plan_code"])
    if not still_valid:
        transition(db, intent, "CANCELLED_BEFORE_SUBMISSION", "eligibility_changed_before_submission", actor,
                   {"state_revision_now": household.state_revision,
                    "status_now": result["status"], "blockers_now": [b["code"] for b in result["blockers"]]})
        record_detached("purchase_cancelled_before_submission", household_id, {"intent_id": intent.id})
        return None
    transition(db, intent, "SUBMITTED", "submission_authorized", actor,
               {"provider_operation_key": intent.provider_operation_key})
    return intent


def call_provider(intent: PurchaseIntent) -> dict[str, Any]:
    """Outside any application transaction."""
    return provider.submit(intent.provider_operation_key, merchant_reference=intent.household_id,
                           amount_minor=intent.terms["amount_minor"], currency=intent.terms["currency"])


def record_outcome(db: Session, intent_id: str, household_id: str, record: Optional[dict[str, Any]],
                   actor: str, reason: str) -> PurchaseIntent:
    """Phase 2 (app transaction). `record` is the provider's durable record, or
    None when the outcome could not be learned."""
    household = lock_household(db, household_id)
    intent = db.execute(select(PurchaseIntent).where(
        PurchaseIntent.id == intent_id, PurchaseIntent.household_id == household_id)
        .with_for_update()).scalar_one()
    if intent.state in TERMINAL:
        return intent
    status = record["status"] if record else None
    if status == "SUCCEEDED":
        intent.outcome = record
        # The charge becomes one more claim on this camera; the camera's plan is
        # derived from all of its claims, so a cheaper purchase that settles last
        # can never lower it.
        granted = claims.record(
            db, household, db.get(Device, intent.device_id), channel="simulated_purchase",
            external_ref=intent.provider_operation_key, event_status="active",
            plan_code=intent.terms["plan_code"], starts_at=claims.utcnow(),
            ends_at=claims.utcnow() + timedelta(days=365), catalog_version=intent.terms["catalog_version"])
        transition(db, intent, "CONFIRMED", reason, actor,
                   {"provider": record, "camera_plan_before": granted["before"]["plan_code"],
                    "camera_plan_after": granted["after"]["plan_code"]})
        record_detached("purchase_confirmed", household_id, {"intent_id": intent.id,
                                                             "plan_code": intent.terms["plan_code"]})
    elif status == "FAILED":
        intent.outcome = record
        transition(db, intent, "FAILED", reason, actor, {"provider": record})
    else:
        if intent.state != "UNKNOWN":
            transition(db, intent, "UNKNOWN", reason, actor, {"provider": record})
        intent.outcome = record
    db.flush()
    return intent


def describe_plan(intent: PurchaseIntent) -> str:
    return entitlements.plan_label(intent.terms["catalog_version"], intent.terms["plan_code"])
