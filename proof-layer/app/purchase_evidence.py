"""Purchase evidence: what the store says about a purchase, before and after it
reaches a camera.

A parent can pay on the website, in the App Store or Google Play, or hold an
activation code, and still see no plan on their camera. Entitlement claims
(claims.py) always name a camera, so "paid, but not connected to a camera"
needs its own record. This module keeps it:

- every provider event is append-only and versioned by the provider; a replay
  of the same event is a no-op, the same event with different content is a
  conflict;
- one household owns a purchase reference (``purchase_refs``), so evidence for
  one family's purchase can never be recorded under another family;
- a purchase's state is a pure function of its events: the highest provider
  version wins, an older event that arrives late changes nothing, and two
  different statuses at the same version are CONFLICTING, never guessed;
- a purchase is connected to a camera by an ordinary entitlement claim with
  the same (channel, external_ref), written through claims.record. There is
  no second entitlement engine: the camera's plan is still derived only from
  its claims;
- a refund of a connected purchase writes a revoked claim on that camera, so
  the camera's plan drops through the same derivation, and the history stays;
- a renewal (a confirmed later period) of a connected purchase is written to
  the same camera as another claim period, in the same transaction;
- a store grace period is written the same way while it is the store's latest
  word: the store says to keep access until the grace end. Billing retry,
  pause and expiry write nothing; the camera's paid period simply ends, and
  the purchase stays on its camera for recovery (app/provider_lifecycle.py).

Callers hold the household lock.
"""
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import claims, entitlements, provider_lifecycle
from .engine import DomainError
from .models import (EVIDENCE_CHANNELS, EVIDENCE_STATUSES, Device, EntitlementClaim, Household, PurchaseEvidence,
                     PurchaseRef)
from .security import current_trace_id, new_id

CHANNEL_LABELS = {"web": "Website", "apple": "App Store", "google": "Google Play",
                  "activation_code": "Activation code", "trial": "Trial", "seed": "Starting plan",
                  "simulated_purchase": "In-app purchase (simulated)"}


def purchase_state(events: Iterable[Any]) -> dict[str, Any]:
    """The state of one purchase from its provider events. Pure.

    The highest provider_version is the provider's latest word; anything older
    is history, whatever order it arrived in. Two different statuses at the top
    version mean the provider contradicted itself: CONFLICTING."""
    events = list(events)
    top = max(e.provider_version for e in events)
    at_top = sorted((e for e in events if e.provider_version == top), key=lambda e: (e.status, e.provider_event or ""))
    # A set: a re-delivered event is the same fact, not a second opinion
    # (found by the order-invariance property test).
    statuses = sorted({e.status for e in at_top})
    latest = at_top[0]
    return {
        "channel": latest.channel, "external_ref": latest.external_ref,
        "state": "conflicting" if len(statuses) > 1 else statuses[0],
        "conflicting_statuses": statuses if len(statuses) > 1 else [],
        "plan_code": latest.plan_code,
        "provider_version": top,
        "starts_at": latest.starts_at, "ends_at": latest.ends_at,
        # Every confirmed period, whatever version confirmed it: an older year
        # that arrives late is still a year the family paid for.
        "periods": sorted({(e.starts_at, e.ends_at, e.plan_code) for e in events if e.status == "verified"},
                          key=lambda p: (p[0], p[1] or p[0])),
        # Every grace window the store ever granted (history), and the one at the
        # top version. Only the current one is written to a camera, only while
        # grace is the latest word: an older or late grace event grants nothing
        # (adversarial review, 2026-10-11).
        "grace_windows": sorted({(e.starts_at, e.ends_at, e.plan_code) for e in events if e.status == "grace"},
                                key=lambda p: (p[0], p[1] or p[0])),
        "current_grace": sorted({(e.starts_at, e.ends_at, e.plan_code) for e in at_top if e.status == "grace"},
                                key=lambda p: (p[0], p[1] or p[0])),
        "provider_event": latest.provider_event,
        "events": len(events),
    }


def as_of(state: dict[str, Any], now: datetime) -> dict[str, Any]:
    """A purchase's store state at `now`. Pure.

    - A grace period whose end has passed, with no newer word from the store,
      is billing retry: the store keeps retrying and access stopped at the
      grace end.
    - A pause past its resume date, with no newer word, is still paused, but
      overdue: the store hasn't confirmed that it resumed."""
    if state["state"] == "grace" and state["ends_at"] is not None and state["ends_at"] <= now:
        return state | {"state": "billing_retry", "grace_ended": True}
    if state["state"] == "paused" and state["ends_at"] is not None and state["ends_at"] <= now:
        return state | {"resume_overdue": True}
    return state


def household_evidence(db: Session, household_id: str) -> list[PurchaseEvidence]:
    return list(db.execute(select(PurchaseEvidence).where(PurchaseEvidence.household_id == household_id)
                           .order_by(PurchaseEvidence.received_at, PurchaseEvidence.id)).scalars())


def bindings(db: Session, refs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], list[EntitlementClaim]]:
    """Camera claims for these purchase references, in any household. A claim
    with the purchase's (channel, external_ref) is what connects it to a camera."""
    refs = list(refs)
    if not refs:
        return {}
    found: dict[tuple[str, str], list[EntitlementClaim]] = {}
    for channel in {c for c, _ in refs}:
        wanted = [r for c, r in refs if c == channel]
        for claim in db.execute(select(EntitlementClaim).where(
                EntitlementClaim.channel == channel, EntitlementClaim.external_ref.in_(wanted))
                .order_by(EntitlementClaim.received_at, EntitlementClaim.id)).scalars():
            found.setdefault((claim.channel, claim.external_ref), []).append(claim)
    return found


def binding_view(household_id: str, bound: list[EntitlementClaim]) -> dict[str, Any]:
    """Where a purchase is connected. Another household's camera is never named."""
    if not bound:
        return {"bound": False, "device_id": None, "elsewhere": False, "claim_statuses": []}
    device = bound[0].device_id
    if bound[0].household_id != household_id:
        return {"bound": True, "device_id": None, "elsewhere": True, "claim_statuses": []}
    return {"bound": True, "device_id": device, "elsewhere": False,
            "claim_statuses": sorted({c.event_status for c in bound if c.device_id == device})}


def household_purchases(db: Session, household_id: str) -> list[dict[str, Any]]:
    """Every purchase this household has evidence for, with its derived state
    and where it is connected."""
    groups: dict[tuple[str, str], list[PurchaseEvidence]] = {}
    for e in household_evidence(db, household_id):
        groups.setdefault((e.channel, e.external_ref), []).append(e)
    bound = bindings(db, groups)
    result = []
    for key in sorted(groups):
        state = purchase_state(groups[key])
        result.append(state | {"binding": binding_view(household_id, bound.get(key, []))})
    return result


def _claim_owner(db: Session, channel: str, external_ref: str, household_id: str) -> str:
    db.execute(pg_insert(PurchaseRef).values(channel=channel, external_ref=external_ref,
                                             household_id=household_id).on_conflict_do_nothing())
    return db.execute(select(PurchaseRef.household_id).where(
        PurchaseRef.channel == channel, PurchaseRef.external_ref == external_ref)).scalar_one()


def record(db: Session, household: Household, *, channel: str, external_ref: str, plan_code: str,
           status: Optional[str], provider_version: int, starts_at: datetime, ends_at: Optional[datetime],
           catalog_version: str, provider_event: Optional[str] = None) -> dict[str, Any]:
    """Record one provider event about a purchase, given as an evidence status,
    a store event (translated by provider_lifecycle), or both when they agree.
    Simulated input in this prototype: no store is contacted."""
    if channel not in EVIDENCE_CHANNELS:
        raise DomainError(422, "UNKNOWN_CHANNEL", "unknown purchase channel", allowed=list(EVIDENCE_CHANNELS))
    if provider_event is not None:
        translated = provider_lifecycle.translate(channel, provider_event)
        if status is not None and status != translated:
            raise DomainError(422, "PROVIDER_EVENT_MISMATCH", f"{provider_event} means {translated}, not {status}")
        status = translated
    if status not in EVIDENCE_STATUSES:
        raise DomainError(422, "INVALID_STATUS", "unknown evidence status", allowed=list(EVIDENCE_STATUSES))
    if status in provider_lifecycle.SUBSCRIPTION_ONLY and channel == "activation_code":
        raise DomainError(422, "NOT_A_SUBSCRIPTION", "an activation code doesn't renew, so it has no billing state")
    if status == "grace" and ends_at is None:
        raise DomainError(422, "GRACE_NEEDS_END", "a grace period needs the store's grace end date")
    plans = entitlements.CATALOGS[catalog_version]["plans"]
    if plan_code not in plans or plan_code == "none":
        raise DomainError(422, "UNKNOWN_PLAN", "a purchase must name a plan in the catalog")
    if provider_version < 1:
        raise DomainError(422, "INVALID_VERSION", "provider versions start at 1")

    owner = _claim_owner(db, channel, external_ref, household.id)
    if owner != household.id:
        raise DomainError(409, "PURCHASE_OWNED_ELSEWHERE", "this purchase belongs to a different account")

    # provider_event joins the hash only when present, so earlier rows keep their hashes.
    request_hash = claims._hash(channel=channel, external_ref=external_ref, plan_code=plan_code, status=status,
                                provider_version=provider_version, starts_at=starts_at, ends_at=ends_at,
                                **({"provider_event": provider_event} if provider_event is not None else {}))
    existing = db.execute(select(PurchaseEvidence).where(
        PurchaseEvidence.channel == channel, PurchaseEvidence.external_ref == external_ref,
        PurchaseEvidence.provider_version == provider_version, PurchaseEvidence.status == status)
    ).scalar_one_or_none()
    events = [e for e in household_evidence(db, household.id)
              if (e.channel, e.external_ref) == (channel, external_ref)]
    before = purchase_state(events) if events else None
    if existing is not None:
        if existing.request_hash != request_hash:
            raise DomainError(409, "EVIDENCE_CONFLICT", "this provider event was already recorded with different content")
        return {"evidence_id": existing.id, "replayed": True, "before": _public(before), "after": _public(before),
                "revoked_claim": None, "renewed_claim": None}

    evidence = PurchaseEvidence(id=new_id("pev"), household_id=household.id, channel=channel,
                                external_ref=external_ref, plan_code=plan_code, status=status,
                                provider_version=provider_version, starts_at=starts_at, ends_at=ends_at,
                                provider_event=provider_event, request_hash=request_hash,
                                trace_id=current_trace_id())
    db.add(evidence)
    db.flush()
    after = purchase_state(events + [evidence])
    revoked = _revoke_if_refunded(db, household, after, catalog_version)
    renewed = apply_periods(db, household, after, catalog_version)
    if before is None or (before["state"], before["plan_code"]) != (after["state"], after["plan_code"]):
        household.state_revision += 1
    return {"evidence_id": evidence.id, "replayed": False, "before": _public(before), "after": _public(after),
            "revoked_claim": revoked, "renewed_claim": renewed}


def _revoke_if_refunded(db: Session, household: Household, state: dict[str, Any],
                        catalog_version: str) -> Optional[str]:
    """A refunded purchase that is connected to a camera in this household
    writes a revoked claim there. The camera's plan falls through the normal
    derivation; the active claim and the refund both stay on record."""
    if state["state"] != "refunded":
        return None
    active = db.execute(select(EntitlementClaim).where(
        EntitlementClaim.household_id == household.id, EntitlementClaim.channel == state["channel"],
        EntitlementClaim.external_ref == state["external_ref"], EntitlementClaim.event_status == "active")
    ).scalars().first()
    if active is None:
        return None
    device = db.execute(select(Device).where(Device.household_id == household.id,
                                             Device.id == active.device_id)).scalar_one()
    result = claims.record(db, household, device, channel=active.channel, external_ref=active.external_ref,
                           event_status="revoked", plan_code=active.plan_code, starts_at=active.starts_at,
                           ends_at=active.ends_at, catalog_version=catalog_version)
    return result["claim_id"]


def apply_periods(db: Session, household: Household, state: dict[str, Any], catalog_version: str,
                  device: Optional[Device] = None) -> Optional[str]:
    """Write every confirmed period of a purchase to its camera: a renewal, an
    extension (same start, later end) or an older period that arrived late.
    While the store's latest word is a grace period, its grace window is
    written too. Without `device` it applies only to a purchase already on a
    camera, so nobody has to connect a renewal again. A revoked purchase is not
    renewed: that is a conflict (see access). Returns the newest claim written."""
    if state["state"] not in ("verified", "grace"):
        return None
    existing = list(db.execute(select(EntitlementClaim).where(
        EntitlementClaim.household_id == household.id, EntitlementClaim.channel == state["channel"],
        EntitlementClaim.external_ref == state["external_ref"])).scalars())
    if any(c.event_status == "revoked" for c in existing):
        return None
    if device is None:
        if not existing:
            return None
        device = db.execute(select(Device).where(Device.household_id == household.id,
                                                 Device.id == existing[0].device_id)).scalar_one()
    have = {(c.starts_at, c.ends_at) for c in existing if c.event_status == "active"}
    written = None
    windows = state["periods"] + (state["current_grace"] if state["state"] == "grace" else [])
    for starts_at, ends_at, plan_code in windows:
        if (starts_at, ends_at) in have:
            continue
        written = claims.record(db, household, device, channel=state["channel"],
                                external_ref=state["external_ref"], event_status="active", plan_code=plan_code,
                                starts_at=starts_at, ends_at=ends_at, catalog_version=catalog_version)["claim_id"]
    return written


def _public(state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if state is None:
        return None
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in state.items()}
