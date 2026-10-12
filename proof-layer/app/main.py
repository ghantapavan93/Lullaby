"""Nanit Proof Layer — API.

Independent engineering prototype. Synthetic child records. Simulated
payments. Not affiliated with Nanit.

Run:  uvicorn app.main:app --port 8000      then open http://127.0.0.1:8000

Every write follows the same order: authenticate -> lock household ->
recheck membership -> authorize -> load resources scoped to the household ->
domain rule -> commit. Development-only routes live under /v1/dev and are
disabled unless PROOF_LAYER_DEV=1 (scripts/demo.py and the tests set it).
"""
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from . import (access, access_metrics, acks, claims, compare, customer_evidence, decisions, engine as evidence,
               entitlements, growth, metrics, proof, provider, provider_lifecycle, purchase_evidence, purchases,
               scenarios, seed, video_evidence, video_processing)
from .db import begin_request_scope, end_request_scope, get_db, transaction
from .models import (AccessAction, AccessCheck, AnalyticsEvent, AuthorizationRecord, BriefingRevision, BriefingWindow,
                     CapabilityDecision, Child, Device, EvidenceEvent, ExperimentAssignment, Household,
                     EntitlementClaim, IntentTransition, Member, Notification, OutboxJob, PurchaseIntent,
                     PurchaseEvidence, RenderReceipt, VideoAnalysisRun, VideoAnnotation, VideoAsset)
from .security import (POLICY, AuthError, Principal, authenticate, authorize, issue_token, load_owned,
                       lock_household, recheck_member, trace_id_var)

# Scenario controls are off unless explicitly enabled (scripts/demo.py sets it).
DEV = os.environ.get("PROOF_LAYER_DEV", "0") == "1"
ROOT = Path(__file__).resolve().parent.parent
DISCLOSURE = "Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit."

app = FastAPI(title="Nanit Proof Layer", version="1.0.0", description=DISCLOSURE)


# A reviewer instance (scripts/reviewer.py) has a hard end. After it, every
# request is refused, so a forgotten instance stops answering on its own even
# before it is reaped.
EXPIRES_AT = float(os.environ["PROOF_LAYER_EXPIRES_AT"]) if os.environ.get("PROOF_LAYER_EXPIRES_AT") else None


@app.middleware("http")
async def review_window(request: Request, call_next):
    if EXPIRES_AT is not None and time.time() >= EXPIRES_AT:
        return JSONResponse({"code": "REVIEW_EXPIRED",
                             "message": "This review instance has expired. Ask the author for a new link."},
                            status_code=410)
    return await call_next(request)


@app.post("/v1/workspace", include_in_schema=False)
def workspace_direct():
    """Run directly, there is one demo and no per-visitor workspace. Behind the
    review gateway (deploy/gateway) the gateway answers this route itself."""
    return {"gateway": False, "synthetic": True}


@app.middleware("http")
async def trace(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or f"trc_{uuid.uuid4().hex[:16]}"
    token = trace_id_var.set(trace_id[:40])
    scope = begin_request_scope()
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        end_request_scope(scope)
        trace_id_var.reset(token)
    route = request.scope.get("route")
    metrics.observe(request.method, getattr(route, "path", "unmatched"), response.status_code,
                    time.perf_counter() - started)
    response.headers["X-Trace-Id"] = trace_id[:40]
    response.headers["X-Prototype-Disclosure"] = DISCLOSURE
    return response


@app.exception_handler(AuthError)
async def auth_error(_: Request, exc: AuthError):
    return JSONResponse({"code": exc.code, "message": exc.message, "trace_id": trace_id_var.get()},
                        status_code=exc.status)


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse({"code": "INVALID_INPUT", "message": str(exc)[:200], "trace_id": trace_id_var.get()},
                        status_code=422)


@app.exception_handler(DataError)
async def data_error(_: Request, exc: DataError):
    """The database refused a value the request models let through (a NUL byte,
    an out-of-range number). That is bad input, not a server failure (RT-2)."""
    return JSONResponse({"code": "INVALID_INPUT", "message": "a value in the request can't be stored",
                         "trace_id": trace_id_var.get()}, status_code=422)


@app.exception_handler(evidence.DomainError)
async def domain_error(_: Request, exc: evidence.DomainError):
    return JSONResponse({"code": exc.code, "message": exc.message, "detail": exc.detail,
                         "trace_id": trace_id_var.get()}, status_code=exc.status)


def session_principal(db: Session, authorization: Optional[str]) -> Principal:
    return authenticate(db, authorization)


def write_scope(db: Session, authorization: Optional[str], action: str, resource: str) -> Principal:
    """authenticate -> household lock -> membership recheck -> authorize."""
    principal = authenticate(db, authorization)
    lock_household(db, principal.household_id)
    recheck_member(db, principal)
    authorize(principal, action, resource)
    return principal


# ------------------------------------------------------------------ schemas

Id = Field(min_length=1, max_length=40)
REVISION_MAX = 2**31 - 1   # revisions are PostgreSQL integers (RT-3)


class SessionIn(BaseModel):
    member_id: str = Id


class CorrectionIn(BaseModel):
    target_event_id: str = Id
    corrected_end: Optional[datetime] = None
    corrected_start: Optional[datetime] = None
    expected_revision: int
    client_event_id: str = Field(min_length=8, max_length=120)
    reason: str = Field(default="parent confirmed the actual time", max_length=200)


class AckIn(BaseModel):
    revision_id: str = Id
    displayed_facts: list[str] = Field(max_length=5)


class DecisionIn(BaseModel):
    feature: str = Id
    device_id: Optional[str] = Field(default=None, max_length=40)   # default: the household's primary camera
    surface: Literal["feature_availability", "night_briefing", "live_view", "safety_alert"] = "feature_availability"


class PurchaseIn(BaseModel):
    decision_id: str = Id
    offer_id: str = Field(min_length=1, max_length=60)
    expected_amount_minor: Optional[int] = Field(default=None, ge=0, le=10_000_000)
    expected_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)


class RenderIn(BaseModel):
    render_id: str = Field(min_length=8, max_length=80)
    surface: str = Id
    subject_id: str = Id
    experiment_key: Optional[str] = Field(default=None, max_length=60)
    variant: Optional[str] = Field(default=None, max_length=40)


class AnalyticsIn(BaseModel):
    event_id: str = Field(min_length=8, max_length=80)
    name: str = Field(max_length=60)
    properties: dict[str, Any] = {}


# ------------------------------------------------------------------ views

def revision_view(window: BriefingWindow, revision: BriefingRevision, child: Child) -> dict[str, Any]:
    return {"window_id": window.id, "child": {"id": child.id, "name": child.display_name},
            "local_date": window.local_date.isoformat(), "timezone": window.timezone,
            "revision": revision.revision, "revision_id": revision.id,
            "current_revision": window.current_revision,
            "is_current": revision.revision == window.current_revision,
            "facts": revision.facts, "fingerprints": revision.fingerprints,
            "evidence_ids": revision.evidence_ids, "cause": revision.cause,
            "computed_at": revision.created_at.isoformat() if revision.created_at else None,
            "trace_id": revision.trace_id, "synthetic": True}


def evidence_view(e: EvidenceEvent, superseded_by: Optional[str]) -> dict[str, Any]:
    return {"id": e.id, "client_event_id": e.client_event_id, "kind": e.kind, "source": e.source,
            "started_at": e.started_at.isoformat(), "ended_at": e.ended_at.isoformat(),
            "payload": e.payload, "supersedes_event_id": e.supersedes_event_id,
            "superseded_by": superseded_by, "actor_member_id": e.actor_member_id, "device_id": e.device_id,
            "received_at": e.received_at.isoformat() if e.received_at else None, "trace_id": e.trace_id}


# ------------------------------------------------------------------ identity

@app.get("/v1/me")
def me(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    household = db.get(Household, p.household_id)
    windows = db.execute(select(BriefingWindow).where(BriefingWindow.household_id == p.household_id)
                         .order_by(BriefingWindow.local_date.desc())).scalars()
    unread = db.execute(select(Notification).where(Notification.member_id == p.member_id)
                        .order_by(Notification.created_at.desc())).scalars()
    return {"member": {"id": p.member_id, "name": p.display_name, "role": p.role, "can_bill": p.can_bill},
            "household": {"id": household.id, "name": household.name, "timezone": household.timezone,
                          "state_revision": household.state_revision},
            "windows": [{"window_id": w.id, "child_id": w.child_id, "local_date": w.local_date.isoformat(),
                         "current_revision": w.current_revision} for w in windows],
            "notifications": [{"id": n.id, "kind": n.kind, "revision": n.revision, "window_id": n.window_id,
                               "body": n.body, "created_at": n.created_at.isoformat()} for n in unread],
            "disclosure": DISCLOSURE}


# ------------------------------------------------------------------ briefing

@app.get("/v1/windows/{window_id}/brief")
def get_brief(window_id: str, revision: Optional[int] = Query(None, ge=1, le=REVISION_MAX), db: Session = Depends(get_db),
              authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "briefing.read", f"window:{window_id}")
    window = load_owned(db, p, BriefingWindow, window_id, action="briefing.read")
    number = window.current_revision if revision is None else revision
    rev = db.execute(select(BriefingRevision).where(BriefingRevision.window_id == window.id,
                                                    BriefingRevision.revision == number)).scalar_one_or_none()
    if rev is None:
        raise evidence.DomainError(404, "NOT_FOUND", "no such revision")
    return revision_view(window, rev, db.get(Child, window.child_id))


@app.get("/v1/windows/{window_id}/week")
def get_week(window_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "briefing.read", f"window:{window_id}")
    window = load_owned(db, p, BriefingWindow, window_id, action="briefing.read")
    return evidence.week_view(db, window)


@app.get("/v1/windows/{window_id}/evidence")
def get_evidence(window_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "xray.read", f"window:{window_id}")
    window = load_owned(db, p, BriefingWindow, window_id, action="xray.read")
    rows = list(db.execute(select(EvidenceEvent).where(
        EvidenceEvent.household_id == p.household_id, EvidenceEvent.child_id == window.child_id,
        EvidenceEvent.started_at < window.ends_at, EvidenceEvent.ended_at > window.starts_at)
        .order_by(EvidenceEvent.started_at, EvidenceEvent.received_at)).scalars())
    superseded = {e.supersedes_event_id: e.id for e in rows if e.supersedes_event_id}
    return [evidence_view(e, superseded.get(e.id)) for e in rows]


@app.get("/v1/windows/{window_id}/revisions")
def get_revisions(window_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "xray.read", f"window:{window_id}")
    window = load_owned(db, p, BriefingWindow, window_id, action="xray.read")
    rows = db.execute(select(BriefingRevision).where(BriefingRevision.window_id == window.id)
                      .order_by(BriefingRevision.revision)).scalars()
    return [{"revision": r.revision, "revision_id": r.id, "cause": r.cause, "fingerprints": r.fingerprints,
             "evidence_ids": r.evidence_ids, "status": r.facts["status"],
             "recorded_sleep_minutes": r.facts["recorded_sleep"]["minutes"],
             "coverage_pct": r.facts["observation_coverage"]["pct"], "trace_id": r.trace_id,
             "created_at": r.created_at.isoformat()} for r in rows]


@app.post("/v1/windows/{window_id}/corrections")
def post_correction(window_id: str, body: CorrectionIn, authorization: Optional[str] = Header(None)):
    with transaction() as db:
        p = write_scope(db, authorization, "evidence.correct", f"window:{window_id}")
        window = load_owned(db, p, BriefingWindow, window_id, action="evidence.correct", lock=True)
        return evidence.apply_correction(
            db, p, window, target_event_id=body.target_event_id, corrected_start=body.corrected_start,
            corrected_end=body.corrected_end, expected_revision=body.expected_revision,
            client_event_id=body.client_event_id, reason=body.reason)


@app.post("/v1/windows/{window_id}/acknowledgments")
def post_ack(window_id: str, body: AckIn, authorization: Optional[str] = Header(None)):
    with transaction() as db:
        p = write_scope(db, authorization, "briefing.acknowledge", f"window:{window_id}")
        window = load_owned(db, p, BriefingWindow, window_id, action="briefing.acknowledge", lock=True)
        return acks.acknowledge(db, p, window, revision_id=body.revision_id,
                                displayed_facts=body.displayed_facts)


@app.get("/v1/windows/{window_id}/changes")
def get_changes(window_id: str, since_revision: Optional[int] = Query(None, ge=1, le=REVISION_MAX),
                db: Session = Depends(get_db),
                authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "briefing.read", f"window:{window_id}")
    window = load_owned(db, p, BriefingWindow, window_id, action="briefing.read")
    latest = evidence.current_revision(db, window)
    base = None
    if since_revision is not None:
        base = db.execute(select(BriefingRevision).where(BriefingRevision.window_id == window.id,
                                                         BriefingRevision.revision == since_revision)).scalar_one_or_none()
        if base is None:
            raise evidence.DomainError(404, "NOT_FOUND", f"revision {since_revision} does not exist")
    elif latest and latest.revision > 1:
        base = db.execute(select(BriefingRevision).where(BriefingRevision.window_id == window.id,
                                                         BriefingRevision.revision == latest.revision - 1)).scalar_one()
    return {"current_revision": latest.revision if latest else None,
            "compared_with": base.revision if base else None,
            "latest_cause": latest.cause if latest else None,
            "changes": evidence.diff(base, latest) if latest else [],
            "members": acks.window_status(db, window)}


# ------------------------------------------------------------------ commerce

@app.get("/v1/features")
def features(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    session_principal(db, authorization)
    from .entitlements import POLICIES
    _, policy_version = decisions.active_versions(db)
    return [{"feature": k, "label": v["label"], "parent_value": v["parent_value"], "sources": v["sources"],
             "assumption": v.get("assumption")} for k, v in POLICIES[policy_version]["features"].items()]


@app.post("/v1/capability-decisions")
def post_decision(body: DecisionIn, authorization: Optional[str] = Header(None)):
    with transaction() as db:
        p = write_scope(db, authorization, "capability.evaluate", f"feature:{body.feature}")
        household = db.get(Household, p.household_id)
        device = camera_for(db, p, body.device_id, action="capability.evaluate")
        decision = decisions.create(db, p, household, body.feature, device, body.surface)
        return decisions.view(db, decision, household) | {
            "experiment": growth.serve(db, p.household_id, "availability_explanation")}


@app.get("/v1/capability-decisions/{decision_id}")
def get_decision(decision_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "capability.evaluate", f"decision:{decision_id}")
    decision = load_owned(db, p, CapabilityDecision, decision_id, action="capability.evaluate")
    return decisions.view(db, decision, db.get(Household, p.household_id))


@app.post("/v1/purchase-intents")
def post_purchase(body: PurchaseIn, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8,
                                                                  max_length=120),
                  authorization: Optional[str] = Header(None)):
    with transaction() as db:
        p = write_scope(db, authorization, "purchase.create", f"decision:{body.decision_id}")
        intent, replayed = purchases.prepare(
            db, p, decision_id=body.decision_id, offer_id=body.offer_id, idempotency_key=idempotency_key,
            expected_amount_minor=body.expected_amount_minor, expected_currency=body.expected_currency)
        return JSONResponse(purchases.view(intent) | {"replayed": replayed}, status_code=200 if replayed else 201)


@app.get("/v1/purchase-intents/{intent_id}")
def get_purchase(intent_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "purchase.read", f"intent:{intent_id}")
    intent = load_owned(db, p, PurchaseIntent, intent_id, action="purchase.read")
    transitions = db.execute(select(IntentTransition).where(IntentTransition.intent_id == intent.id)
                             .order_by(IntentTransition.id)).scalars()
    job = db.execute(select(OutboxJob).where(OutboxJob.dedupe_key == f"submit:{intent.id}")).scalar_one_or_none()
    return purchases.view(intent) | {
        "transitions": [{"from": t.from_state, "to": t.to_state, "reason": t.reason, "actor": t.actor,
                         "detail": t.detail, "trace_id": t.trace_id, "at": t.at.isoformat()} for t in transitions],
        "outbox": None if job is None else {"state": job.state, "attempts": job.attempts,
                                            "last_error": job.last_error},
        "provider_record": provider.lookup(intent.provider_operation_key)}


@app.get("/v1/purchase-intents")
def list_purchases(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "purchase.read", "intents")
    rows = db.execute(select(PurchaseIntent).where(PurchaseIntent.household_id == p.household_id)
                      .order_by(PurchaseIntent.created_at.desc())).scalars()
    return [purchases.view(i) for i in rows]


@app.get("/v1/household/commercial")
def commercial(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """Each camera with its own derived plan, plan state and team-member limit."""
    p = session_principal(db, authorization)
    authorize(p, "commercial.read", "household")
    household = db.get(Household, p.household_id)
    catalog_version, _ = decisions.active_versions(db)
    devices = db.execute(select(Device).where(Device.household_id == p.household_id)
                         .order_by(Device.is_primary.desc(), Device.id)).scalars()
    return {"state_revision": household.state_revision,
            "devices": [{"id": d.id, "label": d.label, "model": d.model, "mount": d.mount,
                         "mount_label": entitlements.MOUNT_LABELS.get(d.mount, d.mount), "is_primary": d.is_primary,
                         "plan": claims.camera_plan(db, p.household_id, d.id, catalog_version)} for d in devices],
            "member_cap_note": entitlements.MEMBER_CAP_ASSUMPTION}


def camera_for(db: Session, p: Principal, device_id: Optional[str], *, action: str) -> Device:
    if device_id:
        return load_owned(db, p, Device, device_id, action=action)
    device = db.execute(select(Device).where(Device.household_id == p.household_id, Device.is_primary)).scalar_one_or_none()
    if device is None:
        raise evidence.DomainError(422, "DEVICE_REQUIRED", "this household has no primary camera; name one")
    return device


@app.get("/v1/devices/{device_id}/plan-preview")
def plan_preview(device_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """What each plan would and would not unlock on THIS camera, using the same
    rules as decisions. Nothing is stored and nothing is offered."""
    p = session_principal(db, authorization)
    authorize(p, "commercial.read", f"device:{device_id}")
    device = load_owned(db, p, Device, device_id, action="commercial.read")
    catalog_version, policy_version = decisions.active_versions(db)
    plans = entitlements.CATALOGS[catalog_version]["plans"]
    features = entitlements.POLICIES[policy_version]["features"]
    current = claims.camera_plan(db, p.household_id, device.id, catalog_version)
    rows = []
    for code in sorted(plans, key=lambda c: plans[c]["rank"]):
        unlocks = {}
        for feature in features:
            r = entitlements.evaluate(feature=feature, plan_code=code, mounts=[device.mount],
                                      catalog_version=catalog_version, policy_version=policy_version,
                                      with_offer=False)
            unlocks[feature] = {"status": r["status"], "blocked_by": [b["kind"] for b in r["blockers"]]}
        rows.append({"plan_code": code, "plan_label": plans[code]["label"],
                     "amount_minor": plans[code].get("amount_minor"), "currency": plans[code].get("currency"),
                     "is_current": code == current["plan_code"], "features": unlocks})
    return {"device": {"id": device.id, "label": device.label, "mount": device.mount,
                       "mount_label": entitlements.MOUNT_LABELS.get(device.mount, device.mount)},
            "current_plan": current, "plans": rows,
            "feature_labels": {k: v["label"] for k, v in features.items()}, "simulated": True}


# ------------------------------------------------------------------ purchase to access

class AccessCheckIn(BaseModel):
    device_id: str = Id
    channel: Literal["web", "apple", "google", "activation_code", "trial", "simulated_purchase"]
    external_ref: str = Field(min_length=3, max_length=120)


class BindIn(BaseModel):
    device_id: str = Id
    channel: Literal["web", "apple", "google", "activation_code"]
    external_ref: str = Field(min_length=3, max_length=120)
    expected_state_revision: int = Field(ge=0, le=REVISION_MAX)


class VerifyIn(BaseModel):
    device_id: str = Id


def access_view(p: Principal, result: dict[str, Any]) -> dict[str, Any]:
    """Purchase references and the reconciliation trace are commercial detail:
    owners and admins see them; a caregiver sees the status and the stages."""
    if p.role in POLICY["commercial.read"]:
        return result
    return {k: v for k, v in result.items() if k not in ("focus_purchase", "trace")}


@app.get("/v1/access")
def access_overview(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """Family home: every camera's access status. Reading writes nothing."""
    p = session_principal(db, authorization)
    authorize(p, "access.read", "household")
    view = access.overview(db, p, db.get(Household, p.household_id))
    view["cameras"] = [access_view(p, c) for c in view["cameras"]]
    return view


@app.get("/v1/access/cameras/{device_id}")
def access_camera(device_id: str, feature: Optional[str] = Query(None, max_length=40),
                  db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "access.read", f"device:{device_id}")
    device = load_owned(db, p, Device, device_id, action="access.read")
    return access_view(p, access.assess(db, db.get(Household, p.household_id), device,
                                        member_can_bind=access.can_bind(p), feature=feature))


@app.post("/v1/access/check")
def access_check(body: AccessCheckIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """Where a specific purchase or code stands for one camera. A POST only to
    keep purchase references out of URLs and logs; it writes nothing. A
    purchase on another family's account reads exactly like a missing one."""
    p = session_principal(db, authorization)
    authorize(p, "commercial.read", f"device:{body.device_id}")
    device = load_owned(db, p, Device, body.device_id, action="commercial.read")
    return access.assess(db, db.get(Household, p.household_id), device, member_can_bind=access.can_bind(p),
                         purchase=(body.channel, body.external_ref))


@app.post("/v1/access/bind")
def access_bind(body: BindIn, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8,
                                                            max_length=120),
                authorization: Optional[str] = Header(None)):
    """Connect a confirmed purchase to a camera. Completing it is an operation,
    not yet a verified capability: call /v1/access/verify next."""
    with transaction() as db:
        p = write_scope(db, authorization, "access.bind", f"device:{body.device_id}")
        household = db.get(Household, p.household_id)
        device = load_owned(db, p, Device, body.device_id, action="access.bind")
        outcome, replayed = access.bind(db, p, household, device, channel=body.channel,
                                        external_ref=body.external_ref,
                                        expected_state_revision=body.expected_state_revision,
                                        idempotency_key=idempotency_key)
        return JSONResponse(outcome | {"replayed": replayed}, status_code=200 if replayed else 201)


@app.post("/v1/access/verify")
def access_verify(body: VerifyIn, authorization: Optional[str] = Header(None)):
    with transaction() as db:
        p = write_scope(db, authorization, "access.verify", f"device:{body.device_id}")
        household = db.get(Household, p.household_id)
        device = load_owned(db, p, Device, body.device_id, action="access.verify")
        result = access.verify(db, p, household, device)
        return result | {"access": access_view(p, result["access"])}


@app.get("/v1/access/cameras/{device_id}/window")
def access_window(device_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "access.read", f"device:{device_id}")
    device = load_owned(db, p, Device, device_id, action="access.read")
    return access.window(db, p, db.get(Household, p.household_id), device)


@app.get("/v1/access/cameras/{device_id}/outcome")
def access_outcome(device_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "commercial.read", f"device:{device_id}")
    device = load_owned(db, p, Device, device_id, action="commercial.read")
    return access.outcome_review(db, db.get(Household, p.household_id), device)


@app.get("/v1/access/metrics")
def access_funnel(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """This family's purchase-to-access funnel, computed from its records at read
    time. Synthetic in this prototype; purchases are numbered, never named."""
    p = session_principal(db, authorization)
    authorize(p, "commercial.read", "access-metrics")
    return access_metrics.household_funnel(db, p.household_id)


# ------------------------------------------------------------------ growth

@app.post("/v1/render-receipts")
def post_render(body: RenderIn, authorization: Optional[str] = Header(None)):
    with transaction() as db:
        p = authenticate(db, authorization)
        authorize(p, "growth.render", body.surface)
        receipt, created = growth.record_render(db, p, render_id=body.render_id, surface=body.surface,
                                                subject_id=body.subject_id, experiment_key=body.experiment_key,
                                                variant=body.variant)
        return {"receipt_id": receipt.id, "created": created, "variant": receipt.variant}


@app.post("/v1/analytics")
def post_analytics(body: AnalyticsIn, authorization: Optional[str] = Header(None)):
    with transaction() as db:
        p = authenticate(db, authorization)
        created = growth.ingest(db, p, event_id=body.event_id, name=body.name, properties=body.properties)
        return {"event_id": body.event_id, "created": created}


# ------------------------------------------------------------------ x-ray

@app.get("/v1/proof")
def proof_explorer(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """Data & Proof Explorer: public-data audit, live schema, code-resolved flows,
    benchmarks, mutation and test results. No household's records are included;
    table sizes are planner estimates across the whole database."""
    p = session_principal(db, authorization)
    authorize(p, "xray.read", "proof")
    return proof.explorer(db)


@app.get("/v1/customer-evidence")
def customer_evidence_map(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """Public reports (paraphrased, dated) mapped to synthetic scenarios and the
    tests behind our answer. No household data."""
    p = session_principal(db, authorization)
    authorize(p, "xray.read", "customer-evidence")
    return customer_evidence.catalog()


@app.get("/v1/xray")
def xray(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    p = session_principal(db, authorization)
    authorize(p, "xray.read", "household")
    hh = p.household_id

    def rows(model, order, limit=40):
        return list(db.execute(select(model).where(model.household_id == hh).order_by(order.desc())
                               .limit(limit)).scalars())

    # Purchase data (transitions, provider charges, purchase analytics, submit jobs)
    # follows the same rule as /v1/purchase-intents: owners and admins only.
    commerce = p.role in POLICY["commercial.read"]
    hidden = {"purchase_rejected", "purchase_confirmed", "purchase_cancelled_before_submission"}
    return {
        "commerce_visible": commerce,
        "authorization": [{"member_id": r.member_id, "action": r.action, "resource": r.resource,
                           "allowed": r.allowed, "reason": r.reason, "trace_id": r.trace_id,
                           "at": r.at.isoformat()} for r in rows(AuthorizationRecord, AuthorizationRecord.id)],
        "outbox": [{"id": j.id, "kind": j.kind, "dedupe_key": j.dedupe_key, "state": j.state,
                    "attempts": j.attempts, "last_error": j.last_error, "trace_id": j.trace_id}
                   for j in rows(OutboxJob, OutboxJob.id) if commerce or j.kind != "submit_purchase"],
        "intent_transitions": [{"intent_id": t.intent_id, "from": t.from_state, "to": t.to_state,
                                "reason": t.reason, "actor": t.actor, "trace_id": t.trace_id,
                                "at": t.at.isoformat()} for t in rows(IntentTransition, IntentTransition.id)]
                              if commerce else [],
        "analytics": [{"name": a.name, "properties": a.properties, "event_id": a.event_id,
                       "trace_id": a.trace_id, "at": a.received_at.isoformat()}
                      for a in rows(AnalyticsEvent, AnalyticsEvent.received_at)
                      if commerce or a.name not in hidden],
        "experiments": [{"experiment_key": a.experiment_key, "config_version": a.config_version,
                         "variant": a.variant, "assigned_at": a.assigned_at.isoformat()}
                        for a in rows(ExperimentAssignment, ExperimentAssignment.assigned_at)],
        "render_receipts": [{"render_id": r.render_id, "surface": r.surface, "subject_id": r.subject_id,
                             "variant": r.variant, "trace_id": r.trace_id, "at": r.created_at.isoformat()}
                            for r in rows(RenderReceipt, RenderReceipt.created_at)],
        "provider_ledger": provider.ledger(hh) if commerce else [],
        "entitlement_claims": [{"device_id": c.device_id, "channel": c.channel, "external_ref": c.external_ref,
                                "event_status": c.event_status, "plan_code": c.plan_code,
                                "starts_at": c.starts_at.isoformat(),
                                "ends_at": c.ends_at.isoformat() if c.ends_at else None,
                                "received_at": c.received_at.isoformat(), "trace_id": c.trace_id}
                               for c in rows(EntitlementClaim, EntitlementClaim.received_at)] if commerce else [],
        "experiment_definitions": growth.EXPERIMENTS,
        "purchase_evidence": [{"channel": e.channel, "external_ref": e.external_ref, "plan_code": e.plan_code,
                               "status": e.status, "provider_version": e.provider_version, "trace_id": e.trace_id,
                               "provider_event": e.provider_event,
                               "starts_at": e.starts_at.isoformat(),
                               "ends_at": e.ends_at.isoformat() if e.ends_at else None,
                               "at": e.received_at.isoformat()}
                              for e in rows(PurchaseEvidence, PurchaseEvidence.received_at)] if commerce else [],
        "access_actions": [{"device_id": a.device_id, "channel": a.channel, "external_ref": a.external_ref,
                            "idempotency_key": a.idempotency_key, "outcome": a.outcome, "trace_id": a.trace_id,
                            "at": a.created_at.isoformat()}
                           for a in rows(AccessAction, AccessAction.created_at)] if commerce else [],
        "access_checks": [access.check_view(c) | {"device_id": c.device_id}
                          for c in rows(AccessCheck, AccessCheck.created_at)],
        # Video evidence: stored rows only (no media bytes, no storage path).
        "video_assets": [{"id": a.id, "device_id": a.device_id, "source_kind": a.source_kind, "sha256": a.sha256,
                          "size_bytes": a.size_bytes, "codec": a.codec, "duration_s": a.duration_s,
                          "evidence_revision": a.evidence_revision, "deleted": a.deleted_at is not None}
                         for a in rows(VideoAsset, VideoAsset.created_at)],
        "video_runs": [{"id": r.id, "asset_id": r.asset_id, "state": r.state, "attempts": r.attempts,
                        "processor_version": r.processor_version, "config_digest": r.config_digest,
                        "error_code": r.error_code, "elapsed_ms": r.elapsed_ms,
                        "frames": (r.summary or {}).get("frames", {}).get("decoded"), "trace_id": r.trace_id}
                       for r in rows(VideoAnalysisRun, VideoAnalysisRun.created_at)],
        "video_annotations": [{"id": a.id, "asset_id": a.asset_id, "member_id": a.member_id, "revision": a.revision,
                               "label": a.label, "t_start": a.t_start, "t_end": a.t_end,
                               "supersedes": a.supersedes_annotation_id}
                              for a in rows(VideoAnnotation, VideoAnnotation.created_at)],
    }


# ------------------------------------------------------------------ development-only scenario controls

def require_dev() -> None:
    if not DEV:
        raise AuthError(404, "NOT_FOUND", "development routes are disabled")


# Checked before the body is validated, so a disabled route answers 404 for any
# request, never 422: it doesn't reveal what it would have accepted.
DEV_ONLY = [Depends(require_dev)]


@app.get("/v1/dev/accounts", dependencies=DEV_ONLY)
def dev_accounts(db: Session = Depends(get_db)):
    require_dev()
    members = db.execute(select(Member).order_by(Member.household_id, Member.created_at)).scalars()
    return [{"member_id": m.id, "name": m.display_name, "role": m.role, "household_id": m.household_id,
             "revoked": m.revoked_at is not None} for m in members]


@app.post("/v1/dev/sessions", dependencies=DEV_ONLY)
def dev_session(body: SessionIn, db: Session = Depends(get_db)):
    require_dev()
    if db.get(Member, body.member_id) is None:
        raise AuthError(404, "NOT_FOUND", "unknown synthetic account")
    return {"token": issue_token(body.member_id), "member_id": body.member_id,
            "warning": "development-only session; not real authentication"}


@app.post("/v1/dev/reset", dependencies=DEV_ONLY)
def dev_reset(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    require_dev()
    if db.execute(select(Member.id).limit(1)).first() is not None:   # an empty DB bootstraps without a session
        authorize(session_principal(db, authorization), "dev.control", "reset")
    db.close()
    provider.create_schema()
    return seed.load_fresh()


class MountIn(BaseModel):
    mount: Literal["floor_stand", "wall_mount", "flex_stand", "multi_stand", "anywhere_stand"]
    device_id: Optional[str] = Field(default=None, max_length=40)   # default: primary camera


@app.post("/v1/dev/mount", dependencies=DEV_ONLY)
def dev_mount(body: MountIn, authorization: Optional[str] = Header(None)):
    """Scenario control: the family moves one camera. A commercial-state change."""
    require_dev()
    with transaction() as db:
        p = write_scope(db, authorization, "capability.evaluate", "devices")
        household = db.get(Household, p.household_id)
        device = camera_for(db, p, body.device_id, action="capability.evaluate")
        device.mount = body.mount
        household.state_revision += 1
        return {"state_revision": household.state_revision, "device_id": device.id, "mount": body.mount}


class ClaimIn(BaseModel):
    device_id: Optional[str] = Field(default=None, max_length=40)
    channel: Literal["trial", "apple", "google", "web", "activation_code"]
    external_ref: str = Field(min_length=3, max_length=120)
    event_status: Literal["active", "pending", "revoked"]
    plan_code: Literal["sleep", "memories", "milestones"]
    starts_at: Optional[datetime] = None   # the store's own event date; default: start of today (UTC)
    days: int = Field(default=365, ge=1, le=3650)

    @field_validator("starts_at")
    @classmethod
    def _aware_and_plausible(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Store events carry an offset and a real date (RT-5)."""
        if v is None:
            return v
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("starts_at must include a timezone offset")
        if not 2000 <= v.year <= 2100:
            raise ValueError("starts_at must be between the years 2000 and 2100")
        return v


@app.post("/v1/dev/claims", dependencies=DEV_ONLY)
def dev_claim(body: ClaimIn, authorization: Optional[str] = Header(None)):
    """Scenario control: a synthetic event from a purchase channel (an App Store
    renewal, a Google Play purchase, a redeemed activation code, a refund).
    Simulated input only; no store is contacted."""
    require_dev()
    with transaction() as db:
        p = write_scope(db, authorization, "dev.control", "claims")
        household = db.get(Household, p.household_id)
        device = camera_for(db, p, body.device_id, action="dev.control")
        catalog_version, _ = decisions.active_versions(db)
        # A store event carries its own dates, so a re-delivered event hashes the same.
        starts = body.starts_at or claims.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = claims.record(db, household, device, channel=body.channel, external_ref=body.external_ref,
                               event_status=body.event_status, plan_code=body.plan_code, starts_at=starts,
                               ends_at=starts + timedelta(days=body.days), catalog_version=catalog_version)
        return result | {"state_revision": household.state_revision, "device_id": device.id}


class EvidenceIn(BaseModel):
    channel: Literal["web", "apple", "google", "activation_code"]
    external_ref: str = Field(min_length=3, max_length=120)
    plan_code: Literal["sleep", "memories", "milestones"]
    # An evidence status, a store event (app/provider_lifecycle.py), or both when they agree.
    status: Optional[Literal["verified", "pending", "refunded", "unverified", "grace", "billing_retry", "paused",
                             "expired"]] = None
    provider_event: Optional[str] = Field(default=None, min_length=3, max_length=64)
    provider_version: int = Field(ge=1, le=REVISION_MAX)
    starts_at: Optional[datetime] = None
    # Omitted: a year for a purchase; no end for billing retry, a pause or an
    # expiry; refused for a grace period, which must say when it ends.
    days: Optional[int] = Field(default=None, ge=1, le=3650)

    @model_validator(mode="after")
    def _status_or_event(self) -> "EvidenceIn":
        if self.status is None and self.provider_event is None:
            raise ValueError("give a status or a provider_event")
        return self

    @field_validator("starts_at")
    @classmethod
    def _aware_and_plausible(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Provider events carry an offset and a real date, like store claims (RT-5)."""
        if v is None:
            return v
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("starts_at must include a timezone offset")
        if not 2000 <= v.year <= 2100:
            raise ValueError("starts_at must be between the years 2000 and 2100")
        return v


@app.post("/v1/dev/purchase-evidence", dependencies=DEV_ONLY)
def dev_purchase_evidence(body: EvidenceIn, authorization: Optional[str] = Header(None)):
    """Scenario control: a synthetic provider event about a purchase (a website
    order, a store receipt, an activation code, a refund, a store billing
    notification). No store is contacted and no signature is verified."""
    require_dev()
    with transaction() as db:
        p = write_scope(db, authorization, "dev.control", "purchase-evidence")
        household = db.get(Household, p.household_id)
        catalog_version, _ = decisions.active_versions(db)
        starts = body.starts_at or claims.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        status = body.status or provider_lifecycle.translate(body.channel, body.provider_event)
        days = body.days or (None if status in provider_lifecycle.SUBSCRIPTION_ONLY else 365)
        result = purchase_evidence.record(db, household, channel=body.channel, external_ref=body.external_ref,
                                          plan_code=body.plan_code, status=body.status,
                                          provider_version=body.provider_version, starts_at=starts,
                                          ends_at=starts + timedelta(days=days) if days else None,
                                          catalog_version=catalog_version, provider_event=body.provider_event)
        return result | {"state_revision": household.state_revision}


class CompareIn(BaseModel):
    a: str = Field(min_length=1, max_length=40)
    b: str = Field(min_length=1, max_length=40)


@app.post("/v1/dev/compare", dependencies=DEV_ONLY)
def dev_compare(body: CompareIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """Scenario Studio: the same symptom from two causes. Builds each scenario
    in turn through the real runner and reports the backend's decision and its
    refusals. Refusal probes run in a transaction that is rolled back."""
    require_dev()
    authorize(session_principal(db, authorization), "dev.control", f"compare:{body.a}:{body.b}")
    db.close()
    provider.create_schema()
    return compare.compare(body.a, body.b)


class ScenarioIn(BaseModel):
    scenario: str = Field(min_length=1, max_length=40)


@app.get("/v1/dev/access-scenarios", dependencies=DEV_ONLY)
def dev_access_scenarios():
    require_dev()
    return scenarios.catalog()


@app.post("/v1/dev/access-scenario", dependencies=DEV_ONLY)
def dev_access_scenario(body: ScenarioIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """Scenario Studio: reset the demo data and build one situation through the
    application's own code paths."""
    require_dev()
    authorize(session_principal(db, authorization), "dev.control", f"scenario:{body.scenario}")
    if body.scenario not in scenarios.SCENARIOS:
        raise evidence.DomainError(422, "UNKNOWN_SCENARIO", "unknown scenario", allowed=list(scenarios.SCENARIOS))
    db.close()
    provider.create_schema()
    return scenarios.run(body.scenario)


class BehaviorIn(BaseModel):
    behavior: Literal["succeed", "decline", "pending", "lose_response", "unavailable"]


@app.post("/v1/dev/provider/next", dependencies=DEV_ONLY)
def dev_provider_next(body: BehaviorIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    require_dev()
    p = session_principal(db, authorization)
    authorize(p, "dev.control", "provider")
    provider.schedule(p.household_id, body.behavior)
    return {"scheduled": body.behavior}


class ResolveIn(BaseModel):
    operation_key: str = Field(min_length=1, max_length=80)
    status: Literal["SUCCEEDED", "FAILED"]


@app.post("/v1/dev/provider/resolve", dependencies=DEV_ONLY)
def dev_provider_resolve(body: ResolveIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    require_dev()
    p = session_principal(db, authorization)
    authorize(p, "dev.control", "provider")
    intent = db.execute(select(PurchaseIntent).where(PurchaseIntent.provider_operation_key == body.operation_key,
                                                     PurchaseIntent.household_id == p.household_id)).scalar_one_or_none()
    if intent is None:
        raise AuthError(404, "NOT_FOUND", "operation not found")
    return provider.resolve_pending(body.operation_key, body.status)


class ExperimentSwitchIn(BaseModel):
    killed: bool
    reason: str = Field(min_length=3, max_length=200)


@app.post("/v1/dev/experiments/{experiment_key}", dependencies=DEV_ONLY)
def dev_experiment_switch(experiment_key: str, body: ExperimentSwitchIn, authorization: Optional[str] = Header(None)):
    """Scenario control: kill or revive an experiment at run time. In a real
    deployment this is an operator action with its own permission."""
    require_dev()
    with transaction() as db:
        p = write_scope(db, authorization, "dev.control", f"experiment:{experiment_key}")
        return growth.set_killed(db, experiment_key, body.killed, body.reason, p.member_id)


class WorkerIn(BaseModel):
    crash_after_provider: bool = False


@app.post("/v1/dev/worker", dependencies=DEV_ONLY)
def dev_worker(body: WorkerIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """Runs the real worker as a separate OS process. With crash_after_provider
    the process exits (os._exit) after the provider commits and before the
    application records the outcome."""
    require_dev()
    authorize(session_principal(db, authorization), "dev.control", "worker")
    db.close()
    cmd = [sys.executable, "-m", "app.worker", "--once"] + (["--crash-after-provider"] if body.crash_after_provider else [])
    env = os.environ | {"PROOF_LAYER_LEASE_SECONDS": os.environ.get("PROOF_LAYER_LEASE_SECONDS", "3")}
    done = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    return {"command": " ".join(cmd[1:]), "exit_code": done.returncode,
            "crashed": done.returncode == 17, "stdout": done.stdout[-2000:], "stderr": done.stderr[-2000:]}


# ------------------------------------------------------------------ video evidence

# The video module can be switched off (PROOF_LAYER_VIDEO=0) without touching
# the purchase-to-access demo: every route below then answers 404. Read per
# request, so it takes effect without a code change.
def request_is_https() -> bool:
    """Cookies are marked Secure when the demo runs behind HTTPS."""
    return os.environ.get("PROOF_LAYER_HTTPS", "0") == "1"


def require_video() -> None:
    if os.environ.get("PROOF_LAYER_VIDEO", "1") != "1":
        raise AuthError(404, "NOT_FOUND", "the video evidence module is switched off")


VIDEO_ONLY = [Depends(require_video)]


def owned_asset(db: Session, p: Principal, asset_id: str, *, action: str, lock: bool = False) -> VideoAsset:
    return load_owned(db, p, VideoAsset, asset_id, action=action, lock=lock)


@app.get("/v1/video/status", dependencies=VIDEO_ONLY)
def video_status(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    session_principal(db, authorization)
    return {"enabled": True, "processor_version": video_processing.PROCESSOR_VERSION,
            "measures": ["frame timestamps", "brightness", "sharpness", "camera shift", "local pixel change"],
            "not_claimed": ["sleep or wake state", "breathing", "health or safety", "who or what moved"]}


@app.get("/v1/video/assets", dependencies=VIDEO_ONLY)
def video_assets(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """This family's clips, with each camera's access gate. Read-only."""
    p = session_principal(db, authorization)
    authorize(p, "video.read", "video-assets")
    household = db.get(Household, p.household_id)
    rows = list(db.execute(select(VideoAsset).where(VideoAsset.household_id == p.household_id,
                                                    VideoAsset.deleted_at.is_(None))
                           .order_by(VideoAsset.created_at.desc())).scalars())
    gates = {d.id: video_evidence.gate(db, household, d) for d in
             db.execute(select(Device).where(Device.household_id == p.household_id)).scalars()}
    def run_state(a: VideoAsset) -> Optional[dict[str, Any]]:
        """Only the run's state: measurements are served by the gated view."""
        r = video_evidence.latest_run(db, a)
        return None if r is None else {"id": r.id, "state": r.state, "error_code": r.error_code}

    return {"assets": [video_evidence.asset_view(a) | {"run": run_state(a), "evidence_revision": a.evidence_revision}
                       for a in rows],
            "gates": gates}


@app.get("/v1/video/assets/{asset_id}", dependencies=VIDEO_ONLY)
def video_view(asset_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """The evidence view, from stored rows. 403 FEATURE_NOT_IN_PLAN when the
    camera's plan doesn't include video history. Read-only."""
    p = session_principal(db, authorization)
    authorize(p, "video.read", f"video:{asset_id}")
    asset = owned_asset(db, p, asset_id, action="video.read")
    return video_evidence.view(db, p, db.get(Household, p.household_id), asset)


@app.post("/v1/video/assets/{asset_id}/media-link", dependencies=VIDEO_ONLY)
def video_media_link(asset_id: str, authorization: Optional[str] = Header(None)):
    """Access to the clip's bytes for five minutes, granted after the plan
    check (which is recorded). The signed token travels in an HttpOnly cookie
    scoped to this clip's media path, so it never appears in a URL, a log line
    or the page's script (review, 2026-10-11)."""
    with transaction() as db:
        p = write_scope(db, authorization, "video.read", f"video:{asset_id}")
        asset = owned_asset(db, p, asset_id, action="video.read")
        if asset.deleted_at is not None:
            raise evidence.DomainError(410, "ASSET_DELETED", "this clip was deleted")
        household = db.get(Household, p.household_id)
        video_evidence.require_gate(db, household, db.get(Device, asset.device_id), record=True)
        token, expires = video_evidence.media_token(p, asset)
        response = JSONResponse({"url": f"/v1/video/media/{asset.id}", "expires_at": expires})
        response.set_cookie(video_evidence.MEDIA_COOKIE, token, max_age=int(video_evidence.MEDIA_TOKEN_TTL.total_seconds()),
                            path=f"/v1/video/media/{asset.id}", httponly=True, samesite="strict",
                            secure=request_is_https())
        return response


@app.get("/v1/video/media/{asset_id}", dependencies=VIDEO_ONLY, include_in_schema=False)
def video_media(asset_id: str, request: Request, db: Session = Depends(get_db)):
    """Clip bytes, only with a valid signed cookie for this clip, re-checked
    against the member, household, deletion and plan on every request (a
    refresh or a range request included). Never cached by shared caches."""
    token = request.cookies.get(video_evidence.MEDIA_COOKIE)
    if not token or len(token) > 200:
        raise evidence.DomainError(404, "NOT_FOUND", "media not found")
    member_id = video_evidence.check_media_token(token, asset_id)
    member = db.get(Member, member_id)
    asset = db.get(VideoAsset, asset_id)
    if member is None or member.revoked_at is not None or asset is None or asset.household_id != member.household_id:
        raise evidence.DomainError(404, "NOT_FOUND", "media not found")
    if asset.deleted_at is not None:
        raise evidence.DomainError(410, "ASSET_DELETED", "this clip was deleted")
    household = db.get(Household, asset.household_id)
    video_evidence.require_gate(db, household, db.get(Device, asset.device_id), record=False)
    path = video_evidence.media_root() / asset.storage_key
    if not path.is_file():
        raise evidence.DomainError(410, "MEDIA_UNAVAILABLE", "the clip's bytes are no longer stored")
    return FileResponse(path, media_type="video/webm" if asset.storage_key.endswith(".webm") else "video/mp4",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@app.post("/v1/video/assets/{asset_id}/analysis", status_code=202, dependencies=VIDEO_ONLY)
def video_analyze(asset_id: str, authorization: Optional[str] = Header(None)):
    """Queue the analysis. Repeating it returns the same run."""
    with transaction() as db:
        p = write_scope(db, authorization, "video.manage", f"video:{asset_id}")
        asset = owned_asset(db, p, asset_id, action="video.manage", lock=True)
        run, created = video_evidence.request_analysis(db, db.get(Household, p.household_id), asset)
        return {"run": video_evidence.run_view(run), "created": created}


@app.post("/v1/video/assets/{asset_id}/analysis/retry", status_code=202, dependencies=VIDEO_ONLY)
def video_retry(asset_id: str, authorization: Optional[str] = Header(None)):
    with transaction() as db:
        p = write_scope(db, authorization, "video.manage", f"video:{asset_id}")
        asset = owned_asset(db, p, asset_id, action="video.manage", lock=True)
        run = video_evidence.latest_run(db, asset)
        if run is None:
            raise evidence.DomainError(404, "NOT_FOUND", "no analysis for this clip")
        return {"run": video_evidence.run_view(video_evidence.retry(db, run))}


class AnnotationIn(BaseModel):
    label: Literal["caregiver_present", "infant_moving", "infant_still", "camera_moved", "not_sure"]
    t_start: float = Field(ge=0, le=3600)
    t_end: float = Field(gt=0, le=3600)
    expected_revision: int = Field(ge=0, le=REVISION_MAX)
    supersedes: Optional[str] = Field(default=None, min_length=1, max_length=40)


@app.post("/v1/video/assets/{asset_id}/annotations", status_code=201, dependencies=VIDEO_ONLY)
def video_annotate(asset_id: str, body: AnnotationIn, authorization: Optional[str] = Header(None),
                   idempotency_key: str = Header(min_length=8, max_length=64)):
    """What a person reports about an interval: a new evidence revision. The
    measurement is never changed."""
    with transaction() as db:
        p = write_scope(db, authorization, "video.annotate", f"video:{asset_id}")
        asset = owned_asset(db, p, asset_id, action="video.annotate", lock=True)
        household = db.get(Household, p.household_id)
        video_evidence.require_gate(db, household, db.get(Device, asset.device_id), record=False)
        result, replayed = video_evidence.annotate(db, p, household, asset, label=body.label, t_start=body.t_start,
                                                   t_end=body.t_end, expected_revision=body.expected_revision,
                                                   client_ref=idempotency_key, supersedes=body.supersedes)
        return JSONResponse(result, status_code=200 if replayed else 201)


class VideoAckIn(BaseModel):
    revision: int = Field(ge=1, le=REVISION_MAX)


@app.post("/v1/video/assets/{asset_id}/acknowledge", dependencies=VIDEO_ONLY)
def video_acknowledge(asset_id: str, body: VideoAckIn, authorization: Optional[str] = Header(None)):
    with transaction() as db:
        p = write_scope(db, authorization, "video.read", f"video:{asset_id}")
        asset = owned_asset(db, p, asset_id, action="video.read", lock=True)
        video_evidence.require_gate(db, db.get(Household, p.household_id), db.get(Device, asset.device_id),
                                    record=False)
        return video_evidence.acknowledge(db, p, asset, body.revision)


@app.delete("/v1/video/assets/{asset_id}", dependencies=VIDEO_ONLY)
def video_delete(asset_id: str, authorization: Optional[str] = Header(None)):
    """Delete the clip's bytes. Measurements stay as numbers about a deleted clip."""
    with transaction() as db:
        p = write_scope(db, authorization, "video.manage", f"video:{asset_id}")
        asset = owned_asset(db, p, asset_id, action="video.manage", lock=True)
        return video_evidence.delete(db, asset)


class VideoSampleIn(BaseModel):
    device_id: Optional[str] = Field(default=None, min_length=1, max_length=40)


@app.post("/v1/dev/video/sample", dependencies=DEV_ONLY + VIDEO_ONLY)
def dev_video_sample(body: VideoSampleIn, authorization: Optional[str] = Header(None)):
    """Scenario control: store the demo clip for a camera and queue its
    analysis. Only the fixed sample or the generated staged clip; no path or
    URL is accepted."""
    require_dev()
    with transaction() as db:
        p = write_scope(db, authorization, "dev.control", "video-sample")
        household = db.get(Household, p.household_id)
        device = camera_for(db, p, body.device_id, action="dev.control")
        source, kind, provenance = video_evidence.sample_source()
        asset, created = video_evidence.register(db, household, device, source=source, source_kind=kind,
                                                 provenance=provenance)
        run, queued = video_evidence.request_analysis(db, household, asset)
        return {"asset": video_evidence.asset_view(asset), "created": created, "run": video_evidence.run_view(run),
                "queued": queued}


@app.post("/v1/dev/video/upload", dependencies=DEV_ONLY + VIDEO_ONLY)
async def dev_video_upload(request: Request, device_id: Optional[str] = Query(default=None, max_length=40),
                           authorization: Optional[str] = Header(None)):
    """Local development import of a clip as the raw request body. Streamed
    to a private temporary file with a byte limit, inspected by content (never
    by name), stored by hash. The client's file name is not kept."""
    require_dev()
    with transaction() as db:                     # who is asking, before a single byte is written
        authorize(session_principal(db, authorization), "dev.control", "video-upload")
    limit = video_evidence.upload_limits().max_bytes
    incoming = video_evidence.media_root() / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / f"{uuid.uuid4().hex}.part"
    size = 0
    try:
        with tmp.open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise evidence.DomainError(413, "TOO_LARGE", "the file is larger than the limit", limit=limit)
                f.write(chunk)
        with transaction() as db:
            p = write_scope(db, authorization, "dev.control", "video-upload")
            household = db.get(Household, p.household_id)
            device = camera_for(db, p, device_id, action="dev.control")
            asset, created = video_evidence.register(db, household, device, source=tmp, source_kind="local_import",
                                                     provenance={"kind": "local_import",
                                                                 "caption": "A clip imported in local development."})
            return {"asset": video_evidence.asset_view(asset), "created": created}
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/v1/dev/video/worker", status_code=202, dependencies=DEV_ONLY + VIDEO_ONLY)
def dev_video_worker(db: Session = Depends(get_db), authorization: Optional[str] = Header(None)):
    """Start the real worker as a separate OS process and return at once; the
    page polls the run's state. The request never waits for the analysis."""
    require_dev()
    authorize(session_principal(db, authorization), "dev.control", "video-worker")
    db.close()
    proc = subprocess.Popen([sys.executable, "-m", "app.worker", "--once"], cwd=ROOT, env=os.environ.copy(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"started": True, "pid": proc.pid, "command": "app.worker --once"}


# ------------------------------------------------------------------ operations

@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    """Aggregate operational metrics for Prometheus/Grafana. No tenant data."""
    from fastapi import Response
    body, content_type = metrics.render()
    return Response(body, media_type=content_type)


# ------------------------------------------------------------------ UI

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "static" / "index.html")


ARCHITECTURE = ROOT.parent / "docs" / "architecture"
ARCHITECTURE_FILES = {f"{b}.{ext}": (ARCHITECTURE / ("exports" if ext in ("svg", "png") else "") / f"{b}.{ext}", media)
                      for b in ("A-backend-architecture", "B-same-symptom-different-cause", "C-demo-storyboard")
                      for ext, media in (("svg", "image/svg+xml"), ("png", "image/png"),
                                         ("excalidraw", "application/json"))}


@app.get("/architecture", include_in_schema=False)
def architecture():
    """The three Excalidraw boards, for engineers. Not part of the parent app."""
    return FileResponse(ROOT / "static" / "architecture.html")


@app.get("/architecture/{name}", include_in_schema=False)
def architecture_file(name: str):
    """One exported board or its editable source, from a fixed list (no paths)."""
    entry = ARCHITECTURE_FILES.get(name)
    if entry is None or not entry[0].is_file():
        raise evidence.DomainError(404, "NOT_FOUND", "no such board")
    headers = {"Content-Disposition": f'attachment; filename="{name}"'} if name.endswith(".excalidraw") else None
    return FileResponse(entry[0], media_type=entry[1], headers=headers)


@app.get("/privacy", include_in_schema=False)
def privacy():
    return FileResponse(ROOT / "static" / "privacy.html")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return FileResponse(ROOT / "static" / "robots.txt", media_type="text/plain")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(ROOT / "static" / "favicon.svg", media_type="image/svg+xml")


@app.exception_handler(404)
async def not_found(request: Request, exc):
    """API callers get JSON; people get a page that leads back."""
    if request.url.path.startswith("/v1/") or "application/json" in request.headers.get("accept", ""):
        detail = getattr(exc, "detail", "not found")
        return JSONResponse({"code": "NOT_FOUND", "message": str(detail)}, status_code=404)
    return FileResponse(ROOT / "static" / "404.html", status_code=404)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
