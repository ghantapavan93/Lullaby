"""Evidence ingestion, corrections and briefing revisions.

Invariant: **when evidence changes, the briefing gets a new immutable
revision; nothing that was shown before is rewritten.**

- Evidence is append-only. A correction is a new event that names the event
  it supersedes (database-enforced: at most one correction per event).
- A briefing revision is computed from the window's *effective* evidence
  (events not superseded) and stored immutably with its fact fingerprints.
- Only windows whose bounds overlap the new evidence get a new revision.
  The correction workflow is limited to one declared window.
- Every write runs under the household row lock, so concurrent corrections
  are serialized and the loser gets ``REVISION_CONFLICT``.
- Event keys are idempotent per household: a replay with the same content
  returns the original result; the same key with different content is a
  conflict and the original is kept.
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from . import briefing, intervals as iv
from .models import BriefingRevision, BriefingWindow, Child, EvidenceEvent, OutboxJob
from .security import Principal, current_trace_id, new_id

WINDOW_START = time(19, 0)
WINDOW_END = time(7, 0)
WINDOW_KINDS = ("sleep_interval", "camera_availability")


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, **detail: Any):
        self.status, self.code, self.message, self.detail = status, code, message, detail
        super().__init__(message)


@dataclass(frozen=True)
class NewEvidence:
    client_event_id: str
    kind: str
    started_at: datetime
    ended_at: datetime
    payload: dict[str, Any]


# ---------------------------------------------------------------- windows

def window_bounds(local_date: date, timezone: str) -> tuple[datetime, datetime]:
    """19:00 on the evening date to 07:00 the next morning, in the household's
    named timezone, returned as aware instants."""
    tz = ZoneInfo(timezone)
    start = datetime.combine(local_date, WINDOW_START, tzinfo=tz)
    end = datetime.combine(local_date + timedelta(days=1), WINDOW_END, tzinfo=tz)
    return iv.utc(start), iv.utc(end)


def ensure_window(db: Session, household_id: str, child_id: str, local_date: date,
                  timezone: str) -> BriefingWindow:
    start, end = window_bounds(local_date, timezone)
    db.execute(pg_insert(BriefingWindow).values(
        id=new_id("win"), household_id=household_id, child_id=child_id, local_date=local_date,
        timezone=timezone, starts_at=start, ends_at=end,
    ).on_conflict_do_nothing(constraint="uq_windows_child_date"))
    return db.execute(select(BriefingWindow).where(
        BriefingWindow.child_id == child_id, BriefingWindow.local_date == local_date
    ).with_for_update()).scalar_one()


def windows_for_interval(db: Session, household_id: str, child_id: str, start: datetime,
                         end: datetime, timezone: str) -> list[BriefingWindow]:
    tz = ZoneInfo(timezone)
    first = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date()
    out, day = [], first
    while day <= last:
        w_start, w_end = window_bounds(day, timezone)
        if start < w_end and w_start < end:
            out.append(ensure_window(db, household_id, child_id, day, timezone))
        day += timedelta(days=1)
    return out


# ---------------------------------------------------------------- evidence

def _canonical_hash(fields: dict[str, Any]) -> str:
    def norm(v: Any) -> Any:
        return v.isoformat() if isinstance(v, datetime) else v
    blob = json.dumps({k: norm(v) for k, v in fields.items()}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _validate_interval(start: datetime, end: datetime) -> None:
    for value in (start, end):
        if value.tzinfo is None or value.utcoffset() is None:
            raise DomainError(422, "AMBIGUOUS_TIMESTAMP",
                              "timestamps must include a UTC offset; local wall-clock times are ambiguous")
    if end <= start:
        raise DomainError(422, "INVALID_INTERVAL", "interval end must be after its start")


def _existing_by_key(db: Session, household_id: str, client_event_id: str) -> Optional[EvidenceEvent]:
    return db.execute(select(EvidenceEvent).where(
        EvidenceEvent.household_id == household_id,
        EvidenceEvent.client_event_id == client_event_id)).scalar_one_or_none()


def effective_evidence(db: Session, window: BriefingWindow) -> list[EvidenceEvent]:
    correction = aliased(EvidenceEvent)
    stmt = (select(EvidenceEvent)
            .where(EvidenceEvent.household_id == window.household_id,
                   EvidenceEvent.child_id == window.child_id,
                   EvidenceEvent.kind.in_(WINDOW_KINDS),
                   EvidenceEvent.started_at < window.ends_at,
                   EvidenceEvent.ended_at > window.starts_at,
                   ~exists().where(correction.supersedes_event_id == EvidenceEvent.id))
            .order_by(EvidenceEvent.started_at, EvidenceEvent.id))
    return list(db.execute(stmt).scalars())


def current_revision(db: Session, window: BriefingWindow) -> Optional[BriefingRevision]:
    if window.current_revision == 0:
        return None
    return db.execute(select(BriefingRevision).where(
        BriefingRevision.window_id == window.id,
        BriefingRevision.revision == window.current_revision)).scalar_one()


def write_revision(db: Session, window: BriefingWindow, cause: dict[str, Any]) -> BriefingRevision:
    """Compute and append the next revision. Caller holds the household lock
    and the window row lock."""
    evidence = effective_evidence(db, window)
    views = [briefing.EvidenceView(e.id, e.kind, e.source, e.started_at, e.ended_at,
                                   e.payload or {}, e.supersedes_event_id) for e in evidence]
    facts = briefing.compute_facts((window.starts_at, window.ends_at), window.timezone, views)
    number = window.current_revision + 1
    revision = BriefingRevision(
        id=new_id("rev"), household_id=window.household_id, window_id=window.id, revision=number,
        facts=facts, fingerprints=briefing.fingerprints(facts),
        evidence_ids=[e.id for e in evidence], cause=cause, trace_id=current_trace_id(),
    )
    db.add(revision)
    window.current_revision = number
    db.flush()
    return revision


def ingest(db: Session, *, household_id: str, child_id: str, timezone: str, source: str,
           actor_member_id: Optional[str], device_id: Optional[str],
           items: list[NewEvidence]) -> dict[str, Any]:
    """Append a batch of evidence and give each touched window ONE new revision.
    Caller holds the household lock."""
    if db.get(Child, child_id) is None or db.get(Child, child_id).household_id != household_id:
        raise DomainError(404, "NOT_FOUND", "child not found")
    created, replayed = [], []
    for item in items:
        _validate_interval(item.started_at, item.ended_at)
        request_hash = _canonical_hash({
            "child_id": child_id, "kind": item.kind, "source": source,
            "started_at": item.started_at, "ended_at": item.ended_at, "payload": item.payload})
        existing = _existing_by_key(db, household_id, item.client_event_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise DomainError(409, "EVENT_KEY_REUSED",
                                  "this event key was already used for different content; the original is kept",
                                  client_event_id=item.client_event_id, original_event_id=existing.id)
            replayed.append(existing)
            continue
        event = EvidenceEvent(
            id=new_id("evt"), household_id=household_id, child_id=child_id,
            client_event_id=item.client_event_id, request_hash=request_hash, kind=item.kind,
            source=source, actor_member_id=actor_member_id, device_id=device_id,
            started_at=item.started_at, ended_at=item.ended_at, payload=item.payload,
            trace_id=current_trace_id())
        db.add(event)
        created.append(event)
    db.flush()

    touched: dict[str, BriefingWindow] = {}
    for event in created:
        if event.kind in WINDOW_KINDS:
            for window in windows_for_interval(db, household_id, child_id, event.started_at,
                                               event.ended_at, timezone):
                touched[window.id] = window
    revisions = [write_revision(db, w, {"kind": "evidence", "event_ids": [e.id for e in created],
                                        "source": source, "member_id": actor_member_id})
                 for w in sorted(touched.values(), key=lambda w: w.local_date)]
    return {"created": [e.id for e in created], "replayed": [e.id for e in replayed],
            "revisions": [{"window_id": r.window_id, "revision": r.revision, "revision_id": r.id}
                          for r in revisions]}


# ---------------------------------------------------------------- corrections

def diff(before: Optional[BriefingRevision], after: BriefingRevision) -> list[dict[str, Any]]:
    out = []
    for key, label in briefing.ACK_FACTS:
        old = before.fingerprints.get(key) if before else None
        out.append({"fact": key, "label": label, "changed": old != after.fingerprints[key],
                    "before": before.facts[key] if before else None, "after": after.facts[key]})
    return out


def apply_correction(db: Session, principal: Principal, window: BriefingWindow, *,
                     target_event_id: str, corrected_start: Optional[datetime],
                     corrected_end: Optional[datetime], expected_revision: int,
                     client_event_id: str, reason: str) -> dict[str, Any]:
    """Caller holds the household lock and has authorized evidence.correct and
    loaded `window` scoped to the caller's household (locked)."""
    target = db.execute(select(EvidenceEvent).where(
        EvidenceEvent.id == target_event_id,
        EvidenceEvent.household_id == principal.household_id)).scalar_one_or_none()
    if target is None:
        raise DomainError(404, "NOT_FOUND", "evidence event not found")

    start = corrected_start or target.started_at
    end = corrected_end or target.ended_at
    _validate_interval(start, end)
    source = "PARENT" if principal.role in ("owner", "admin") else "CAREGIVER"
    # The camera record this correction stands on. Only inside it is the parent's
    # interpretation a selection; outside it the claim is weighed like any report.
    camera_basis = ([[target.started_at.isoformat(), target.ended_at.isoformat()]] if target.source == "CAMERA"
                    else (target.payload or {}).get("camera_basis", []))
    payload = {"reason": reason[:200], "corrects": target.id, "camera_basis": camera_basis,
               "original": {"started_at": target.started_at.isoformat(),
                            "ended_at": target.ended_at.isoformat(), "source": target.source}}
    request_hash = _canonical_hash({
        "child_id": target.child_id, "kind": target.kind, "source": source, "started_at": start,
        "ended_at": end, "supersedes": target.id, "window_id": window.id,
        "expected_revision": expected_revision})

    # 1. Replay or key reuse, before any freshness check: a retry of a committed
    #    correction must return the original result even though the revision moved.
    existing = _existing_by_key(db, principal.household_id, client_event_id)
    if existing is not None:
        if existing.request_hash != request_hash:
            raise DomainError(409, "EVENT_KEY_REUSED",
                              "this correction key was already used for a different correction",
                              original_event_id=existing.id)
        made = db.execute(select(BriefingRevision).where(
            BriefingRevision.window_id == window.id,
            BriefingRevision.cause["event_ids"].contains([existing.id]))).scalar_one()
        before = db.execute(select(BriefingRevision).where(
            BriefingRevision.window_id == window.id,
            BriefingRevision.revision == made.revision - 1)).scalar_one_or_none()
        return _correction_result(existing, target, before, made, replayed=True)

    # 2. Freshness and scope.
    if window.current_revision != expected_revision:
        raise DomainError(409, "REVISION_CONFLICT",
                          "the briefing changed since this screen loaded; refresh before correcting",
                          expected_revision=expected_revision, current_revision=window.current_revision)
    if target.kind != "sleep_interval" or target.child_id != window.child_id:
        raise DomainError(422, "NOT_CORRECTABLE", "only this window's sleep records can be corrected")
    if not (target.started_at < window.ends_at and target.ended_at > window.starts_at):
        raise DomainError(422, "OUTSIDE_WINDOW", "the target record is not part of this briefing window")
    if target.started_at < window.starts_at or target.ended_at > window.ends_at:
        # Superseding it would silently change another window's evidence.
        raise DomainError(422, "TARGET_SPANS_WINDOWS",
                          "this record extends beyond this briefing window and can't be corrected here")
    if start < window.starts_at or end > window.ends_at:
        raise DomainError(422, "OUTSIDE_WINDOW",
                          "a correction must stay inside its briefing window; cross-window moves are not supported")
    superseded = db.execute(select(EvidenceEvent.id).where(
        EvidenceEvent.supersedes_event_id == target.id)).scalar_one_or_none()
    if superseded is not None:
        raise DomainError(409, "ALREADY_CORRECTED",
                          "this record was already corrected; correct the newer record instead",
                          superseded_by=superseded)

    before = current_revision(db, window)
    correction = EvidenceEvent(
        id=new_id("evt"), household_id=principal.household_id, child_id=target.child_id,
        client_event_id=client_event_id, request_hash=request_hash, kind=target.kind,
        source=source, actor_member_id=principal.member_id, device_id=None,
        started_at=start, ended_at=end, payload=payload, supersedes_event_id=target.id,
        trace_id=current_trace_id())
    db.add(correction)
    db.flush()
    after = write_revision(db, window, {"kind": "correction", "event_ids": [correction.id],
                                        "target_event_id": target.id,
                                        "member_id": principal.member_id})
    # Notification work commits atomically with the revision it announces.
    db.add(OutboxJob(household_id=principal.household_id, kind="notify_revision",
                     dedupe_key=f"notify:{window.id}:{after.revision}",
                     payload={"window_id": window.id, "revision": after.revision,
                              "revision_id": after.id, "by_member_id": principal.member_id},
                     trace_id=current_trace_id()))
    db.flush()
    return _correction_result(correction, target, before, after, replayed=False)


def _correction_result(correction: EvidenceEvent, target: EvidenceEvent,
                       before: Optional[BriefingRevision], after: BriefingRevision,
                       replayed: bool) -> dict[str, Any]:
    return {
        "replayed": replayed,
        "correction_event_id": correction.id,
        "original_event": {"id": target.id, "retained": True, "source": target.source,
                           "started_at": target.started_at.isoformat(),
                           "ended_at": target.ended_at.isoformat()},
        "previous_revision": before.revision if before else None,
        "revision": after.revision,
        "revision_id": after.id,
        "changes": diff(before, after),
    }


# ---------------------------------------------------------------- the week

def week_view(db: Session, window: BriefingWindow, nights: int = 7) -> dict[str, Any]:
    """The last `nights` windows ending at `window`, each read from its CURRENT
    revision at request time.

    Nanit's support article says sleep values "may not recalculate for days
    that have already passed". This view makes the opposite trade: nothing
    multi-night is stored, so a correction to any past night is reflected the
    next time anyone looks, and corrected nights are marked. Nights without an
    exact total (contested evidence, no records) are excluded from the average
    and listed with the reason, so the denominator is always visible.
    """
    first = window.local_date - timedelta(days=nights - 1)
    windows = list(db.execute(select(BriefingWindow).where(
        BriefingWindow.household_id == window.household_id, BriefingWindow.child_id == window.child_id,
        BriefingWindow.local_date >= first, BriefingWindow.local_date <= window.local_date)
        .order_by(BriefingWindow.local_date)).scalars())
    corrected = set(db.execute(select(BriefingRevision.window_id).where(
        BriefingRevision.window_id.in_([w.id for w in windows]),
        BriefingRevision.cause["kind"].astext == "correction")).scalars())
    rows = []
    for w in windows:
        latest = current_revision(db, w)
        if latest is None:
            continue
        sleep = latest.facts["recorded_sleep"]
        reason = None
        if sleep["minutes"] is None:
            reason = "sources disagree" if sleep["state"] == "contested" else "no sleep records"
        rows.append({"local_date": w.local_date.isoformat(), "window_id": w.id, "revision": latest.revision,
                     "minutes": sleep["minutes"], "excluded_reason": reason, "corrected": w.id in corrected,
                     "coverage_pct": latest.facts["observation_coverage"]["pct"], "status": latest.facts["status"]})
    counted = [r["minutes"] for r in rows if r["minutes"] is not None]
    return {"nights": rows, "counted": len(counted), "of": len(rows),
            "average_minutes": round(sum(counted) / len(counted), 1) if counted else None,
            "computed_from": "each night's current revision, at read time"}
