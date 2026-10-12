"""Purchase to access: one answer to "I paid for this. Why can't I use it?"

For one camera, the answer is a status plus five stages a parent can read:

    Purchase -> Account -> Camera -> Plan -> Feature

Each stage is verified, needs an action, waiting, blocked, conflicting, not
yet known, or not needed. The status is chosen by a fixed precedence from
facts this application already keeps; nothing here is a second entitlement
engine:

- purchase state        purchase_evidence.purchase_state (provider events)
- camera binding        entitlement claims with the purchase's channel and ref
- the camera's plan     claims.camera_plan -> entitlements.effective_plan
- in-flight purchases   decisions.unconfirmed_plans and purchase intents
- feature and hardware  entitlements.evaluate, with the camera's own mount
- verification         access_checks (append-only)
- store billing state  grace, billing retry, pause, expiry (app/provider_lifecycle.py)

Reading a status writes nothing (only the authorization record every request
leaves). The two commands are explicit:

- ``bind`` connects a verified purchase to a camera. It holds the household
  lock, requires the state revision the parent was looking at, is idempotent
  per household and key, refuses to move a purchase that is on another camera
  and refuses a purchase the camera already has. It writes an ordinary claim
  through claims.record. Completing it is an *operation*; it is not yet a
  verified capability.
- ``verify`` re-reads everything in a new transaction and records an access
  check: the *capability* verified, with what was usable.

A third outcome, the parent actually using the feature, is a client event
(``access_feature_opened``). The three are kept apart and never inferred from
one another.

No status ever carries an offer. A parent who already paid is never asked to
pay again from this surface.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import claims, clock, decisions, entitlements, growth, purchase_evidence
from .engine import DomainError
from .models import (AccessAction, AccessCheck, AnalyticsEvent, Device, EntitlementClaim, Household,
                     PurchaseIntent)
from .security import Principal, POLICY, current_trace_id, new_id

# Fixed precedence for a camera's headline status (first match wins).
STATUSES = (
    "CONFLICTING_EVIDENCE",           # the store contradicted itself, or the store and the camera disagree
    "ASSIGNED_PENDING_CONFIRMATION",  # connected; capability not yet verified
    "PROVIDER_UNKNOWN",               # an in-app purchase whose outcome we could not learn
    "PURCHASE_PENDING",               # the store has not confirmed yet
    "BILLING_RETRY",                  # a renewal payment failed; no access while the store retries
    "PLAN_PAUSED",                    # paused in the store; no access until it resumes
    "VERIFIED_UNBOUND",               # paid and confirmed, not connected to a camera
    "REFUNDED_OR_REVOKED",            # a higher plan was refunded or revoked
    "BILLING_GRACE",                # a renewal payment failed; the store keeps access until the grace end
    "PLAN_SCHEDULED",                 # paid and connected; its period hasn't started yet
    "PLAN_EXPIRED",                   # a higher plan's period ended: no renewal reported, or the store reported expiry
    "PURCHASE_UNVERIFIED",            # the store could not verify the purchase
    "ACTIVE_FEATURE_USABLE",
    "ACTIVE_HARDWARE_BLOCKED",        # paid and active; the mount is the limit
    "NO_PURCHASE",
)
# Only reached when a specific purchase or feature is asked about.
FOCUSED_STATUSES = ("ALREADY_ASSIGNED", "DUPLICATE_PURCHASE", "PLAN_EXCLUDES_FEATURE", "PURCHASE_NOT_FOUND")
STAGES = ("purchase", "account", "camera", "plan", "feature")
STAGE_LABELS = {"purchase": "Purchase", "account": "Account", "camera": "Camera", "plan": "Plan",
                "feature": "Feature"}
STATE_LABELS = {"verified": "Verified", "action": "Needs an action", "waiting": "Waiting", "blocked": "Blocked",
                "conflicting": "Conflicting", "unknown": "Not yet known", "not_needed": "Not needed",
                "unchecked": "Not checked yet"}
# Channels that grant without a payment: a trial does not make a paid purchase a duplicate.
FREE_CHANNELS = ("trial", "seed")

# The stage pattern for each status. Stages after the first unresolved one are
# "unknown": we don't claim anything we haven't reached.
STAGE_PATTERN: dict[str, tuple[str, ...]] = {
    "ACTIVE_FEATURE_USABLE":         ("verified", "verified", "verified", "verified", "verified"),
    "ACTIVE_HARDWARE_BLOCKED":       ("verified", "verified", "verified", "verified", "blocked"),
    "PLAN_EXCLUDES_FEATURE":         ("verified", "verified", "verified", "blocked", "unknown"),
    "ASSIGNED_PENDING_CONFIRMATION": ("verified", "verified", "verified", "verified", "waiting"),
    # The plan is known (the purchase says which); it waits for the connect.
    "VERIFIED_UNBOUND":              ("verified", "verified", "action", "waiting", "unknown"),
    "DUPLICATE_PURCHASE":            ("verified", "verified", "blocked", "verified", "unknown"),
    "ALREADY_ASSIGNED":              ("verified", "verified", "blocked", "unknown", "unknown"),
    "PURCHASE_PENDING":              ("waiting", "verified", "unknown", "unknown", "unknown"),
    "PROVIDER_UNKNOWN":              ("unknown", "verified", "unknown", "unknown", "unknown"),
    "CONFLICTING_EVIDENCE":          ("conflicting", "verified", "unknown", "unknown", "unknown"),
    "REFUNDED_OR_REVOKED":           ("blocked", "verified", "verified", "blocked", "blocked"),
    "PLAN_EXPIRED":                  ("blocked", "verified", "verified", "blocked", "blocked"),
    "PLAN_SCHEDULED":                ("verified", "verified", "verified", "waiting", "unknown"),
    "BILLING_GRACE":                 ("action", "verified", "verified", "verified", "verified"),
    "BILLING_RETRY":                 ("action", "verified", "verified", "blocked", "blocked"),
    "PLAN_PAUSED":                   ("waiting", "verified", "verified", "blocked", "blocked"),
    "PURCHASE_UNVERIFIED":           ("action", "verified", "unknown", "unknown", "unknown"),
    "PURCHASE_NOT_FOUND":            ("unknown", "unknown", "unknown", "unknown", "unknown"),
    "NO_PURCHASE":                   ("not_needed", "verified", "not_needed", "not_needed", "not_needed"),
}

# What the server allows next. "connect" becomes "ask_owner" for members who
# cannot manage plans. There is no "buy" action here, by design.
ACTIONS: dict[str, tuple[str, ...]] = {
    "ACTIVE_FEATURE_USABLE": ("verify",),
    "ACTIVE_HARDWARE_BLOCKED": ("mount_guide", "verify"),
    "PLAN_EXCLUDES_FEATURE": (),
    "ASSIGNED_PENDING_CONFIRMATION": ("verify",),
    "VERIFIED_UNBOUND": ("connect",),
    "DUPLICATE_PURCHASE": ("contact_support",),
    "ALREADY_ASSIGNED": ("contact_support",),
    "PURCHASE_PENDING": ("check_again",),
    "PROVIDER_UNKNOWN": ("check_again",),
    "CONFLICTING_EVIDENCE": ("contact_support",),
    "REFUNDED_OR_REVOKED": ("contact_support",),
    "PLAN_EXPIRED": ("check_again", "contact_support"),
    "PLAN_SCHEDULED": ("check_again",),
    "BILLING_GRACE": ("check_again",),
    "BILLING_RETRY": ("check_again", "contact_support"),
    "PLAN_PAUSED": ("check_again",),
    "PURCHASE_UNVERIFIED": ("check_again", "contact_support"),
    "PURCHASE_NOT_FOUND": ("check_again", "contact_support"),
    "NO_PURCHASE": (),
}
USABLE = "ACTIVE_FEATURE_USABLE"
ENDED_IS_NEWS = timedelta(days=30)
# Where a parent fixes a renewal payment, and who reports an expiry. Words only:
# the prototype links to no store.
PAYMENT_WHERE = {"apple": "in your App Store account", "google": "in your Google Play account",
                 "web": "in your account on the website"}
STORE_NAME = {"apple": "The App Store", "google": "Google Play", "web": "The website"}


def utcnow() -> datetime:
    return clock.now()


def _fingerprint(**fields: Any) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def can_bind(principal: Principal) -> bool:
    return principal.role in POLICY["access.bind"]


# ---------------------------------------------------------------- the read model

def feature_matrix(plan_code: str, mount: str, catalog_version: str, policy_version: str) -> list[dict[str, Any]]:
    """Every feature on this camera under this plan, by the same rules decisions use."""
    rows = []
    for key, policy in entitlements.POLICIES[policy_version]["features"].items():
        result = entitlements.evaluate(feature=key, plan_code=plan_code, mounts=[mount],
                                       catalog_version=catalog_version, policy_version=policy_version,
                                       with_offer=False)
        kinds = sorted({b["kind"] for b in result["blockers"]})
        rows.append({"feature": key, "label": policy["label"], "parent_value": policy["parent_value"],
                     "included": plan_code in policy["plans"], "status": result["status"],
                     "usable": result["status"] == "AVAILABLE", "blocked_by": kinds,
                     "blockers": result["blockers"]})
    return rows


def _rank(catalog_version: str, plan_code: str) -> int:
    return entitlements.CATALOGS[catalog_version]["plans"].get(plan_code, {"rank": 0})["rank"]


def assess(db: Session, household: Household, device: Device, *, member_can_bind: bool,
           feature: Optional[str] = None, purchase: Optional[tuple[str, str]] = None,
           now: Optional[datetime] = None, pending_verification_overlay: bool = True) -> dict[str, Any]:
    """The canonical access decision for one camera. Deterministic and read-only.

    1. The caller and household are already resolved (authentication, scoping).
    2. The camera is already loaded within the household.
    3. Load the household's purchase evidence and group it by purchase.
    4. Per purchase, the highest provider version wins; disagreement there is CONFLICTING.
    5. Find where each purchase is connected (claims with its channel and ref).
    6. Derive the camera's plan from its claims.
    7. Collect unconfirmed purchases for this camera (pending claims, in-flight intents).
    8. Evaluate every feature against the plan and the camera's mount.
    9. Compare the last connect command with the last verification check.
    10. Choose the status by fixed precedence (or for the purchase or feature asked about).
    11. Derive the stages and the allowed actions from the status and the member's permissions.
    12. Return it; nothing is written.
    """
    now = now or utcnow()
    catalog_version, policy_version = decisions.active_versions(db)
    plans = entitlements.CATALOGS[catalog_version]["plans"]
    trace: list[dict[str, Any]] = []

    def step(n: int, rule: str, result: Any) -> None:
        trace.append({"step": n, "rule": rule, "result": result})

    step(1, "caller and household resolved from the members table", {"household_id": household.id})
    step(2, "camera loaded within the household", {"device_id": device.id, "mount": device.mount})
    purchases = [purchase_evidence.as_of(p, now) for p in purchase_evidence.household_purchases(db, household.id)]
    step(3, "purchase evidence grouped by purchase", {"purchases": len(purchases)})
    step(4, "highest provider version wins; disagreement is conflicting",
         [{"ref": f"{p['channel']}:{p['external_ref']}", "state": p["state"], "version": p["provider_version"]}
          for p in purchases])
    step(5, "camera binding from claims with the same channel and ref",
         [{"ref": f"{p['channel']}:{p['external_ref']}", "device_id": p["binding"]["device_id"],
           "elsewhere": p["binding"]["elsewhere"]} for p in purchases])

    plan = claims.camera_plan(db, household.id, device.id, catalog_version, now)
    current_rank = _rank(catalog_version, plan["plan_code"])
    paid_rank = max((_rank(catalog_version, t["plan_code"]) for t in plan["transactions"]
                     if t["state"] == "active" and t["channel"] not in FREE_CHANNELS), default=0)
    step(6, "camera plan derived from its claims (entitlements.effective_plan)",
         {"plan_code": plan["plan_code"], "state": plan["state"]})

    intents = list(db.execute(select(PurchaseIntent.state, PurchaseIntent.terms).where(
        PurchaseIntent.household_id == household.id, PurchaseIntent.device_id == device.id,
        PurchaseIntent.state.in_(decisions.LIVE_INTENT_STATES))))
    unconfirmed = decisions.unconfirmed_plans(db, household.id, device, catalog_version, plan["pending_plans"])
    provider_unknown = [terms["plan_code"] for state, terms in intents if state == "UNKNOWN"]
    step(7, "unconfirmed purchases for this camera",
         {"pending_or_in_flight": unconfirmed, "provider_unknown": provider_unknown})

    features = feature_matrix(plan["plan_code"], device.mount, catalog_version, policy_version)
    included = [f for f in features if f["included"]]
    usable = sorted(f["feature"] for f in features if f["usable"])
    hardware_blocked = sorted(f["feature"] for f in included if f["blocked_by"] == ["hardware"])
    step(8, "features evaluated against plan and mount (entitlements.evaluate)",
         {"usable": usable, "hardware_blocked": hardware_blocked})

    last_bind = last_connect(db, household.id, device.id)
    last_check = latest_check(db, household.id, device.id)
    bound_refs = sorted(f"{t['channel']}:{t['external_ref']}" for t in plan["transactions"] if t["state"] == "active")
    fingerprint = _fingerprint(plan=plan["plan_code"], state=plan["state"], usable=usable,
                               hardware_blocked=hardware_blocked, mount=device.mount, bound=bound_refs)
    awaiting_verification = last_bind is not None and (last_check is None or last_check.created_at <= last_bind.created_at)
    # "Connected, checking" only while the connected purchase is still what the
    # camera has: it must never hide a refund that came after the connect.
    connected_still_active = last_bind is not None and any(
        (t["channel"], t["external_ref"]) == (last_bind.channel, last_bind.external_ref) and t["state"] == "active"
        for t in plan["transactions"])
    overlay_plan = (last_bind.outcome.get("plan_code") if awaiting_verification and pending_verification_overlay
                    and connected_still_active else None)
    step(9, "last connect command compared with last verification check",
         {"last_connect_at": last_bind.created_at.isoformat() if last_bind else None,
          "last_check_at": last_check.created_at.isoformat() if last_check else None,
          "awaiting_verification": awaiting_verification,
          "matches_last_check": last_check is not None and last_check.fingerprint == fingerprint})

    def relevant(p: dict[str, Any]) -> bool:
        """Unconnected purchases matter to every camera they would improve;
        connected ones only to their own camera. An unconnected purchase whose
        period has ended is history, not something to connect."""
        if not p["binding"]["bound"] and (_ended(p, now) or p["state"] in ("billing_retry", "paused", "expired")):
            return False
        if p["binding"]["bound"]:
            return p["binding"]["device_id"] == device.id
        # Compared with what was paid for, not with a trial: a trial must not
        # hide a paid purchase of the same plan waiting to be connected.
        return _rank(catalog_version, p["plan_code"]) > paid_rank

    mine = [p for p in purchases if relevant(p)]
    revoked_higher = [t for t in plan["transactions"]
                      if t["state"] == "revoked" and _rank(catalog_version, t["plan_code"]) > current_rank]
    # An ending is news for a while, then history: older endings don't outrank a working camera.
    scheduled_higher = [t for t in plan["transactions"]
                        if t["state"] == "scheduled" and _rank(catalog_version, t["plan_code"]) > current_rank]
    expired_higher = [t for t in plan["transactions"]
                      if t["state"] == "lapsed" and _rank(catalog_version, t["plan_code"]) > current_rank
                      and t["last_ended"] and now - datetime.fromisoformat(t["last_ended"]) <= ENDED_IS_NEWS]
    focus: Optional[dict[str, Any]] = None
    reason: dict[str, Any] = {}

    if purchase is not None:
        status, focus, reason = _purchase_focus(purchase, purchases, plan, device, catalog_version, paid_rank, now)
    else:
        status = None
    if status is None:
        status, focus, reason = _camera_status(
            awaiting_plan=overlay_plan, mine=mine, provider_unknown=provider_unknown,
            unconfirmed=unconfirmed, current_rank=current_rank, revoked_higher=revoked_higher,
            expired_higher=expired_higher, scheduled_higher=scheduled_higher, plan=plan, included=included,
            usable=usable,
            catalog_version=catalog_version)
    if focus is None:
        # The purchase behind this camera's plan, for the stage facts.
        connected = [p for p in mine if p["binding"]["device_id"] == device.id and p["state"] in ("verified", "grace")]
        focus = max(connected, key=lambda p: _rank(catalog_version, p["plan_code"]), default=None)
    if feature is not None and status in (USABLE, "ACTIVE_HARDWARE_BLOCKED", "NO_PURCHASE"):
        status, reason = _feature_focus(feature, features, status, reason)
    step(10, "status by fixed precedence", {"status": status, "reason": reason})

    actions = [("ask_owner" if a == "connect" and not member_can_bind else a) for a in ACTIONS[status]]
    if reason.get("trial"):
        actions = []             # a trial that ended isn't a store record to check or a case for support
    elif reason.get("store_reported"):
        actions = [a for a in actions if a != "check_again"]     # the store already gave its answer
    preview = None
    if status == "VERIFIED_UNBOUND" and focus is not None:
        # What connecting would change on this camera, by the same rules: a
        # parent sees hardware limits before connecting, not after.
        would = feature_matrix(focus["plan_code"], device.mount, catalog_version, policy_version)
        preview = {"plan_code": focus["plan_code"],
                   "usable": [f["label"] for f in would if f["usable"]],
                   "hardware_limited": [f["label"] for f in would if f["included"] and f["blocked_by"] == ["hardware"]]}
    stages = _stages(status, focus=focus, plan=plan, device=device, features=features,
                     member_can_bind=member_can_bind, catalog_version=catalog_version, reason=reason,
                     checked=last_check is not None and last_check.fingerprint == fingerprint)
    step(11, "stages and allowed actions from status and permissions", {"allowed_actions": actions})
    step(12, "returned; nothing written", None)

    return {
        "device": {"id": device.id, "label": device.label, "model": device.model, "mount": device.mount,
                   "mount_label": entitlements.MOUNT_LABELS.get(device.mount, device.mount)},
        "status": status, "headline": HEADLINES[status], "message": _message(status, focus, plan, device, features,
                                                                             catalog_version, reason),
        "reason": reason,
        "stages": stages, "allowed_actions": actions,
        "plan": {k: plan[k] for k in ("plan_code", "plan_label", "state", "pending_plans")},
        "features": [{k: f[k] for k in ("feature", "label", "parent_value", "included", "status", "usable",
                                        "blocked_by")} for f in features],
        "usable_features": usable, "hardware_blocked_features": hardware_blocked,
        "focus_purchase": _public(focus),
        "periods": camera_periods(db, household.id, device.id, catalog_version, now,
                                  grace={(p["channel"], p["external_ref"], s, e)
                                         for p in purchases for s, e, _plan in p["grace_windows"]}),
        "connect_preview": preview,
        "verification": {"awaiting": awaiting_verification,
                         "last_check": check_view(last_check) if last_check else None,
                         "matches_last_check": last_check is not None and last_check.fingerprint == fingerprint},
        "fingerprint": fingerprint,
        "state_revision": household.state_revision,
        "catalog_version": catalog_version, "policy_version": policy_version,
        "offer": None,
        "trace": trace,
    }


def camera_periods(db: Session, household_id: str, device_id: str, catalog_version: str,
                   now: datetime, grace: frozenset | set = frozenset()) -> list[dict[str, Any]]:
    """The camera's plan periods, newest first: what a parent can read as its
    plan history. Purchase references are left out; `purchase` groups the
    periods of one purchase (a renewal is a second period of the same one).
    `grace` holds (channel, ref, start, end) of store grace windows: a grace
    window is marked as one and is never counted as a renewal."""
    groups: dict[tuple[str, str], int] = {}
    revoked = {(c.channel, c.external_ref) for c in claims.device_claims(db, household_id, device_id)
               if c.event_status == "revoked"}
    out = []
    for c in claims.device_claims(db, household_id, device_id):
        if c.event_status != "active":
            continue
        key = (c.channel, c.external_ref)
        groups.setdefault(key, len(groups) + 1)
        state = ("refunded" if key in revoked and c.channel not in FREE_CHANNELS else "revoked" if key in revoked
                 else "scheduled" if c.starts_at > now else "ended" if c.ends_at and c.ends_at <= now else "active")
        out.append({"purchase": groups[key], "plan_code": c.plan_code, "plan_label": _plan_label(catalog_version, c.plan_code),
                    "channel": purchase_evidence.CHANNEL_LABELS.get(c.channel, c.channel), "trial": c.channel in FREE_CHANNELS,
                    "starts_at": c.starts_at.isoformat(), "ends_at": c.ends_at.isoformat() if c.ends_at else None,
                    "state": state, "grace": (c.channel, c.external_ref, c.starts_at, c.ends_at) in grace})
    out.sort(key=lambda p: p["starts_at"], reverse=True)
    paid = [p for p in out if not p["grace"]]
    counts: dict[int, int] = {}
    for p in paid:
        counts[p["purchase"]] = counts.get(p["purchase"], 0) + 1
    for p in out:
        p["renewal"] = not p["grace"] and counts[p["purchase"]] > 1 and p["starts_at"] != min(
            q["starts_at"] for q in paid if q["purchase"] == p["purchase"])
    return out


def _store_ended_camera_active(p: dict[str, Any], plan: dict[str, Any]) -> bool:
    """The store's latest word takes access away, yet a claim from this purchase
    still covers now on the camera: a grace window the store later cut short,
    or an expiry inside a paid period. Claims are append-only, so this is shown
    as a contradiction for support, never silently honoured or hidden
    (adversarial review, 2026-10-11)."""
    return (p["binding"]["bound"] and p["state"] in ("billing_retry", "paused", "expired")
            and any((t["channel"], t["external_ref"]) == (p["channel"], p["external_ref"]) and t["state"] == "active"
                    for t in plan["transactions"]))


def _ended(p: dict[str, Any], now: datetime) -> bool:
    """Every confirmed period is over (an extension or a later year keeps it current)."""
    ends = [e for _, e, _plan in p.get("periods", [])] or [p["ends_at"]]
    return p["state"] == "verified" and None not in ends and max(ends) <= now


def _purchase_focus(purchase: tuple[str, str], purchases: list[dict[str, Any]], plan: dict[str, Any],
                    device: Device, catalog_version: str, paid_rank: int, now: datetime):
    channel, ref = purchase
    found = next((p for p in purchases if (p["channel"], p["external_ref"]) == (channel, ref)), None)
    if found is None:
        on_camera = next((t for t in plan["transactions"] if (t["channel"], t["external_ref"]) == (channel, ref)), None)
        if on_camera is None:
            # Missing and another family's purchase look the same: nothing leaks.
            return "PURCHASE_NOT_FOUND", None, {"code": "PURCHASE_NOT_FOUND"}
        return None, None, {}    # a purchase recorded straight on this camera (trial, in-app): camera status applies
    binding = found["binding"]
    if found["state"] == "conflicting":
        return "CONFLICTING_EVIDENCE", found, {"code": "PROVIDER_CONTRADICTION", "statuses": found["conflicting_statuses"]}
    if binding["bound"] and (binding["elsewhere"] or binding["device_id"] != device.id):
        return "ALREADY_ASSIGNED", found, {"code": "BOUND_TO_ANOTHER_CAMERA", "device_id": binding["device_id"],
                                           "elsewhere": binding["elsewhere"]}
    if found["state"] == "refunded":
        return "REFUNDED_OR_REVOKED", found, {"code": "PURCHASE_REFUNDED"}
    if found["state"] == "pending":
        return "PURCHASE_PENDING", found, {"code": "STORE_NOT_CONFIRMED"}
    if found["state"] == "unverified":
        return "PURCHASE_UNVERIFIED", found, {"code": "STORE_COULD_NOT_VERIFY"}
    if _store_ended_camera_active(found, plan):
        return "CONFLICTING_EVIDENCE", found, {"code": "STORE_ENDED_CAMERA_ACTIVE", "store_state": found["state"]}
    if found["state"] in ("billing_retry", "paused", "expired"):
        return _billing_status(found)
    if binding["bound"] and "revoked" in binding["claim_statuses"]:
        return "CONFLICTING_EVIDENCE", found, {"code": "STORE_ACTIVE_CAMERA_REVOKED"}
    if _ended(found, now):
        ends = [e for _, e, _plan in found.get("periods", []) if e] or [found["ends_at"]]
        return "PLAN_EXPIRED", found, {"code": "PERIOD_ENDED", "plan_code": found["plan_code"],
                                       "ended_at": max(ends).isoformat()}
    if not binding["bound"]:
        if paid_rank >= _rank(catalog_version, found["plan_code"]):
            return "DUPLICATE_PURCHASE", found, {"code": "CAMERA_ALREADY_HAS_PLAN", "camera_plan": plan["plan_code"]}
        return "VERIFIED_UNBOUND", found, {"code": "NOT_CONNECTED"}
    return None, found, {}       # verified and connected here: the camera status applies


def _billing_status(p: dict[str, Any]):
    """The status for a purchase the store says is retrying, paused or expired."""
    if p["state"] == "billing_retry":
        return "BILLING_RETRY", p, {"code": "STORE_RETRYING_PAYMENT", "plan_code": p["plan_code"],
                                    "grace_ended": p.get("grace_ended", False)}
    if p["state"] == "paused":
        return "PLAN_PAUSED", p, {"code": "STORE_PAUSED", "plan_code": p["plan_code"],
                                  "resumes_at": p["ends_at"].isoformat() if p["ends_at"] else None,
                                  "resume_overdue": p.get("resume_overdue", False)}
    ended = p["ends_at"] or p["starts_at"]
    return "PLAN_EXPIRED", p, {"code": "STORE_REPORTED_EXPIRED", "plan_code": p["plan_code"],
                               "ended_at": ended.isoformat(), "store_reported": True}


def _camera_status(*, awaiting_plan: Optional[str], mine: list[dict[str, Any]], provider_unknown: list[str],
                   unconfirmed: list[str], current_rank: int, revoked_higher: list[dict[str, Any]],
                   expired_higher: list[dict[str, Any]], scheduled_higher: list[dict[str, Any]],
                   plan: dict[str, Any], included: list[dict[str, Any]], usable: list[str], catalog_version: str):
    def first(*states: str, bound: Optional[bool] = None) -> Optional[dict[str, Any]]:
        candidates = [p for p in mine if p["state"] in states
                      and (bound is None or p["binding"]["bound"] == bound)]
        return max(candidates, key=lambda p: (_rank(catalog_version, p["plan_code"]), p["external_ref"]),
                   default=None)

    def lost(state: str) -> Optional[dict[str, Any]]:
        """A purchase on this camera, in this store state, whose plan the camera no longer has."""
        p = first(state, bound=True)
        return p if p and _rank(catalog_version, p["plan_code"]) > current_rank else None

    # Contradictory store evidence outranks "connected, checking": calm pending
    # copy must never sit on top of a contradiction (external review, 2026-10-11).
    conflicting = first("conflicting")
    if conflicting:
        return "CONFLICTING_EVIDENCE", conflicting, {"code": "PROVIDER_CONTRADICTION",
                                                     "statuses": conflicting["conflicting_statuses"]}
    store_active_camera_revoked = next((p for p in mine if p["state"] == "verified" and p["binding"]["bound"]
                                        and "revoked" in p["binding"]["claim_statuses"]), None)
    if store_active_camera_revoked:
        return "CONFLICTING_EVIDENCE", store_active_camera_revoked, {"code": "STORE_ACTIVE_CAMERA_REVOKED"}
    ended_early = next((p for p in mine if _store_ended_camera_active(p, plan)), None)
    if ended_early:
        return "CONFLICTING_EVIDENCE", ended_early, {"code": "STORE_ENDED_CAMERA_ACTIVE",
                                                     "store_state": ended_early["state"]}
    if awaiting_plan:
        return "ASSIGNED_PENDING_CONFIRMATION", None, {"code": "CONNECTED_NOT_VERIFIED", "plan_code": awaiting_plan}
    if provider_unknown:
        return "PROVIDER_UNKNOWN", None, {"code": "PROVIDER_OUTCOME_UNKNOWN", "plans": provider_unknown}
    pending = first("pending")
    if pending or any(_rank(catalog_version, c) > current_rank for c in unconfirmed):
        return "PURCHASE_PENDING", pending, {"code": "STORE_NOT_CONFIRMED", "plans": unconfirmed}
    # A higher plan the store is holding outranks connecting a lower purchase
    # (adversarial review, 2026-10-11).
    for state in ("billing_retry", "paused"):
        held = lost(state)
        if held:
            return _billing_status(held)
    # A purchase in grace still has the store's access: it can be connected.
    unbound = first("verified", "grace")
    if unbound and not unbound["binding"]["bound"]:
        return "VERIFIED_UNBOUND", unbound, {"code": "NOT_CONNECTED"}
    refunded = first("refunded")
    if revoked_higher or refunded:
        return "REFUNDED_OR_REVOKED", refunded, {"code": "PURCHASE_REFUNDED",
                                                 "plans": sorted({t["plan_code"] for t in revoked_higher})}
    # Grace matters only when its plan is the one the camera runs on; a higher
    # plan from another purchase loses nothing.
    grace = first("grace", bound=True)
    if grace and _rank(catalog_version, grace["plan_code"]) >= current_rank:
        return "BILLING_GRACE", grace, {"code": "STORE_GRACE_PERIOD", "plan_code": grace["plan_code"],
                                        "grace_ends_at": grace["ends_at"].isoformat()}
    if scheduled_higher:
        soonest = min(scheduled_higher, key=lambda t: t["next_start"])
        upcoming = next((p for p in mine if (p["channel"], p["external_ref"]) == (soonest["channel"], soonest["external_ref"])),
                        None)
        return "PLAN_SCHEDULED", upcoming, {"code": "PERIOD_NOT_STARTED", "plan_code": soonest["plan_code"],
                                            "starts_at": soonest["next_start"]}
    if expired_higher:
        latest = max(expired_higher, key=lambda t: t["last_ended"])
        ended = next((p for p in mine if (p["channel"], p["external_ref"]) == (latest["channel"], latest["external_ref"])),
                     None)
        if ended is not None and ended["state"] == "expired":
            return "PLAN_EXPIRED", ended, {"code": "STORE_REPORTED_EXPIRED", "plan_code": latest["plan_code"],
                                           "ended_at": latest["last_ended"], "store_reported": True}
        return "PLAN_EXPIRED", ended, {"code": "PERIOD_ENDED", "plan_code": latest["plan_code"],
                                       "ended_at": latest["last_ended"], "trial": latest["channel"] in FREE_CHANNELS}
    unverified = first("unverified")
    if unverified:
        return "PURCHASE_UNVERIFIED", unverified, {"code": "STORE_COULD_NOT_VERIFY"}
    if plan["plan_code"] == "none":
        return "NO_PURCHASE", None, {"code": "NO_PLAN"}
    if usable and any(f["usable"] for f in included):
        return USABLE, None, {"code": "USABLE"}
    return "ACTIVE_HARDWARE_BLOCKED", None, {"code": "MOUNT_NOT_SUPPORTED"}


def _feature_focus(feature: str, features: list[dict[str, Any]], status: str, reason: dict[str, Any]):
    row = next((f for f in features if f["feature"] == feature), None)
    if row is None:
        raise DomainError(422, "UNKNOWN_FEATURE", "unknown feature")
    if row["usable"]:
        return USABLE, {"code": "USABLE", "feature": feature}
    if row["blocked_by"] == ["hardware"]:
        return "ACTIVE_HARDWARE_BLOCKED", {"code": "MOUNT_NOT_SUPPORTED", "feature": feature}
    if status == "NO_PURCHASE":
        return status, reason | {"feature": feature}
    return "PLAN_EXCLUDES_FEATURE", {"code": "PLAN_EXCLUDES_FEATURE", "feature": feature}


HEADLINES = {
    "ACTIVE_FEATURE_USABLE": "Ready to use",
    "ACTIVE_HARDWARE_BLOCKED": "Your plan is active. The mount is the limit",
    "PLAN_EXCLUDES_FEATURE": "Not included in this camera's plan",
    "ASSIGNED_PENDING_CONFIRMATION": "Connected. Checking your camera",
    "VERIFIED_UNBOUND": "We found your plan",
    "DUPLICATE_PURCHASE": "This camera already has this plan",
    "ALREADY_ASSIGNED": "This plan is on another camera",
    "PURCHASE_PENDING": "Waiting for the store to confirm",
    "PROVIDER_UNKNOWN": "We're still confirming your purchase",
    "CONFLICTING_EVIDENCE": "We got two different answers",
    "REFUNDED_OR_REVOKED": "This plan was refunded",
    "PLAN_EXPIRED": "This plan has ended",
    "PLAN_SCHEDULED": "Your plan starts soon",
    "BILLING_GRACE": "Your payment needs attention",
    "BILLING_RETRY": "Your renewal payment didn't go through",
    "PLAN_PAUSED": "This plan is paused",
    "PURCHASE_UNVERIFIED": "We couldn't verify this purchase",
    "PURCHASE_NOT_FOUND": "We couldn't find this purchase on your account",
    "NO_PURCHASE": "No plan on this camera",
}


def _day(iso: str) -> str:
    """A date as a parent reads it: Oct 6, 2026 (the period's UTC date)."""
    d = datetime.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def _last_day(iso: str) -> str:
    """The last day with access before an end instant: an end at midnight UTC on
    Oct 22 means "through Oct 21" (adversarial review, 2026-10-11)."""
    return _day((datetime.fromisoformat(iso) - timedelta(microseconds=1)).isoformat())


def _plan_label(catalog_version: str, code: str) -> str:
    return entitlements.plan_label(catalog_version, code)


def _message(status: str, focus: Optional[dict[str, Any]], plan: dict[str, Any], device: Device,
             features: list[dict[str, Any]], catalog_version: str, reason: dict[str, Any]) -> str:
    camera = device.label
    plan_label = _plan_label(catalog_version, plan["plan_code"])
    bought = _plan_label(catalog_version, focus["plan_code"]) if focus else plan_label
    channel = purchase_evidence.CHANNEL_LABELS.get(focus["channel"], focus["channel"]) if focus else ""
    usable = [f["label"] for f in features if f["usable"]]
    if status == "ACTIVE_FEATURE_USABLE":
        return f"{plan_label} is active on {camera}. " + (f"Working here: {', '.join(usable)}." if usable else "")
    if status == "ACTIVE_HARDWARE_BLOCKED":
        return (f"{plan_label} is active on {camera}, but its features need the camera above the crib on a "
                f"Floor Stand or Wall Mount. It's on {entitlements.MOUNT_LABELS.get(device.mount, device.mount)}. "
                "A different plan wouldn't change this.")
    if status == "PLAN_EXCLUDES_FEATURE":
        return f"{camera} has {plan_label}, which doesn't include this feature."
    if status == "ASSIGNED_PENDING_CONFIRMATION":
        connected = _plan_label(catalog_version, reason.get("plan_code") or plan["plan_code"])
        return f"{connected} is connected to {camera}. We're checking that its features are ready."
    if status == "VERIFIED_UNBOUND" and focus is not None and focus["state"] == "grace":
        return (f"Your {bought} purchase ({channel}) isn't connected to a camera yet, and its renewal payment didn't "
                f"go through. The store keeps it active through {_last_day(focus['ends_at'].isoformat())} while it "
                f"retries. Connect it to {camera} and update the payment method "
                f"{PAYMENT_WHERE.get(focus['channel'], 'where you bought it')}. There's nothing more to pay.")
    if status == "VERIFIED_UNBOUND":
        return (f"Your {bought} purchase ({channel}) is confirmed but isn't connected to a camera yet. "
                f"It can go on one camera: connect it here if {camera} is the one. There's nothing more to pay.")
    if status == "DUPLICATE_PURCHASE":
        return (f"{camera} already has {plan_label} from another purchase, so connecting this one wouldn't add "
                "anything. Support can help with the second charge.")
    if status == "ALREADY_ASSIGNED":
        return ("This purchase is connected to another camera. We don't move plans between cameras "
                "automatically; support can look into moving it.")
    if status == "PURCHASE_PENDING":
        return (f"The store hasn't confirmed your {bought} purchase yet. There's nothing more to buy. "
                "This updates when it confirms.")
    if status == "PROVIDER_UNKNOWN":
        return ("We couldn't get a final answer from the payment provider yet. This purchase won't be "
                "submitted twice; we keep checking the provider's record.")
    if status == "CONFLICTING_EVIDENCE":
        return ("We received updates about this purchase that disagree. We won't guess; support can check "
                "the store's record.")
    if status == "REFUNDED_OR_REVOKED":
        return (f"A plan on {camera} was refunded, so its features are off. Your history is kept."
                + (f" {plan_label} is still active." if plan["plan_code"] != "none" else ""))
    if status == "PLAN_SCHEDULED":
        upcoming = _plan_label(catalog_version, reason.get("plan_code") or "none")
        return (f"{upcoming} on {camera} starts on {_day(reason['starts_at'])}. It's paid for and connected; "
                "there's nothing more to do." + (f" {plan_label} applies until then." if plan["plan_code"] != "none"
                                                 else ""))
    if status in ("BILLING_GRACE", "BILLING_RETRY", "PLAN_PAUSED", "PLAN_EXPIRED") and focus is not None:
        where = PAYMENT_WHERE.get(focus["channel"], "where you bought it")
        still = f" {plan_label} is still active." if plan["plan_code"] != "none" else ""
        bound_here = focus["binding"]["bound"]
        # The camera keeps an equal or higher plan from another purchase: nothing is off there.
        covered = (plan["plan_code"] != "none"
                   and _rank(catalog_version, plan["plan_code"]) >= _rank(catalog_version, focus["plan_code"]))
        kept = f"{camera} keeps {plan_label} from another purchase, so nothing is off there. "
        if status == "BILLING_GRACE":
            return (f"The renewal payment for {bought} ({channel}) didn't go through. {bought} stays on {camera} "
                    f"through {_last_day(reason['grace_ends_at'])} while the store retries. Update the payment "
                    f"method {where}; there's nothing more to buy.")
        if status == "BILLING_RETRY":
            head = f"The renewal payment for {bought} ({channel}) didn't go through, and the store is retrying it. "
            fix = f"Update the payment method {where}; there's nothing more to buy."
            if not bound_here:
                return head + fix + " When the store confirms, you can connect it to a camera."
            if covered:
                return head + kept + fix
            return (head + f"Its features are off on {camera} until then. " + fix
                    + f" When the store confirms, {bought} comes back on this camera by itself." + still)
        if status == "PLAN_PAUSED":
            if reason.get("resume_overdue"):
                when = (f"was set to resume on {_day(reason['resumes_at'])}, but the store hasn't confirmed that it "
                        "resumed. This updates when it does.")
            elif reason.get("resumes_at"):
                when = f"is paused in {channel} and resumes on {_day(reason['resumes_at'])}."
            else:
                when = f"is paused in {channel}."
            after = ("Then you can connect it to a camera; there's nothing more to buy." if not bound_here else
                     kept.strip() if covered else
                     "Its features are off until it resumes, then they come back by themselves; there's nothing "
                     "more to buy." + still)
            return f"{bought} on {camera} {when} {after}"
        if reason.get("store_reported"):
            store = STORE_NAME.get(focus["channel"], channel)
            return (f"{store} reports that {bought} on {camera} ended on {_day(reason['ended_at'])}. Its features "
                    "are off; your history is kept." + still)
    if status == "PLAN_EXPIRED" and reason.get("trial"):
        ended_plan = _plan_label(catalog_version, reason.get("plan_code") or "none")
        return (f"The {ended_plan} trial on {camera} ended on {_day(reason['ended_at'])}."
                + (f" {plan_label} is still active." if plan["plan_code"] != "none" else ""))
    if status == "PLAN_EXPIRED":
        ended_plan = _plan_label(catalog_version, reason.get("plan_code") or (focus or {}).get("plan_code") or "none")
        ended = f" on {_day(reason['ended_at'])}" if reason.get("ended_at") else ""
        return (f"{ended_plan} on {camera} ended{ended}, and the store hasn't reported a renewal. If you renewed, "
                "it may be on a different account; support can check the store's record. "
                + (f"{plan_label} is still active." if plan["plan_code"] != "none" else ""))
    if status == "PURCHASE_UNVERIFIED":
        return "The store couldn't verify this purchase. If you were charged, support can check your receipt."
    if status == "PURCHASE_NOT_FOUND":
        return "Check the code or the account you bought with. If you were charged, support can find it."
    return f"{camera} doesn't have a plan."


def _stages(status: str, *, focus: Optional[dict[str, Any]], plan: dict[str, Any], device: Device,
            features: list[dict[str, Any]], member_can_bind: bool, catalog_version: str,
            reason: dict[str, Any], checked: bool = True) -> list[dict[str, Any]]:
    pattern = STAGE_PATTERN[status]
    channel = purchase_evidence.CHANNEL_LABELS.get(focus["channel"], focus["channel"]) if focus else None
    bought = _plan_label(catalog_version, focus["plan_code"]) if focus else None
    has_plan = plan["plan_code"] != "none"
    granting = max((t for t in plan["transactions"] if t["state"] == "active"),
                   key=lambda t: _rank(catalog_version, t["plan_code"]), default=None)
    store_word = {"verified": "confirmed", "pending": "waiting for the store", "refunded": "refunded",
                  "unverified": "couldn't be verified", "conflicting": "conflicting updates",
                  "grace": "renewal payment didn't go through; grace period",
                  "billing_retry": "renewal payment didn't go through; the store is retrying",
                  "paused": "paused in the store", "expired": "expired, reported by the store"}
    waits = "Not checked yet: waits for the step before"
    if status == "PLAN_EXPIRED" and reason.get("trial"):
        bought = _plan_label(catalog_version, reason.get("plan_code") or "none")
        purchase_fact = f"{bought} trial: ended {_day(reason['ended_at'])}"
    elif status == "PLAN_EXPIRED":
        ended_plan = _plan_label(catalog_version, reason.get("plan_code") or (focus or {}).get("plan_code") or "none")
        bought = ended_plan
        purchase_fact = (f"{ended_plan}" + (f" via {channel}" if channel else "")
                         + (f": ended {_day(reason['ended_at'])}" if reason.get("ended_at") else ": ended")
                         + (", reported by the store" if reason.get("store_reported") else ", no renewal reported"))
    elif focus:
        purchase_fact = f"{bought} via {channel}: {store_word[focus['state']]} (provider version {focus['provider_version']})"
    elif status == "PROVIDER_UNKNOWN":
        purchase_fact = "An in-app purchase's outcome isn't known yet"
    elif status == "PURCHASE_PENDING":
        purchase_fact = "A purchase for this camera is being confirmed"
    elif status == "REFUNDED_OR_REVOKED":
        purchase_fact = "A plan on this camera was refunded"
    elif granting:
        purchase_fact = (f"{_plan_label(catalog_version, granting['plan_code'])} via "
                         f"{purchase_evidence.CHANNEL_LABELS.get(granting['channel'], granting['channel'])}")
    else:
        purchase_fact = "No purchase on record for this camera"
    lost_word = {"PLAN_EXPIRED": "ended", "REFUNDED_OR_REVOKED": "was refunded",
                 "BILLING_RETRY": "is off while the store retries the payment", "PLAN_PAUSED": "is paused"}.get(status)
    working = [f["label"] for f in features if f["usable"]]
    if status == "BILLING_GRACE":
        plan_fact = (f"{_plan_label(catalog_version, reason['plan_code'])} through "
                     f"{_last_day(reason['grace_ends_at'])} (grace period)")
    elif status == "PLAN_SCHEDULED":
        plan_fact = (f"{_plan_label(catalog_version, reason.get('plan_code') or 'none')} starts "
                     f"{_day(reason['starts_at'])}" + (f"; {plan['plan_label']} until then" if has_plan else ""))
    elif lost_word:
        plan_fact = f"{bought or 'A higher plan'} {lost_word}" + (f"; {plan['plan_label']} still active" if has_plan else "")
    elif status == "VERIFIED_UNBOUND" and focus:
        plan_fact = f"{bought} isn't on this camera yet" + (f"; it has {plan['plan_label']}" if has_plan else "")
    elif pattern[3] in ("unknown", "waiting"):
        plan_fact = waits
    else:
        plan_fact = f"{plan['plan_label']} ({plan['state']})" if has_plan else "No plan"
    facts = {
        "purchase": purchase_fact,
        "account": ("On this family's account" + ("" if member_can_bind else
                                                  ". Only the account owner or an admin can connect plans")),
        "camera": (f"Connected to {device.label}" if pattern[2] == "verified" else
                   "Not connected to a camera yet" if status == "VERIFIED_UNBOUND" else
                   "Connected to a different camera" if status == "ALREADY_ASSIGNED" else
                   f"{device.label} already has this plan" if status == "DUPLICATE_PURCHASE" else
                   "No plan to connect" if pattern[2] == "not_needed" else waits),
        "plan": plan_fact,
        "feature": (("Still working: " + ", ".join(working)) if working else "Nothing usable now") if lost_word else
                   (", ".join(working) or "Nothing usable yet")
                   if pattern[4] in ("verified", "blocked") else
                   "Waiting for the check" if pattern[4] == "waiting" else
                   "Nothing to check" if pattern[4] == "not_needed" else waits,
    }
    if pattern[1] == "verified" and not member_can_bind and status == "VERIFIED_UNBOUND":
        pattern = pattern[:1] + ("action",) + pattern[2:]
    # "Verified" only where a record or a check backs it (cold review,
    # 2026-10-11: five "Verified" stages with no check on record). The feature
    # stage needs a check on this camera that matches its current facts; a
    # trial or a seeded plan is not a purchase.
    if pattern[4] == "verified" and not checked:
        pattern = pattern[:4] + ("unchecked",)
        facts["feature"] = f"{facts['feature']}, by the plan's rules. No check has run on this camera yet"
    if pattern[0] == "verified" and focus is None and granting is not None and granting["channel"] in FREE_CHANNELS:
        pattern = ("not_needed",) + pattern[1:]
        facts["purchase"] = (f"{_plan_label(catalog_version, granting['plan_code'])} "
                             f"{'trial' if granting['channel'] == 'trial' else 'from the demo setup'}: nothing was bought")
    return [{"key": key, "label": STAGE_LABELS[key], "state": state, "state_label": STATE_LABELS[state],
             "fact": facts[key]} for key, state in zip(STAGES, pattern)]


def _public(state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return purchase_evidence._public(state)


def overview(db: Session, principal: Principal, household: Household) -> dict[str, Any]:
    """Family home: each camera's status, and (for members who manage plans)
    every purchase with where it is connected."""
    devices = list(db.execute(select(Device).where(Device.household_id == household.id)
                              .order_by(Device.is_primary.desc(), Device.id)).scalars())
    bind_ok = can_bind(principal)
    cameras = [assess(db, household, d, member_can_bind=bind_ok) for d in devices]
    commerce = principal.role in POLICY["commercial.read"]
    return {
        "state_revision": household.state_revision,
        "cameras": [{k: c[k] for k in ("device", "status", "headline", "message", "stages", "allowed_actions",
                                       "plan", "usable_features", "hardware_blocked_features", "focus_purchase",
                                       "verification")} for c in cameras],
        # The same time-aware store state the cameras use, so home and camera agree.
        "purchases": [_public(purchase_evidence.as_of(p, utcnow()))
                      for p in purchase_evidence.household_purchases(db, household.id)] if commerce else [],
        "purchases_visible": commerce,
        "member_can_connect": bind_ok,
        "simulated": True,
    }


# ---------------------------------------------------------------- commands

def bind(db: Session, principal: Principal, household: Household, device: Device, *, channel: str,
         external_ref: str, expected_state_revision: int, idempotency_key: str) -> tuple[dict[str, Any], bool]:
    """Connect a verified purchase to a camera. Caller holds the household lock
    and has authorized access.bind. Returns (outcome, replayed)."""
    request_hash = _fingerprint(device_id=device.id, channel=channel, external_ref=external_ref,
                                expected_state_revision=expected_state_revision)
    prior = db.execute(select(AccessAction).where(AccessAction.household_id == household.id,
                                                  AccessAction.idempotency_key == idempotency_key)).scalar_one_or_none()
    if prior is not None:
        if prior.request_hash != request_hash:
            raise DomainError(409, "IDEMPOTENCY_KEY_REUSED", "this key was used for a different request")
        return prior.outcome, True
    growth.record_detached("activation_action_submitted", household.id,
                           {"device_id": device.id, "channel": channel})

    if household.state_revision != expected_state_revision:
        raise DomainError(409, "STALE_ACCESS_VIEW", "your view of this family's plans is out of date",
                          current_state_revision=household.state_revision)
    purchases = [purchase_evidence.as_of(p, utcnow()) for p in purchase_evidence.household_purchases(db, household.id)]
    found = next((p for p in purchases if (p["channel"], p["external_ref"]) == (channel, external_ref)), None)
    if found is None:
        raise DomainError(404, "PURCHASE_NOT_FOUND", "we couldn't find this purchase on your account")
    refusals = {"pending": ("PURCHASE_NOT_CONFIRMED", "the store hasn't confirmed this purchase yet"),
                "refunded": ("PURCHASE_REFUNDED", "this purchase was refunded"),
                "unverified": ("PURCHASE_UNVERIFIED", "the store couldn't verify this purchase"),
                "conflicting": ("PURCHASE_EVIDENCE_CONFLICT", "the store sent updates about this purchase that disagree"),
                "billing_retry": ("PURCHASE_PAYMENT_RETRYING", "the store is retrying this purchase's renewal payment; "
                                                               "try again when the store confirms"),
                "paused": ("PURCHASE_PAUSED", "this purchase is paused in the store"),
                "expired": ("PURCHASE_EXPIRED", "the store reports that this purchase expired")}
    if found["state"] in refusals:
        code, message = refusals[found["state"]]
        raise DomainError(409, code, message)
    if _ended(found, utcnow()):
        raise DomainError(409, "PURCHASE_EXPIRED", "this purchase's period has ended and no renewal was reported")
    binding = found["binding"]
    if binding["bound"] and (binding["elsewhere"] or binding["device_id"] != device.id):
        code = "CODE_ALREADY_REDEEMED" if channel == "activation_code" else "ALREADY_ASSIGNED"
        raise DomainError(409, code, "this purchase is connected to another camera; it is not moved automatically")
    if binding["bound"] and "revoked" in binding["claim_statuses"]:
        raise DomainError(409, "PURCHASE_EVIDENCE_CONFLICT", "this camera's record says the purchase was revoked")

    catalog_version, _ = decisions.active_versions(db)
    if not binding["bound"]:
        plan = claims.camera_plan(db, household.id, device.id, catalog_version)
        paid_rank = max((_rank(catalog_version, t["plan_code"]) for t in plan["transactions"]
                         if t["state"] == "active" and t["channel"] not in FREE_CHANNELS), default=0)
        if paid_rank >= _rank(catalog_version, found["plan_code"]):
            raise DomainError(409, "CAMERA_ALREADY_HAS_PLAN",
                              "this camera already has this plan or a higher one from another purchase",
                              camera_plan=plan["plan_code"])
    before = claims.camera_plan(db, household.id, device.id, catalog_version)
    written = purchase_evidence.apply_periods(db, household, found, catalog_version, device=device)
    after = claims.camera_plan(db, household.id, device.id, catalog_version)
    outcome = {"operation": "completed", "already_connected": written is None,
               "device_id": device.id, "channel": channel, "plan_code": found["plan_code"],
               "claim_id": written, "camera_plan_before": before["plan_code"],
               "camera_plan_after": after["plan_code"], "state_revision": household.state_revision,
               "capability": "not_yet_verified"}
    # clock_timestamp, not the transaction start: a check that waited for the
    # household lock must still sort after the connect it saw.
    action = AccessAction(id=new_id("acc"), created_at=func.clock_timestamp(), household_id=household.id,
                          member_id=principal.member_id, kind="bind",
                          channel=channel, external_ref=external_ref, device_id=device.id,
                          idempotency_key=idempotency_key, request_hash=request_hash, outcome=outcome,
                          trace_id=current_trace_id())
    db.add(action)
    db.flush()
    growth.record_server(db, "camera_entitlement_resolved", household.id,
                         {"device_id": device.id, "plan_code": found["plan_code"]})
    return outcome | {"action_id": action.id}, False


def last_connect(db: Session, household_id: str, device_id: str) -> Optional[AccessAction]:
    """The latest connect that changed this camera. Re-connecting a purchase
    that was already there is recorded but is not a new activation."""
    return db.execute(select(AccessAction).where(
        AccessAction.household_id == household_id, AccessAction.device_id == device_id,
        AccessAction.outcome["already_connected"].astext == "false")
        .order_by(AccessAction.created_at.desc(), AccessAction.id.desc()).limit(1)).scalar_one_or_none()


def latest_check(db: Session, household_id: str, device_id: str) -> Optional[AccessCheck]:
    return db.execute(select(AccessCheck).where(AccessCheck.household_id == household_id,
                                                AccessCheck.device_id == device_id)
                      .order_by(AccessCheck.created_at.desc(), AccessCheck.id.desc()).limit(1)).scalar_one_or_none()


def check_view(c: AccessCheck) -> dict[str, Any]:
    return {"check_id": c.id, "kind": c.kind, "status": c.status, "plan_code": c.plan_code,
            "usable_features": c.usable_features, "fingerprint": c.fingerprint,
            "state_revision": c.state_revision, "trace_id": c.trace_id, "at": c.created_at.isoformat()}


def verify(db: Session, principal: Principal, household: Household, device: Device) -> dict[str, Any]:
    """Re-check access from committed state and record what was verified.
    Caller holds the household lock. The kind is the server's call: the first
    check after a connect is the activation check."""
    # What the camera can do now, without the "connected, not yet verified" overlay.
    current = assess(db, household, device, member_can_bind=can_bind(principal), pending_verification_overlay=False)
    kind = "activation" if current["verification"]["awaiting"] else "recheck"
    capability = current["status"]
    check = AccessCheck(id=new_id("chk"), created_at=func.clock_timestamp(), household_id=household.id,
                        device_id=device.id, kind=kind,
                        status=capability, plan_code=current["plan"]["plan_code"],
                        usable_features=current["usable_features"], fingerprint=current["fingerprint"],
                        state_revision=household.state_revision, trace_id=current_trace_id())
    db.add(check)
    db.flush()
    name = "feature_access_verified" if capability == USABLE else "access_resolution_unconfirmed"
    growth.record_server(db, name, household.id, {"device_id": device.id, "status": capability.lower()})
    return {"check": check_view(check), "access": assess(db, household, device, member_can_bind=can_bind(principal))}


# ---------------------------------------------------------------- verification window and outcome review

def window(db: Session, principal: Principal, household: Household, device: Device) -> dict[str, Any]:
    """Whether access verified at activation is still what the camera has.
    Each later check is compared with the activation check; the live state is
    compared with the latest check."""
    checks = list(db.execute(select(AccessCheck).where(AccessCheck.household_id == household.id,
                                                       AccessCheck.device_id == device.id)
                             .order_by(AccessCheck.created_at, AccessCheck.id)).scalars())
    activation = next((c for c in reversed(checks) if c.kind == "activation"), None)
    base = activation or (checks[0] if checks else None)
    live = assess(db, household, device, member_can_bind=can_bind(principal))
    observations = []
    for c in checks:
        if base is None or c.created_at < base.created_at:
            continue
        observations.append(check_view(c) | {"verdict": _verdict(base, c.status, c.plan_code, c.usable_features,
                                                                  c.fingerprint)})
    latest = checks[-1] if checks else None
    now_verdict = None if base is None else _verdict(base, live["status"], live["plan"]["plan_code"],
                                                     live["usable_features"], live["fingerprint"])
    return {
        "device_id": device.id,
        "activation": check_view(activation) if activation else None,
        "observations": observations,
        "live": {"status": live["status"], "plan_code": live["plan"]["plan_code"],
                 "usable_features": live["usable_features"], "fingerprint": live["fingerprint"],
                 "verdict": now_verdict,
                 "changed_since_last_check": latest is not None and latest.fingerprint != live["fingerprint"]},
        "decision": _window_decision(base, observations, now_verdict, _feature_labels(db)),
    }


def _verdict(base: AccessCheck, status: str, plan_code: str, usable: list[str], fingerprint: str) -> dict[str, Any]:
    lost = sorted(set(base.usable_features) - set(usable))
    gained = sorted(set(usable) - set(base.usable_features))
    state = "still_verified" if fingerprint == base.fingerprint else "access_lost" if lost else "changed"
    return {"state": state, "lost": lost, "gained": gained, "plan_before": base.plan_code, "plan_after": plan_code,
            "status": status}


def _feature_labels(db: Session) -> dict[str, str]:
    _, policy_version = decisions.active_versions(db)
    return {k: v["label"] for k, v in entitlements.POLICIES[policy_version]["features"].items()}


def _window_decision(base: Optional[AccessCheck], observations: list[dict[str, Any]],
                     now_verdict: Optional[dict[str, Any]], labels: dict[str, str]) -> dict[str, Any]:
    if base is None:
        return {"state": "NOT_STARTED", "reason": "access has not been verified on this camera yet"}
    if now_verdict and now_verdict["state"] == "access_lost":
        return {"state": "ACCESS_LOST", "reason": "features verified at activation are no longer usable: "
                                                  + ", ".join(labels.get(f, f) for f in now_verdict["lost"])}
    if now_verdict and now_verdict["state"] != "still_verified":
        return {"state": "CHANGED", "reason": "the camera's plan or features changed since it was verified"}
    return {"state": "HOLDING", "reason": f"{len(observations)} check(s); what was verified still holds"}


def outcome_review(db: Session, household: Household, device: Device) -> dict[str, Any]:
    """Three outcomes, each from its own evidence, never inferred from another."""
    connect = last_connect(db, household.id, device.id)
    since = connect.created_at if connect else None
    verified = None
    if connect is not None:
        verified = db.execute(select(AccessCheck).where(
            AccessCheck.household_id == household.id, AccessCheck.device_id == device.id,
            AccessCheck.created_at > since, AccessCheck.status == USABLE)
            .order_by(AccessCheck.created_at, AccessCheck.id).limit(1)).scalar_one_or_none()
    opened = None
    if verified is not None:
        opened = db.execute(select(AnalyticsEvent).where(
            AnalyticsEvent.household_id == household.id, AnalyticsEvent.name == "access_feature_opened",
            AnalyticsEvent.properties["device_id"].astext == device.id,
            AnalyticsEvent.received_at > verified.created_at)
            .order_by(AnalyticsEvent.received_at).limit(1)).scalar_one_or_none()

    def tier(key: str, label: str, row: Any, at: Optional[datetime], evidence: Optional[dict[str, Any]],
             missing: str) -> dict[str, Any]:
        return {"key": key, "label": label, "state": "observed" if row is not None else "not_observed",
                "at": at.isoformat() if at else None, "evidence": evidence, "missing": None if row else missing}

    return {
        "device_id": device.id,
        "tiers": [
            tier("operation_completed", "Operation completed", connect, since,
                 connect.outcome if connect else None, "no connect command on record for this camera"),
            tier("capability_verified", "Capability verified", verified, verified.created_at if verified else None,
                 check_view(verified) if verified else None,
                 "no check after the connect found a usable feature"),
            tier("customer_outcome_observed", "Customer outcome observed", opened,
                 opened.received_at if opened else None,
                 {"event": opened.name, "properties": opened.properties} if opened else None,
                 "no access_feature_opened event after verification"),
        ],
        "caveat": "Each outcome needs its own evidence. In this prototype the parent's click is synthetic; "
                  "it validates instrumentation, not a real outcome.",
    }
