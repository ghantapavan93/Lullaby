"""Access funnel metrics, computed from the records the application already keeps.

Every number is derived at read time from append-only rows: purchase evidence,
connect commands, access checks and server analytics. Nothing is estimated,
sampled or stored a second time. Durations use the database's record times
(when each row was written), never the application clock.

In this prototype every row is synthetic (Scenario Studio, tests, demo clicks),
so these numbers show that the instrumentation can answer the question. They
are not evidence of any customer outcome, rate or trend.

Scope: the caller's household. Purchases are numbered, never named.

- time to usable access: store confirmation -> connect -> the first check that
  found a feature usable on that camera;
- unresolved: confirmed purchases with no usable check yet, and how long;
- refused before payment: purchases the server refused before any (simulated)
  money moved, by reason;
- billing recovery: the latest failed renewal -> the store's recovery (by the
  store's version order) -> the first usable check afterwards;
- verification outcomes: every access check, by the status it recorded.
"""
from collections import Counter
from datetime import datetime
from statistics import median
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import clock, purchase_evidence
from .models import (AccessAction, AccessCheck, AnalyticsEvent, EntitlementClaim, PurchaseEvidence,
                     VideoAnalysisRun, VideoAnnotation, VideoObservation)

USABLE = "ACTIVE_FEATURE_USABLE"
FAILED_RENEWAL = ("grace", "billing_retry")
CAVEAT = ("Computed from synthetic records made by scenarios, tests and demo clicks. It shows the "
          "instrumentation works; it is not a customer rate.")


def _seconds(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    return None if start is None or end is None else round((end - start).total_seconds(), 3)


def _first(times: list[datetime], after: Optional[datetime] = None) -> Optional[datetime]:
    later = [t for t in times if after is None or t >= after]
    return min(later) if later else None


def _summary(values: list[Optional[float]]) -> dict[str, Any]:
    done = [v for v in values if v is not None]
    return {"count": len(done), "median_s": round(median(done), 3) if done else None,
            "max_s": max(done) if done else None}


def _recovery(events: list[Any], usable: list[datetime]) -> Optional[dict[str, Any]]:
    """The latest failed-renewal episode of one purchase, ordered by provider
    version (the store's order), timed by record times. An episode starts at
    the first failure after the last confirmed period before it; it ends at
    the first confirmed period at a higher version than its last failure."""
    failed = [e for e in events if e.status in FAILED_RENEWAL]
    if not failed:
        return None
    last_failure = max(e.provider_version for e in failed)
    confirmed_before = max((e.provider_version for e in events if e.status == "verified"
                            and e.provider_version < last_failure), default=0)
    episode = [e for e in failed if e.provider_version > confirmed_before]
    started = min(e.received_at for e in episode)
    recovered = _first([e.received_at for e in events if e.status == "verified"
                        and e.provider_version > last_failure])
    took = _seconds(started, recovered)
    # A recovery recorded before the failure it answers was delivered late: the
    # store's order is known, the elapsed time isn't (adversarial review, 2026-10-11).
    out_of_order = took is not None and took < 0
    return {"grace": any(e.status == "grace" for e in episode),
            "failure_to_recovery_s": None if out_of_order else took,
            "recovery_to_usable_check_s": _seconds(recovered, _first(usable, after=recovered)) if recovered else None,
            "still_failing": recovered is None, "out_of_order": out_of_order}


def funnel(*, evidence: list[Any], binds: list[Any], claims: list[Any], checks: list[Any],
           purchases: list[dict[str, Any]], rejections: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """The metrics, from rows already loaded. Pure, so it is tested without a database.

    A purchase reaches its camera through a connect command or, for a renewal
    or a purchase connected before this feature existed, through its first
    active claim. Only purchases the store still says grant access (verified,
    grace) can be "unresolved": a refund or a failed payment isn't waiting on us."""
    by_ref: dict[tuple[str, str], list[Any]] = {}
    for e in evidence:
        by_ref.setdefault((e.channel, e.external_ref), []).append(e)
    state_of = {(p["channel"], p["external_ref"]): p["state"] for p in purchases}
    device_of = {(p["channel"], p["external_ref"]): p["binding"]["device_id"] for p in purchases
                 if p["binding"]["bound"] and not p["binding"]["elsewhere"]}
    usable_at: dict[str, list[datetime]] = {}
    for c in checks:
        if c.status == USABLE:
            usable_at.setdefault(c.device_id, []).append(c.created_at)

    journeys, recoveries = [], []
    for n, (ref, events) in enumerate(sorted(by_ref.items()), 1):
        confirmed = _first([e.received_at for e in events if e.status == "verified"])
        device = device_of.get(ref)
        by_connect = _first([b.created_at for b in binds if (b.channel, b.external_ref) == ref])
        by_claim = _first([c.received_at for c in claims if (c.channel, c.external_ref) == ref
                           and c.event_status == "active"]) if device else None
        connected = by_connect or by_claim
        usable = _first(usable_at.get(device, []), after=connected) if device and connected else None
        waiting = usable is None and state_of.get(ref) in ("verified", "grace")
        if confirmed is not None:
            journeys.append({"purchase": n, "channel": ref[0], "connected": connected is not None,
                             "connected_via": "connect" if by_connect else "claim" if by_claim else None,
                             "confirmed_to_connected_s": _seconds(confirmed, connected),
                             "connected_to_usable_s": _seconds(connected, usable),
                             "confirmed_to_usable_s": _seconds(confirmed, usable),
                             "unresolved_for_s": _seconds(confirmed, now) if waiting else None})
        recovery = _recovery(events, usable_at.get(device, []) if device else [])
        if recovery:
            recoveries.append({"purchase": n, "channel": ref[0]} | recovery)

    unresolved = [j for j in journeys if j["unresolved_for_s"] is not None]
    outcomes = Counter(c.status for c in checks)
    return {
        "synthetic": True,
        "caveat": CAVEAT,
        "time_to_usable": {"journeys": journeys,
                           "confirmed_to_usable": _summary([j["confirmed_to_usable_s"] for j in journeys]),
                           "connected_to_usable": _summary([j["connected_to_usable_s"] for j in journeys])},
        "unresolved": {"count": len(unresolved), "not_connected": sum(1 for j in unresolved if not j["connected"]),
                       "oldest_s": max((j["unresolved_for_s"] for j in unresolved), default=None)},
        "refused_before_payment": dict(sorted(Counter(r.get("code", "UNKNOWN") for r in rejections).items())),
        "billing_recovery": {"purchases": recoveries,
                             "recovered": sum(1 for r in recoveries if not r["still_failing"]),
                             "still_failing": sum(1 for r in recoveries if r["still_failing"]),
                             "failure_to_recovery": _summary([r["failure_to_recovery_s"] for r in recoveries])},
        "verification_outcomes": {"checks": sum(outcomes.values()), "usable": outcomes.get(USABLE, 0),
                                  "by_status": dict(sorted(outcomes.items()))},
    }


LADDER = (
    ("confirmed", "Purchase confirmed", "purchase_evidence: the store's verified record"),
    ("effective", "Entitlement effective on a camera", "entitlement_claims: active, and its period has started"),
    ("eligible", "Feature eligible", "access_checks: the first check that found a feature usable on that camera"),
    ("shown", "Shown to the parent", "analytics_events: access_check_rendered with a usable status"),
    ("opened", "Opened while it worked", "analytics_events: access_feature_opened, stamped usable by the server"),
)


def ladder(*, evidence: list[Any], claims: list[Any], checks: list[Any], events: list[Any],
           now: datetime) -> dict[str, Any]:
    """Five steps from purchase to use, each counted from its own rows and never
    inferred from another. A purchase reaches a step only after the step before
    it, and every step has the same denominator: confirmed purchases. Pure."""
    confirmed: dict[tuple[str, str], datetime] = {}
    for e in evidence:
        if e.status == "verified":
            key = (e.channel, e.external_ref)
            confirmed[key] = min(confirmed.get(key, e.received_at), e.received_at)
    usable_at: dict[str, list[datetime]] = {}
    for c in checks:
        if c.status == USABLE:
            usable_at.setdefault(c.device_id, []).append(c.created_at)
    shown_at: dict[str, list[datetime]] = {}
    opened_at: dict[str, list[datetime]] = {}
    for e in events:
        device = (e.properties or {}).get("device_id")
        if e.name == "access_check_rendered" and str((e.properties or {}).get("status", "")).upper() == USABLE:   # the client sends it lowercased
            shown_at.setdefault(device, []).append(e.received_at)
        elif e.name == "access_feature_opened" and (e.properties or {}).get("usable_when_opened") is True:
            opened_at.setdefault(device, []).append(e.received_at)

    times: dict[str, list[tuple[datetime, datetime]]] = {key: [] for key, _, _ in LADDER}
    for ref, at_confirmed in confirmed.items():
        times["confirmed"].append((at_confirmed, at_confirmed))
        effective = [(max(c.received_at, c.starts_at), c.device_id) for c in claims
                     if (c.channel, c.external_ref) == ref and c.event_status == "active"
                     and max(c.received_at, c.starts_at) <= now]
        if not effective:
            continue
        at_effective, device = min(effective)
        times["effective"].append((at_confirmed, at_effective))
        previous = at_effective
        for key, source in (("eligible", usable_at), ("shown", shown_at), ("opened", opened_at)):
            reached = _first(source.get(device, []), after=previous)
            if reached is None:
                break
            times[key].append((previous, reached))
            previous = reached

    total = len(confirmed)
    return {
        "synthetic": True,
        "denominator": "confirmed purchases",
        "steps": [{"key": key, "label": label, "source": source, "reached": len(times[key]), "of": total,
                   "median_s_from_previous": (round(median([(b - a).total_seconds() for a, b in times[key]]), 3)
                                              if times[key] and key != "confirmed" else None)}
                  for key, label, source in LADDER],
    }


def video_funnel(runs: list[Any], observations: list[Any], annotations: list[Any],
                 events: list[Any]) -> dict[str, Any]:
    """Recorded-evidence facts, each from its own rows. Access verified, the
    evidence opened, and the analysis outcome are different events, never
    inferred from one another. Pure."""
    done = [r for r in runs if r.state == "COMPLETED"]
    by_name = Counter(e.name for e in events)
    checks = [e for e in events if e.name == "evidence_access_checked"]
    return {
        "runs": {"completed": len(done), "failed": sum(1 for r in runs if r.state == "FAILED"),
                 "in_progress": sum(1 for r in runs if r.state not in ("COMPLETED", "FAILED")),
                 "failure_codes": dict(sorted(Counter(r.error_code for r in runs if r.error_code).items())),
                 "analysis_s": _summary([r.elapsed_ms / 1000 for r in done if r.elapsed_ms is not None])},
        "intervals": {"measured": len(observations),
                      "without_enough_evidence": sum(1 for o in observations
                                                     if o.kind in ("insufficient_evidence", "low_quality"))},
        "reports": {"added": len(annotations), "corrections": sum(1 for a in annotations if a.supersedes_annotation_id)},
        "access": {"checked_allowed": sum(1 for e in checks if e.properties.get("allowed")),
                   "checked_refused": sum(1 for e in checks if not e.properties.get("allowed")),
                   "evidence_opened": by_name.get("evidence_review_opened", 0)},
        "source": "sample or staged clip processed in this prototype; not customer usage",
    }


def household_funnel(db: Session, household_id: str) -> dict[str, Any]:
    """The funnel for one household, read in one transaction."""
    evidence = list(db.execute(select(PurchaseEvidence).where(PurchaseEvidence.household_id == household_id)).scalars())
    binds = list(db.execute(select(AccessAction).where(AccessAction.household_id == household_id,
                                                       AccessAction.kind == "bind")).scalars())
    claims = list(db.execute(select(EntitlementClaim).where(EntitlementClaim.household_id == household_id)).scalars())
    checks = list(db.execute(select(AccessCheck).where(AccessCheck.household_id == household_id)).scalars())
    rejections = [e.properties for e in db.execute(select(AnalyticsEvent).where(
        AnalyticsEvent.household_id == household_id, AnalyticsEvent.name == "purchase_rejected")).scalars()]
    now = db.execute(select(func.clock_timestamp())).scalar_one()
    # Store states are judged on the application clock, like every access status;
    # durations stay on the database's record times.
    purchases = [purchase_evidence.as_of(p, clock.now()) for p in purchase_evidence.household_purchases(db, household_id)]
    result = funnel(evidence=evidence, binds=binds, claims=claims, checks=checks, rejections=rejections, now=now,
                    purchases=purchases)
    shown_or_opened = list(db.execute(select(AnalyticsEvent).where(
        AnalyticsEvent.household_id == household_id,
        AnalyticsEvent.name.in_(("access_check_rendered", "access_feature_opened")))).scalars())
    result["ladder"] = ladder(evidence=evidence, claims=claims, checks=checks, events=shown_or_opened, now=now)
    runs = list(db.execute(select(VideoAnalysisRun).where(VideoAnalysisRun.household_id == household_id)).scalars())
    observations = list(db.execute(select(VideoObservation).where(VideoObservation.household_id == household_id)).scalars())
    annotations = list(db.execute(select(VideoAnnotation).where(VideoAnnotation.household_id == household_id)).scalars())
    events = list(db.execute(select(AnalyticsEvent).where(
        AnalyticsEvent.household_id == household_id,
        AnalyticsEvent.name.in_(("evidence_access_checked", "evidence_review_opened")))).scalars())
    return result | {"video": video_funnel(runs, observations, annotations, events)}
