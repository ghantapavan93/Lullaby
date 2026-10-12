"""Experiment assignment, exposure receipts and product analytics.

- Assignment is per household, persisted on first use and never recomputed:
  the stored row wins over any later change to the hashing or the split.
  Changing an experiment means a new ``config_version``.
- Exposure is a *render receipt* sent by the client after the surface was in
  the viewport. Fetching data never creates exposure. A retried receipt with
  the same ``render_id`` is a no-op (unique per household).
- Analytics ingestion is idempotent on ``(household_id, event_id)`` and accepts
  only typed, allow-listed properties per event name: counts, flags, enums and
  identifiers. Free text, names and care notes are rejected at the door, not
  filtered later. Server events (purchases) cannot be posted by clients.
- Synthetic events produced by the demo validate instrumentation. They are
  not evidence of any business outcome.
"""
import hashlib
import math
import re
import uuid
from typing import Any, Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .db import keep
from .engine import DomainError
from .models import (AnalyticsEvent, BriefingRevision, CapabilityDecision, ExperimentAssignment,
                     ExperimentControl, RenderReceipt)
from .security import Principal, current_trace_id, new_id

EXPERIMENTS = {
    # Hypothesis B (eligibility transparency): does a requirements checklist
    # lead to fewer hardware-blocked purchase attempts than a reason sentence?
    "availability_explanation": {
        "config_version": "v1",
        "variants": ("reason_sentence", "requirements_checklist"),
        "control": "reason_sentence",          # what everyone sees while the experiment is killed
        "surface": "feature_availability",
        "eligible": "households whose owner/admin opened a capability decision",
        # Exposure is recorded on the explanation card, where the variant renders, for
        # every decision (Growth review, 2026-10-12: it was on the offer card, which a
        # hardware-blocked household never sees). A hardware-blocked decision carries no
        # offer, so "purchase attempts" would be zero by construction in both arms.
        "exposure": "the explanation card of a capability decision, at least half visible",
        "primary_metric": "share of hardware-blocked decisions followed by the mount guide or a later "
                          "check that finds the feature available (defined; not yet computed)",
        "guardrails": ["purchase_rejected rate", "support contact rate (not modeled)"],
    },
}

# Every property has a type. Strings are enums or identifiers, never prose, so
# a care note cannot ride in on any field, nested or not.
Validator = Callable[[Any], bool]
_IDENT = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


def _count(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1_000_000


def _flag(v: Any) -> bool:
    return isinstance(v, bool)


def _ident(v: Any) -> bool:
    return isinstance(v, str) and bool(_IDENT.match(v))


def _one_of(*allowed: str) -> Validator:
    return lambda v: isinstance(v, str) and v in allowed


def _codes(v: Any) -> bool:
    return isinstance(v, list) and len(v) <= 8 and all(isinstance(x, str) and re.match(r"^[A-Z_]{1,40}$", x) for x in v)


BRIEFING_STATUS = _one_of("complete", "incomplete_observation", "unknown_observation", "no_observation",
                          "conflicting_evidence")
DECISION_STATUS = _one_of("AVAILABLE", "BLOCKED", "UNKNOWN")
VARIANT = _one_of(*{v for e in EXPERIMENTS.values() for v in e["variants"]})

CLIENT_EVENTS: dict[str, dict[str, Validator]] = {
    "briefing_viewed": {"revision": _count, "status": BRIEFING_STATUS},
    "change_panel_opened": {"revision": _count, "changed_count": _count},
    "correction_started": {"revision": _count},
    "acknowledge_clicked": {"revision": _count, "stale": _flag},
    "availability_viewed": {"feature": _ident, "status": DECISION_STATUS, "variant": VARIANT},
    "offer_cta_clicked": {"feature": _ident, "variant": VARIANT},
    # Purchase to access. The open event is the only evidence of a customer outcome.
    "access_check_rendered": {"device_id": _ident, "status": _ident},
    "access_feature_opened": {"device_id": _ident, "feature": _ident},
    # Video evidence: the parent opened the evidence view (sent after it rendered).
    "evidence_review_opened": {"device_id": _ident, "asset_id": _ident},
}
# Emitted only by the server (record_detached); clients cannot post them.
SERVER_EVENTS: dict[str, dict[str, Validator]] = {
    "purchase_rejected": {"code": lambda v: isinstance(v, str) and bool(re.match(r"^[A-Z_]{1,40}$", v)),
                          "reasons": _codes},
    "purchase_confirmed": {"intent_id": _ident, "plan_code": _ident},
    "purchase_cancelled_before_submission": {"intent_id": _ident},
    "activation_action_submitted": {"device_id": _ident, "channel": _ident},
    "camera_entitlement_resolved": {"device_id": _ident, "plan_code": _ident},
    "feature_access_verified": {"device_id": _ident, "status": _ident},
    "access_resolution_unconfirmed": {"device_id": _ident, "status": _ident},
    "evidence_access_checked": {"device_id": _ident, "allowed": _flag},
    "video_analysis_completed": {"run_id": _ident, "elapsed_ms": _count, "intervals": _count, "insufficient": _count},
    "video_analysis_failed": {"run_id": _ident, "code": lambda v: isinstance(v, str) and bool(re.match(r"^[A-Z_]{1,40}$", v))},
    "video_annotation_added": {"asset_id": _ident, "label": _one_of("caregiver_present", "infant_moving", "infant_still",
                                                                   "camera_moved", "not_sure"),
                               "correction": _flag},
}
SURFACES = {"night_briefing": "revision", "what_changed": "revision",
            "feature_availability": "decision", "engineering_xray": "revision"}


def assignment(db: Session, household_id: str, experiment_key: str) -> ExperimentAssignment:
    spec = EXPERIMENTS[experiment_key]
    bucket = int(hashlib.sha256(f"{household_id}:{experiment_key}:{spec['config_version']}".encode())
                 .hexdigest(), 16) % len(spec["variants"])
    db.execute(pg_insert(ExperimentAssignment).values(
        household_id=household_id, experiment_key=experiment_key, config_version=spec["config_version"],
        variant=spec["variants"][bucket]).on_conflict_do_nothing())
    return db.execute(select(ExperimentAssignment).where(
        ExperimentAssignment.household_id == household_id,
        ExperimentAssignment.experiment_key == experiment_key,
        ExperimentAssignment.config_version == spec["config_version"])).scalar_one()


def is_killed(db: Session, experiment_key: str) -> bool:
    control = db.get(ExperimentControl, experiment_key)
    return bool(control and control.killed)


def serve(db: Session, household_id: str, experiment_key: str) -> dict[str, Any]:
    """The variant to render. While killed: the control variant for everyone,
    no assignment written, and the client is told not to report exposure."""
    spec = EXPERIMENTS[experiment_key]
    if is_killed(db, experiment_key):
        return {"key": experiment_key, "variant": spec["control"], "config_version": spec["config_version"],
                "killed": True}
    assigned = assignment(db, household_id, experiment_key)
    return {"key": experiment_key, "variant": assigned.variant, "config_version": assigned.config_version,
            "killed": False}


def set_killed(db: Session, experiment_key: str, killed: bool, reason: str, actor: str) -> dict[str, Any]:
    if experiment_key not in EXPERIMENTS:
        raise DomainError(404, "NOT_FOUND", "no such experiment")
    db.execute(pg_insert(ExperimentControl).values(
        experiment_key=experiment_key, killed=killed, reason=reason, changed_by=actor)
        .on_conflict_do_update(index_elements=[ExperimentControl.experiment_key],
                               set_={"killed": killed, "reason": reason, "changed_by": actor,
                                     "updated_at": func.now()}))
    return {"experiment": experiment_key, "killed": killed, "reason": reason}


def record_render(db: Session, principal: Principal, *, render_id: str, surface: str, subject_id: str,
                  experiment_key: Optional[str], variant: Optional[str]) -> tuple[RenderReceipt, bool]:
    if surface not in SURFACES:
        raise DomainError(422, "UNKNOWN_SURFACE", "unknown surface", allowed=sorted(SURFACES))
    model = BriefingRevision if SURFACES[surface] == "revision" else CapabilityDecision
    owned = db.execute(select(model.id).where(model.id == subject_id,
                                              model.household_id == principal.household_id)).scalar_one_or_none()
    if owned is None:
        raise DomainError(404, "NOT_FOUND", "render subject not found")
    config_version = None
    if variant is not None and experiment_key is None:
        raise DomainError(422, "VARIANT_WITHOUT_EXPERIMENT", "a variant is only meaningful with its experiment")
    if experiment_key is not None:
        spec = EXPERIMENTS.get(experiment_key)
        if spec is None or spec["surface"] != surface:
            raise DomainError(422, "INVALID_EXPERIMENT", "experiment does not apply to this surface")
        if is_killed(db, experiment_key):
            # Nothing rendered after a kill may count toward the experiment's result.
            raise DomainError(409, "EXPERIMENT_KILLED", "this experiment is switched off; exposure isn't recorded")
        assigned = assignment(db, principal.household_id, experiment_key)
        if variant != assigned.variant:
            raise DomainError(422, "VARIANT_MISMATCH", "rendered variant differs from the assignment",
                              assigned=assigned.variant)
        config_version = assigned.config_version
    inserted = db.execute(pg_insert(RenderReceipt).values(
        id=new_id("rr"), household_id=principal.household_id, member_id=principal.member_id,
        render_id=render_id, surface=surface, subject_type=SURFACES[surface], subject_id=subject_id,
        experiment_key=experiment_key, config_version=config_version, variant=variant,
        trace_id=current_trace_id()).on_conflict_do_nothing(constraint="uq_render_once")
        .returning(RenderReceipt.id)).scalar_one_or_none()
    receipt = db.execute(select(RenderReceipt).where(
        RenderReceipt.household_id == principal.household_id,
        RenderReceipt.render_id == render_id)).scalar_one()
    if inserted is None and (receipt.surface, receipt.subject_id, receipt.variant, receipt.member_id) != \
            (surface, subject_id, variant, principal.member_id):
        raise DomainError(409, "RENDER_ID_REUSED", "this render id was already used for a different render")
    return receipt, inserted is not None


def _validate(schema: dict[str, dict[str, Validator]], name: str, properties: dict[str, Any]) -> None:
    rules = schema.get(name)
    if rules is None:
        code = "SERVER_ONLY_EVENT" if name in SERVER_EVENTS else "UNKNOWN_EVENT"
        raise DomainError(422, code, f"analytics event {name!r} cannot be sent here")
    extra = set(properties) - set(rules)
    if extra:
        raise DomainError(422, "PROPERTY_NOT_ALLOWED", "analytics properties are allow-listed",
                          rejected=sorted(extra))
    bad = sorted(k for k, v in properties.items() if not rules[k](v))
    if bad:
        raise DomainError(422, "PROPERTY_INVALID", "analytics values must be counts, flags, enums or identifiers",
                          rejected=bad)


def ingest(db: Session, principal: Principal, *, event_id: str, name: str,
           properties: dict[str, Any], server_properties: Optional[dict[str, Any]] = None) -> bool:
    """A client event. ``server_properties`` are facts the server adds when the
    event arrives; a client can't send them, since its properties are allow-listed."""
    _validate(CLIENT_EVENTS, name, properties)
    inserted = db.execute(pg_insert(AnalyticsEvent).values(
        event_id=event_id, household_id=principal.household_id, name=name,
        properties=properties | (server_properties or {}),
        trace_id=current_trace_id()).on_conflict_do_nothing().returning(AnalyticsEvent.event_id)
    ).scalar_one_or_none()
    return inserted is not None


def record_server(db: Session, name: str, household_id: str, properties: dict[str, Any]) -> None:
    """Server analytics that are true only if the request commits."""
    _validate(SERVER_EVENTS, name, properties)
    db.add(AnalyticsEvent(event_id=f"srv_{uuid.uuid4().hex}", household_id=household_id, name=name,
                          properties=properties, trace_id=current_trace_id()))


def record_detached(name: str, household_id: Optional[str], properties: dict[str, Any]) -> None:
    """Server analytics that survive a rollback (a rejected purchase is still a
    fact worth counting), written on the same connection via keep()."""
    _validate(SERVER_EVENTS, name, properties)
    keep(AnalyticsEvent(event_id=f"srv_{uuid.uuid4().hex}", household_id=household_id, name=name,
                        properties=properties, trace_id=current_trace_id()))


def sample_ratio(counts: dict[str, int], expected: dict[str, float]) -> dict[str, Any]:
    """Sample-ratio mismatch check (chi-square goodness of fit).

    Before anyone reads an experiment's outcome, the share of units per variant
    must match the configured split. A mismatch (p < 0.001) means assignment or
    exposure is broken (for example, one variant fails to render and never
    sends its receipt), so the outcome must not be read at all."""
    total = sum(counts.values())
    if total == 0:
        return {"total": 0, "chi_square": None, "p_value": None, "mismatch": False}
    chi = sum((counts.get(v, 0) - total * share) ** 2 / (total * share) for v, share in expected.items())
    dof = len(expected) - 1
    # Chi-square survival function; closed forms for 1 and 2 degrees of freedom.
    p_value = math.erfc(math.sqrt(chi / 2)) if dof == 1 else math.exp(-chi / 2)
    return {"total": total, "counts": counts, "chi_square": round(chi, 3), "p_value": p_value,
            "mismatch": p_value < 0.001}


def experiment_health(db: Session, experiment_key: str) -> dict[str, Any]:
    """Assignment and exposure balance for one experiment, across households.
    An internal analytics view: aggregate counts only."""
    spec = EXPERIMENTS[experiment_key]
    expected = {v: 1 / len(spec["variants"]) for v in spec["variants"]}
    assigned = dict(db.execute(select(ExperimentAssignment.variant, func.count()).where(
        ExperimentAssignment.experiment_key == experiment_key,
        ExperimentAssignment.config_version == spec["config_version"]).group_by(ExperimentAssignment.variant)).all())
    exposed = dict(db.execute(select(RenderReceipt.variant, func.count(func.distinct(RenderReceipt.household_id))).where(
        RenderReceipt.experiment_key == experiment_key,
        RenderReceipt.config_version == spec["config_version"]).group_by(RenderReceipt.variant)).all())
    return {"experiment": experiment_key, "config_version": spec["config_version"],
            "assigned": sample_ratio(assigned, expected), "exposed": sample_ratio(exposed, expected)}
