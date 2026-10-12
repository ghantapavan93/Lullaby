"""Acknowledgments bound to the revision a caregiver actually saw.

Invariant: **an acknowledgment covers exactly the revision and facts that
were displayed, and nothing newer.**

- The request must name the displayed revision id; the server never picks
  "the latest" on the caller's behalf.
- The acknowledgment stores the semantic fingerprints of the acknowledged
  facts from that revision. Comparing fingerprints (value + provenance +
  uncertainty), not rendered text, decides what "changed" means. A number
  that stays the same while its source changes still needs another look.
- Per fact, the standing acknowledgment is the one with the highest revision
  number, not the most recent arrival: a delayed click from an old screen can
  never replace a newer acknowledgment. ``ack_heads`` enforces the same rule
  in the database with a conditional upsert.
- An acknowledgment records an action on specified information. It does not
  establish that anyone understood it or took responsibility.
"""
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import briefing
from .engine import DomainError, current_revision
from .models import Acknowledgment, AckHead, BriefingRevision, BriefingWindow, Member
from .security import Principal, current_trace_id, new_id

FACT_KEYS = [key for key, _ in briefing.ACK_FACTS]
LABELS = dict(briefing.ACK_FACTS)


def acknowledge(db: Session, principal: Principal, window: BriefingWindow, *,
                revision_id: str, displayed_facts: list[str]) -> dict[str, Any]:
    """Caller holds the household lock, rechecked membership, authorized
    briefing.acknowledge and loaded `window` within the household."""
    unknown = [f for f in displayed_facts if f not in FACT_KEYS]
    if not displayed_facts or unknown:
        raise DomainError(422, "INVALID_FACTS", "displayed_facts must name acknowledgeable facts",
                          unknown=unknown, allowed=FACT_KEYS)
    shown = db.execute(select(BriefingRevision).where(
        BriefingRevision.id == revision_id,
        BriefingRevision.household_id == principal.household_id,
        BriefingRevision.window_id == window.id)).scalar_one_or_none()
    if shown is None:
        raise DomainError(404, "NOT_FOUND", "that revision does not exist in this briefing")

    fingerprints = {f: shown.fingerprints[f] for f in sorted(set(displayed_facts))}
    inserted = db.execute(pg_insert(Acknowledgment).values(
        id=new_id("ack"), household_id=principal.household_id, window_id=window.id,
        revision_id=shown.id, revision=shown.revision, member_id=principal.member_id,
        fingerprints=fingerprints, trace_id=current_trace_id(),
    ).on_conflict_do_nothing(constraint="uq_ack_member_revision").returning(Acknowledgment.id)
    ).scalar_one_or_none()
    ack = db.execute(select(Acknowledgment).where(
        Acknowledgment.member_id == principal.member_id,
        Acknowledgment.revision_id == shown.id)).scalar_one()
    if inserted is None and ack.fingerprints != fingerprints:
        # Acknowledgments are append-only and one per member and revision. Saying
        # "acknowledged" for facts that were not recorded would be a lie.
        raise DomainError(409, "ACK_FACTS_DIFFER",
                          f"You already acknowledged revision {shown.revision} for a different set of details.",
                          acknowledged_facts=sorted(ack.fingerprints))

    # Monotonic head: only ever moves to a higher revision.
    upsert = pg_insert(AckHead).values(
        member_id=principal.member_id, window_id=window.id, household_id=principal.household_id,
        revision=shown.revision, acknowledgment_id=ack.id)
    db.execute(upsert.on_conflict_do_update(
        index_elements=[AckHead.member_id, AckHead.window_id],
        set_={"revision": upsert.excluded.revision,
              "acknowledgment_id": upsert.excluded.acknowledgment_id,
              "updated_at": upsert.excluded.updated_at},
        where=AckHead.revision < upsert.excluded.revision))
    db.flush()

    latest = current_revision(db, window)
    changed = [f for f in fingerprints if latest.fingerprints[f] != fingerprints[f]]
    return {
        "acknowledgment_id": ack.id,
        "created": inserted is not None,
        "acknowledged_revision": shown.revision,
        "acknowledged_revision_id": shown.id,
        "current_revision": latest.revision,
        "newer_available": latest.revision > shown.revision,
        "changed_since": changed,
        "message": _message(shown.revision, latest.revision, changed),
    }


def _message(acked: int, current: int, changed: list[str]) -> str:
    if current == acked:
        return "You acknowledged the current version."
    if not changed:
        return "You acknowledged an earlier version. Nothing you saw has changed since then."
    n = len(changed)
    words = {1: "One detail has", 2: "Two details have", 3: "Three details have",
             4: "Four details have", 5: "Five details have"}
    return f"You acknowledged the earlier version. {words.get(n, f'{n} details have')} changed since then."


def member_status(db: Session, window: BriefingWindow, member: Member) -> dict[str, Any]:
    latest = current_revision(db, window)
    acks = list(db.execute(select(Acknowledgment).where(
        Acknowledgment.window_id == window.id, Acknowledgment.member_id == member.id)
        .order_by(Acknowledgment.revision.desc())).scalars())
    head = db.get(AckHead, (member.id, window.id))
    facts = []
    for key in FACT_KEYS:
        standing = next((a for a in acks if key in a.fingerprints), None)
        if standing is None:
            state = "not_acknowledged"
        elif latest is not None and standing.fingerprints[key] == latest.fingerprints[key]:
            state = "acknowledged"
        else:
            state = "changed_since_acknowledgment"
        facts.append({"fact": key, "label": LABELS[key], "state": state,
                      "acknowledged_revision": standing.revision if standing else None})
    changed = [f["fact"] for f in facts if f["state"] == "changed_since_acknowledgment"]
    return {
        "member_id": member.id, "display_name": member.display_name, "role": member.role,
        "acknowledged_revision": head.revision if head else None,
        "current_revision": latest.revision if latest else None,
        "has_acknowledged_current": bool(head and latest and head.revision == latest.revision),
        "needs_review": changed,
        "facts": facts,
    }


def window_status(db: Session, window: BriefingWindow) -> list[dict[str, Any]]:
    members = db.execute(select(Member).where(Member.household_id == window.household_id,
                                              Member.revoked_at.is_(None))
                         .order_by(Member.created_at, Member.id)).scalars()
    return [member_status(db, window, m) for m in members]
