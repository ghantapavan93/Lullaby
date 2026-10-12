"""Red-team reproductions: evidence math, corrections, fingerprints, acknowledgments.

Every test here FAILS on the reviewed code and should pass once the defect is fixed.
"""
from datetime import date

from app import briefing, engine, seed
from app.db import SessionLocal, transaction
from app.seed import at, sleep

from ..conftest import CAREGIVER, PARENT, ROOT
from ._helpers import ALL_FACTS, ack, brief, correct, event_id, member_facts, window_for


# F-03 -----------------------------------------------------------------------------------------
def test_correction_cannot_silently_overrule_camera_observation(client, demo):
    """The camera observed 23:00-23:30 (usable) and recorded no sleep. A parent
    'correction' that stretches the 19:00-23:00 record to 23:30 is the same claim
    that briefing.py marks CONTESTED when it arrives as a report; as a correction it
    becomes an exact total. Spec §8: unrelated source disagreements stay unresolved;
    supersession does not establish truth."""
    w = demo["window_id"]
    r = correct(client, w, target=event_id("cam-1009-sleep-1"), expected=4,
                end="2026-10-09T23:30:00-04:00")
    if r.status_code != 200:
        return  # rejecting the extension is an acceptable repair
    sleep_fact = brief(client, w)["facts"]["recorded_sleep"]
    assert sleep_fact["minutes"] is None and sleep_fact["contested_minutes"] == 30, sleep_fact


# F-04 -----------------------------------------------------------------------------------------
def test_unobserved_time_copy_matches_correction_behaviour(client, demo):
    """The UI promises 'Time the camera didn't observe is never counted as sleep.'
    A correction of 23:30-03:40 to end at 04:20 puts the 40 unobserved minutes into
    an exact recorded-sleep total while the screen still says that sentence."""
    w = demo["window_id"]
    r = correct(client, w, target=event_id("cam-1009-sleep-2"), expected=4,
                end="2026-10-10T04:20:00-04:00")
    assert r.status_code == 200, r.text
    f = brief(client, w)["facts"]
    counted_unobserved = f["recorded_sleep"]["state"] == "recorded" and f["recorded_sleep"]["minutes"] == 680
    copy = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    promises_never = "never counted as sleep" in copy
    assert not (counted_unobserved and promises_never), \
        "unobserved minutes are counted as exact sleep while the UI says they never are"


# F-07 -----------------------------------------------------------------------------------------
def test_correction_never_changes_another_windows_evidence(client, demo):
    """A record spanning two windows (06:00-20:00 on Oct 10) is corrected inside the
    Oct 9 window. The correction supersedes the whole record, so the Oct 10 window
    silently loses 19:00-20:00 without a new revision: its stored revision no longer
    matches its own effective evidence."""
    with transaction() as db:
        seed.camera_sync(db, seed.HOUSEHOLD, seed.CHILD, seed.CAMERA,
                         [sleep("cam-span-two-windows", at(10, "06:00"), at(10, "20:00"))])
    w9 = demo["window_id"]
    expected = brief(client, w9)["revision"]
    r = correct(client, w9, target=event_id("cam-span-two-windows"), expected=expected,
                end="2026-10-10T06:30:00-04:00")
    if r.status_code != 200:
        return  # rejecting a target that extends past this window is an acceptable repair
    w10 = window_for(date(2026, 10, 10))
    with SessionLocal() as db:
        stored = engine.current_revision(db, db.get(type(w10), w10.id))
        evidence = engine.effective_evidence(db, w10)
        views = [briefing.EvidenceView(e.id, e.kind, e.source, e.started_at, e.ended_at, e.payload or {},
                                       e.supersedes_event_id) for e in evidence]
        recomputed = briefing.compute_facts((w10.starts_at, w10.ends_at), w10.timezone, views)
    assert recomputed["recorded_sleep"] == stored.facts["recorded_sleep"], \
        "a correction in the Oct 9 window changed the Oct 10 window's evidence without a revision"


# F-08 -----------------------------------------------------------------------------------------
def test_moving_recorded_sleep_is_a_reviewable_change(client, demo):
    """Correcting a correction can move a sleep block (23:20-03:30 -> 23:10-03:20)
    while total, sources, onset, wake, coverage and unobserved stay equal. Every
    fingerprint is identical, so the caregiver's stale acknowledgment reports
    'Nothing you saw has changed' although the night's timeline did."""
    w = demo["window_id"]
    first = correct(client, w, target=event_id("cam-1009-sleep-2"), expected=4,
                    start="2026-10-09T23:20:00-04:00", end="2026-10-10T03:30:00-04:00")
    assert first.status_code == 200, first.text
    rev5 = brief(client, w)
    assert ack(client, w, rev5["revision_id"], CAREGIVER).status_code == 200
    second = correct(client, w, target=first.json()["correction_event_id"], expected=5,
                     start="2026-10-09T23:10:00-04:00", end="2026-10-10T03:20:00-04:00")
    assert second.status_code == 200, second.text
    rev6 = brief(client, w)
    assert rev6["facts"]["recorded_sleep"]["intervals"] != rev5["facts"]["recorded_sleep"]["intervals"]
    member, _ = member_facts(client, w, seed.CAREGIVER_B)
    assert member["needs_review"], "the sleep timeline moved but no acknowledged fact is marked changed"


# F-06 -----------------------------------------------------------------------------------------
def test_second_acknowledgment_of_same_revision_records_its_facts(client, demo):
    """Caregiver acknowledges revision 4 for one fact, then acknowledges revision 4
    for all five. The second request is told 'You acknowledged the current version.'
    but ON CONFLICT DO NOTHING dropped it: four facts stay unacknowledged."""
    w = demo["window_id"]
    rid = brief(client, w)["revision_id"]
    assert ack(client, w, rid, CAREGIVER, facts=["observation_coverage"]).status_code == 200
    r = ack(client, w, rid, CAREGIVER, facts=ALL_FACTS)
    assert r.status_code in (200, 409), r.text
    if r.status_code == 409:
        return  # an explicit refusal is an honest repair
    assert r.json()["message"] == "You acknowledged the current version."
    _, states = member_facts(client, w, seed.CAREGIVER_B)
    assert states == {k: "acknowledged" for k in ALL_FACTS}, states


# F-12 -----------------------------------------------------------------------------------------
def test_changes_against_a_missing_revision_is_not_found(client, demo):
    """since_revision=99 does not exist; the endpoint answers 200 and reports every
    fact as changed from 'nothing', which a client would show as five changes."""
    r = client.get(f"/v1/windows/{demo['window_id']}/changes?since_revision=99", headers=PARENT)
    assert r.status_code == 404, r.json()
