"""Entitlement claims: how a camera's plan is recorded and derived.

Nanit sells plans through several channels (the App Store, its website,
activation codes; Android purchases are managed on its website, and "google"
here is a modelling label) and attaches them to a camera. Customers report
payments, renewals and codes "not reflected" in an app; whatever the cause
in any real case, this module makes the camera's plan a derived fact:

- every channel event is an append-only claim, idempotent per
  (channel, external_ref, event_status, period); a replay is a no-op and the
  same key with different content is a conflict. A renewal is a new period;
- a purchase or code names one camera only, in every channel and period
  (database exclusion constraint);
- the plan, its state ("checking" while a purchase is unconfirmed) and the
  team-member limit are computed together by `entitlements.effective_plan`,
  so they can never be out of step;
- when a camera's derived plan changes, the household's commercial-state
  revision moves, so open offers built on the old plan go stale.

Callers hold the household lock.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import clock, entitlements
from .engine import DomainError
from .models import CLAIM_CHANNELS, Device, EntitlementClaim, Household, PurchaseRef
from .security import current_trace_id, new_id


def utcnow() -> datetime:
    return clock.now()


def device_claims(db: Session, household_id: str, device_id: str) -> list[EntitlementClaim]:
    return list(db.execute(select(EntitlementClaim).where(
        EntitlementClaim.household_id == household_id, EntitlementClaim.device_id == device_id)
        .order_by(EntitlementClaim.received_at, EntitlementClaim.id)).scalars())


def camera_plan(db: Session, household_id: str, device_id: str, catalog_version: str,
                now: Optional[datetime] = None) -> dict[str, Any]:
    return entitlements.effective_plan(device_claims(db, household_id, device_id), now or utcnow(),
                                       catalog_version)


def _canonical(value: Any) -> str:
    """The same instant in any offset is the same fact (critique 2026-10-11)."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _hash(**fields: Any) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=_canonical,
                                     separators=(",", ":")).encode()).hexdigest()


def _commercial_view(plan: dict[str, Any]) -> tuple:
    """Everything about a camera's plan that eligibility and offers depend on.
    Pending plans count: a newly pending higher plan changes what may be sold."""
    return plan["plan_code"], plan["state"], plan["member_cap"], tuple(plan["pending_plans"])


def _already_recorded(db: Session, device: Device, channel: str, external_ref: str, event_status: str,
                      starts_at: datetime, ends_at: Optional[datetime], request_hash: str,
                      before: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The answer when this channel event is already on record: a refusal if it
    belongs to another camera or differs, a replay if it is the same event,
    None if it is new."""
    # A purchase or code belongs to one camera; say so before anything else.
    other_camera = db.execute(select(EntitlementClaim.device_id).where(
        EntitlementClaim.channel == channel, EntitlementClaim.external_ref == external_ref,
        EntitlementClaim.device_id != device.id)).scalars().first()
    if other_camera is not None:
        code = "CODE_ALREADY_REDEEMED" if channel == "activation_code" else "CLAIM_DEVICE_MISMATCH"
        raise DomainError(409, code, "this purchase or code already belongs to another camera")
    existing = db.execute(select(EntitlementClaim).where(
        EntitlementClaim.channel == channel, EntitlementClaim.external_ref == external_ref,
        EntitlementClaim.event_status == event_status,
        EntitlementClaim.starts_at == starts_at,
        EntitlementClaim.ends_at.is_not_distinct_from(ends_at))).scalar_one_or_none()
    if existing is None:
        return None
    if existing.request_hash != request_hash:
        raise DomainError(409, "CLAIM_CONFLICT", "this channel event was already recorded with different content")
    return {"claim_id": existing.id, "replayed": True, "before": before, "after": before}


def record(db: Session, household: Household, device: Device, *, channel: str, external_ref: str,
           event_status: str, plan_code: str, starts_at: datetime, ends_at: Optional[datetime],
           catalog_version: str) -> dict[str, Any]:
    if channel not in CLAIM_CHANNELS:
        raise DomainError(422, "UNKNOWN_CHANNEL", "unknown purchase channel", allowed=list(CLAIM_CHANNELS))
    if event_status not in ("active", "pending", "revoked"):
        raise DomainError(422, "INVALID_STATUS", "claim status must be active, pending or revoked")
    plans = entitlements.CATALOGS[catalog_version]["plans"]
    if plan_code not in plans or plan_code == "none":
        raise DomainError(422, "UNKNOWN_PLAN", "a claim must name a plan in the catalog")
    request_hash = _hash(device_id=device.id, channel=channel, external_ref=external_ref,
                         event_status=event_status, plan_code=plan_code, starts_at=starts_at, ends_at=ends_at)

    # A code or store purchase is global, but the caller's lock covers only its
    # own household. Two households redeeming one code at the same moment each
    # inserted a row, then each waited on the other's to check the one-camera
    # exclusion constraint: a deadlock, and a 500 for one of them (the flaky
    # race test, 2026-10-11). One lock per purchase serializes them, so the
    # second sees the first's row and gets a clean 409.
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"claim:{channel}:{external_ref}"))))
    before = camera_plan(db, household.id, device.id, catalog_version)
    owner = db.execute(select(PurchaseRef.household_id).where(
        PurchaseRef.channel == channel, PurchaseRef.external_ref == external_ref)).scalar_one_or_none()
    if owner is not None and owner != household.id:
        # Another family's purchase can't be put on this family's camera
        # (critique 2026-10-11: possible through a channel event before).
        raise DomainError(409, "PURCHASE_OWNED_ELSEWHERE", "this purchase belongs to a different account")
    settled = _already_recorded(db, device, channel, external_ref, event_status, starts_at, ends_at, request_hash,
                                before)
    if settled is not None:
        return settled

    claim = EntitlementClaim(id=new_id("clm"), household_id=household.id, device_id=device.id, channel=channel,
                             external_ref=external_ref, event_status=event_status, plan_code=plan_code,
                             starts_at=starts_at, ends_at=ends_at, request_hash=request_hash,
                             trace_id=current_trace_id())
    # Codes and store events are global, but the household lock serializes only
    # this household. If another household recorded the same event first, the
    # unique index stops this insert; answer as if we had seen it (found by
    # mutation testing: this race used to surface as a 500).
    try:
        with db.begin_nested():
            db.add(claim)
            db.flush()
    except IntegrityError:
        settled = _already_recorded(db, device, channel, external_ref, event_status, starts_at, ends_at,
                                    request_hash, before)
        if settled is not None:
            return settled
        raise
    after = camera_plan(db, household.id, device.id, catalog_version)
    if _commercial_view(before) != _commercial_view(after):
        household.state_revision += 1
    return {"claim_id": claim.id, "replayed": False, "before": before, "after": after}
