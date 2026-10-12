"""Corrections and acknowledgments through the API, on PostgreSQL.

Invariants
- A correction appends evidence, never rewrites it, and yields exactly one new
  revision of exactly one window.
- A replayed correction returns the original result; a reused key with new
  content is rejected; a stale screen gets REVISION_CONFLICT.
- An acknowledgment binds to the displayed revision only, never to a newer
  one, and an older acknowledgment arriving late cannot replace a newer one.
"""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import seed
from app.db import SessionLocal, engine, transaction
from app.models import BriefingRevision, EvidenceEvent, Member, OutboxJob

from .conftest import CAREGIVER, PARENT

CORRECTED_END = "2026-10-10T06:35:00-04:00"


def brief(client, demo, headers=PARENT, revision=None):
    q = f"?revision={revision}" if revision else ""
    r = client.get(f"/v1/windows/{demo['window_id']}/brief{q}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def correct(client, demo, expected=4, key=None, end=CORRECTED_END, headers=PARENT, target=None):
    return client.post(f"/v1/windows/{demo['window_id']}/corrections", headers=headers, json={
        "target_event_id": target or demo["final_wake_event_id"], "corrected_end": end,
        "expected_revision": expected, "client_event_id": key or f"corr-{uuid.uuid4().hex}"})


def ack(client, demo, revision_id, headers=CAREGIVER, facts=None):
    return client.post(f"/v1/windows/{demo['window_id']}/acknowledgments", headers=headers, json={
        "revision_id": revision_id,
        "displayed_facts": facts or ["recorded_sleep", "sleep_onset", "final_wake",
                                     "observation_coverage", "unobserved"]})


# ---------------------------------------------------------------- corrections

def test_canonical_correction(client, demo):
    before = brief(client, demo)
    assert before["revision"] == 4
    assert before["facts"]["recorded_sleep"]["minutes"] == 640
    r = correct(client, demo)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["previous_revision"], body["revision"]) == (4, 5)
    changed = {c["fact"] for c in body["changes"] if c["changed"]}
    assert changed == {"recorded_sleep", "final_wake"}
    after = brief(client, demo)
    assert after["facts"]["recorded_sleep"]["minutes"] == 625
    assert after["facts"]["observation_coverage"]["pct"] == 94.4
    assert after["facts"]["unobserved"]["minutes"] == 40      # the gap stays unknown
    assert after["facts"]["final_wake"]["sources"] == ["PARENT"]


def test_original_evidence_is_retained_and_database_refuses_rewrites(client, demo):
    correct(client, demo)
    ack(client, demo, brief(client, demo)["revision_id"])
    with SessionLocal() as db:
        original = db.get(EvidenceEvent, demo["final_wake_event_id"])
        assert original.ended_at == datetime(2026, 10, 10, 6, 50, tzinfo=ZoneInfo("America/New_York"))
    statements = ["UPDATE evidence_events SET ended_at = now() WHERE id = :id",
                  "DELETE FROM evidence_events WHERE id = :id",
                  "UPDATE briefing_revisions SET facts = '{}'::jsonb WHERE window_id IS NOT NULL AND :id = :id",
                  "DELETE FROM acknowledgments WHERE :id = :id"]
    for sql in statements:
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(sql), {"id": original.id})


def test_replayed_correction_creates_no_new_revision(client, demo):
    first = correct(client, demo, key="corr-replay-0001").json()
    again = correct(client, demo, key="corr-replay-0001")
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["revision"] == first["revision"] == 5
    assert brief(client, demo)["current_revision"] == 5


def test_reused_correction_key_with_different_content_conflicts(client, demo):
    correct(client, demo, key="corr-reuse-00001")
    r = correct(client, demo, key="corr-reuse-00001", expected=4, end="2026-10-10T06:40:00-04:00")
    assert r.status_code == 409 and r.json()["code"] == "EVENT_KEY_REUSED"
    assert brief(client, demo)["facts"]["recorded_sleep"]["minutes"] == 625  # first one stands


def test_stale_screen_correction_gets_revision_conflict(client, demo):
    assert correct(client, demo).status_code == 200
    r = correct(client, demo, expected=4, target=None)
    assert r.status_code == 409 and r.json()["code"] == "REVISION_CONFLICT"
    assert r.json()["detail"]["current_revision"] == 5


def test_simultaneous_corrections_one_wins_one_conflicts(client, demo):
    gate = Barrier(4)

    def attempt(i):
        gate.wait(timeout=10)
        return correct(client, demo, end=f"2026-10-10T06:{30 + i:02d}:00-04:00")

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(attempt, range(4)))
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409, 409, 409], [r.text for r in results]
    with SessionLocal() as db:
        revisions = db.execute(select(BriefingRevision.revision).where(
            BriefingRevision.window_id == demo["window_id"])).scalars().all()
        assert sorted(revisions) == [1, 2, 3, 4, 5]
        corrections = db.execute(select(EvidenceEvent).where(
            EvidenceEvent.supersedes_event_id == demo["final_wake_event_id"])).scalars().all()
        assert len(corrections) == 1


def test_correction_does_not_touch_other_windows(client, demo):
    me = client.get("/v1/me", headers=PARENT).json()
    control = next(w for w in me["windows"] if w["local_date"] == "2026-10-08")
    before = client.get(f"/v1/windows/{control['window_id']}/revisions", headers=PARENT).json()
    correct(client, demo)
    after = client.get(f"/v1/windows/{control['window_id']}/revisions", headers=PARENT).json()
    assert before == after and len(after) == 1


def test_correction_cannot_leave_its_window(client, demo):
    r = correct(client, demo, end="2026-10-10T07:30:00-04:00")
    assert r.status_code == 422 and r.json()["code"] == "OUTSIDE_WINDOW"


def test_naive_timestamps_are_rejected(client, demo):
    r = correct(client, demo, end="2026-10-10T06:35:00")
    assert r.status_code == 422 and r.json()["code"] == "AMBIGUOUS_TIMESTAMP"


def test_caregiver_cannot_correct(client, demo):
    r = correct(client, demo, headers=CAREGIVER)
    assert r.status_code == 403


def test_revision_and_notification_work_commit_together(client, demo):
    body = correct(client, demo).json()
    with SessionLocal() as db:
        job = db.execute(select(OutboxJob).where(OutboxJob.kind == "notify_revision")).scalar_one()
        assert job.payload["revision"] == body["revision"]
    # A rejected correction leaves neither a revision nor a job behind.
    assert correct(client, demo, expected=4).status_code == 409
    with SessionLocal() as db:
        assert len(db.execute(select(OutboxJob)).scalars().all()) == 1
        assert db.execute(select(EvidenceEvent).where(
            EvidenceEvent.supersedes_event_id.is_not(None))).scalars().all().__len__() == 1


# ---------------------------------------------------------------- acknowledgments

def test_stale_screen_acknowledgment_binds_to_displayed_revision(client, demo):
    screen = brief(client, demo, headers=CAREGIVER)          # B's screen shows revision 4
    correct(client, demo)                                      # A creates revision 5
    r = ack(client, demo, screen["revision_id"])               # B clicks on the old screen
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["acknowledged_revision"] == 4 and body["current_revision"] == 5
    assert body["newer_available"] is True
    assert sorted(body["changed_since"]) == ["final_wake", "recorded_sleep"]
    assert body["message"] == "You acknowledged the earlier version. Two details have changed since then."
    status = client.get(f"/v1/windows/{demo['window_id']}/changes", headers=PARENT).json()
    b = next(m for m in status["members"] if m["member_id"] == seed.CAREGIVER_B)
    assert b["acknowledged_revision"] == 4 and b["has_acknowledged_current"] is False
    states = {f["fact"]: f["state"] for f in b["facts"]}
    assert states == {"recorded_sleep": "changed_since_acknowledgment", "sleep_onset": "acknowledged",
                      "final_wake": "changed_since_acknowledgment", "observation_coverage": "acknowledged",
                      "unobserved": "acknowledged"}


def test_acknowledgment_requires_the_displayed_revision(client, demo):
    r = client.post(f"/v1/windows/{demo['window_id']}/acknowledgments", headers=CAREGIVER,
                    json={"displayed_facts": ["recorded_sleep"]})
    assert r.status_code == 422
    r = ack(client, demo, brief(client, demo)["revision_id"], facts=["made_up_fact"])
    assert r.status_code == 422 and r.json()["code"] == "INVALID_FACTS"


def test_late_old_acknowledgment_cannot_replace_a_newer_one(client, demo):
    old = brief(client, demo, headers=CAREGIVER)
    correct(client, demo)
    new = brief(client, demo, headers=CAREGIVER)
    assert ack(client, demo, new["revision_id"]).json()["acknowledged_revision"] == 5
    late = ack(client, demo, old["revision_id"]).json()      # delayed click from the old screen
    assert late["acknowledged_revision"] == 4                 # recorded as what it was
    status = client.get(f"/v1/windows/{demo['window_id']}/changes", headers=PARENT).json()
    b = next(m for m in status["members"] if m["member_id"] == seed.CAREGIVER_B)
    assert b["acknowledged_revision"] == 5 and b["needs_review"] == []


def test_acknowledgment_is_idempotent(client, demo):
    rid = brief(client, demo)["revision_id"]
    first, second = ack(client, demo, rid).json(), ack(client, demo, rid).json()
    assert first["acknowledgment_id"] == second["acknowledgment_id"] and second["created"] is False


def test_partial_acknowledgment_only_covers_displayed_facts(client, demo):
    rid = brief(client, demo)["revision_id"]
    ack(client, demo, rid, facts=["recorded_sleep"])
    status = client.get(f"/v1/windows/{demo['window_id']}/changes", headers=PARENT).json()
    b = next(m for m in status["members"] if m["member_id"] == seed.CAREGIVER_B)
    states = {f["fact"]: f["state"] for f in b["facts"]}
    assert states["recorded_sleep"] == "acknowledged" and states["final_wake"] == "not_acknowledged"


def test_revoked_caregiver_loses_access_with_the_same_token(client, demo):
    assert client.get(f"/v1/windows/{demo['window_id']}/brief", headers=CAREGIVER).status_code == 200
    with transaction() as db:
        db.execute(text("UPDATE members SET revoked_at = now() WHERE id = :id"), {"id": seed.CAREGIVER_B})
    for r in (client.get(f"/v1/windows/{demo['window_id']}/brief", headers=CAREGIVER),
              ack(client, demo, "rev_any")):
        assert r.status_code == 403 and r.json()["code"] == "ACCESS_REVOKED"


def test_notification_reaches_the_other_caregiver_once(client, demo):
    from app import worker
    correct(client, demo)
    worker.run_once("test")
    worker.run_once("test")
    me = client.get("/v1/me", headers=CAREGIVER).json()
    assert [n["revision"] for n in me["notifications"]] == [5]
    assert client.get("/v1/me", headers=PARENT).json()["notifications"] == []
