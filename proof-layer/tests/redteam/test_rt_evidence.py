"""Red team: evidence and calculations (I5, I6, I7, I20, fingerprints).

Each test tries to break one advertised guarantee. Passing tests are kept as
regressions; strict xfails are reproduced failures (see artifacts/redteam/).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import briefing, engine, seed
from app.briefing import EvidenceView
from app.db import SessionLocal
from app.engine import NewEvidence
from app.models import EvidenceEvent

from ..conftest import CAREGIVER, PARENT
from .conftest import (ack, brief, correct, event_id, evidence_count, ingest, member_facts, window_id_for,
                       window_revisions)

NY = ZoneInfo("America/New_York")


def at(y, m, d, hh, mm, tz=NY):
    return datetime(y, m, d, hh, mm, tzinfo=tz)


def ev(i, kind, source, start, end, **payload):
    return EvidenceView(f"e{i}", kind, source, start, end, payload)


def facts_for(local_date: str, tz: str, evidence: list) -> dict:
    d = datetime.fromisoformat(local_date).date()
    return briefing.compute_facts(engine.window_bounds(d, tz), tz, evidence)


# ---------------------------------------------------------------- coverage (I5)

def test_rt_no_observation_evidence_is_unknown_not_full_not_zero(client, demo):
    """Parent-only night through the real engine and API: coverage must be unknown."""
    ingest([NewEvidence("rt-parent-only", "sleep_interval", at(2026, 10, 20, 19, 30), at(2026, 10, 21, 6, 30),
                        {"synthetic": True})], source="PARENT")
    b = brief(client, window_id_for("2026-10-20"))
    cov = b["facts"]["observation_coverage"]
    assert cov["state"] == "unknown" and cov["pct"] is None and cov["observed_minutes"] is None
    assert b["facts"]["unobserved"]["state"] == "unknown"
    assert b["facts"]["status"] == "unknown_observation"


def test_rt_parent_reports_never_create_camera_observation(client, demo):
    """A parent-sourced camera_availability record (forged source) and parent sleep
    must not add coverage, alone or next to real camera observation."""
    s, e = at(2026, 10, 22, 19, 0), at(2026, 10, 23, 7, 0)
    forged = [ev(1, "camera_availability", "PARENT", s, e, usable=True),
              ev(2, "camera_availability", "CAREGIVER", s, e, usable=True),
              ev(3, "sleep_interval", "PARENT", s, e)]
    f = facts_for("2026-10-22", seed.TZ, forged)
    assert f["observation_coverage"]["pct"] is None and f["status"] == "unknown_observation"

    real = [ev(9, "camera_availability", "CAMERA", s, at(2026, 10, 22, 23, 0), usable=True)]
    assert facts_for("2026-10-22", seed.TZ, real + forged)["observation_coverage"] == \
        facts_for("2026-10-22", seed.TZ, real)["observation_coverage"]

    # The same attack through the engine and the API.
    ingest([NewEvidence("rt-forged-avail", "camera_availability", s, e, {"usable": True})], source="PARENT")
    cov = brief(client, window_id_for("2026-10-22"))["facts"]["observation_coverage"]
    assert cov["state"] == "unknown" and cov["pct"] is None


# ---------------------------------------------------------------- overlap and duplicates (I6)

def test_rt_overlapping_camera_records_do_not_inflate_duration(client, demo):
    ingest([NewEvidence("rt-ov-obs", "camera_availability", at(2026, 10, 24, 19, 0), at(2026, 10, 25, 7, 0),
                        {"usable": True}),
            NewEvidence("rt-ov-a", "sleep_interval", at(2026, 10, 24, 20, 0), at(2026, 10, 24, 23, 0), {}),
            NewEvidence("rt-ov-b", "sleep_interval", at(2026, 10, 24, 22, 0), at(2026, 10, 25, 1, 0), {}),
            NewEvidence("rt-ov-c", "sleep_interval", at(2026, 10, 24, 21, 0), at(2026, 10, 24, 21, 30), {})])
    sleep = brief(client, window_id_for("2026-10-24"))["facts"]["recorded_sleep"]
    assert sleep["minutes"] == 300.0          # 20:00-01:00, not 180 + 180 + 30


def test_rt_duplicate_records_do_not_double_count_minutes(client, demo):
    before = brief(client, demo["window_id"])["facts"]["recorded_sleep"]["minutes"]
    # Same key replayed: no new event, no new revision.
    rev_before = window_revisions()[demo["window_id"]]
    replay = ingest([NewEvidence("cam-1009-sleep-3", "sleep_interval", seed.at(10, "04:20"),
                                 seed.at(10, "06:50"), {"synthetic": True})])
    assert replay["created"] == [] and window_revisions()[demo["window_id"]] == rev_before
    # Same content under a new key (a re-delivered camera sync): minutes unchanged.
    ingest([NewEvidence("rt-dup-sleep-3", "sleep_interval", seed.at(10, "04:20"), seed.at(10, "06:50"),
                        {"synthetic": True})])
    after = brief(client, demo["window_id"])
    assert after["revision"] == rev_before + 1
    assert after["facts"]["recorded_sleep"]["minutes"] == before == 640


# RT-7 (fixed): a duplicate record used to change the recorded_sleep fingerprint.
def test_rt_duplicate_record_does_not_require_renewed_review(client, demo):
    rev4 = brief(client, demo["window_id"])
    assert ack(client, demo["window_id"], rev4["revision_id"], CAREGIVER).status_code == 200
    ingest([NewEvidence("rt-dup-sleep-3b", "sleep_interval", seed.at(10, "04:20"), seed.at(10, "06:50"),
                        {"synthetic": True})])
    rev5 = brief(client, demo["window_id"])
    assert rev5["facts"]["recorded_sleep"]["minutes"] == rev4["facts"]["recorded_sleep"]["minutes"]
    assert rev5["facts"]["recorded_sleep"]["sources"] == rev4["facts"]["recorded_sleep"]["sources"]
    # Nothing a person could see changed in value, provenance or uncertainty ...
    assert rev5["fingerprints"]["recorded_sleep"] == rev4["fingerprints"]["recorded_sleep"]
    _, facts = member_facts(client, demo["window_id"], seed.CAREGIVER_B)
    assert facts["recorded_sleep"]["state"] == "acknowledged"


# ---------------------------------------------------------------- corrections keep originals (I2)

def test_rt_corrected_interval_preserves_original_evidence(client, demo):
    target = demo["final_wake_event_id"]
    with SessionLocal() as db:
        original = db.get(EvidenceEvent, target)
        orig = (original.started_at, original.ended_at, original.source, original.request_hash)
    rev4 = brief(client, demo["window_id"])
    r = correct(client, demo["window_id"], target=target, expected=4, end="2026-10-10T06:10:00-04:00")
    assert r.status_code == 200, r.text
    evidence = {e["id"]: e for e in client.get(f"/v1/windows/{demo['window_id']}/evidence", headers=PARENT).json()}
    assert target in evidence and evidence[target]["superseded_by"] == r.json()["correction_event_id"]
    assert evidence[r.json()["correction_event_id"]]["payload"]["original"]["source"] == "CAMERA"
    with SessionLocal() as db:
        again = db.get(EvidenceEvent, target)
        assert (again.started_at, again.ended_at, again.source, again.request_hash) == orig
    # The revision that showed the original is still retrievable and unchanged.
    assert brief(client, demo["window_id"], revision=4)["fingerprints"] == rev4["fingerprints"]
    # Correcting the same original again is refused: the chain must continue from the newer record.
    again = correct(client, demo["window_id"], target=target, expected=5, end="2026-10-10T06:20:00-04:00")
    assert again.status_code == 409 and again.json()["code"] == "ALREADY_CORRECTED"


# ---------------------------------------------------------------- conflicts keep uncertainty

def test_rt_unresolved_conflicting_sources_preserve_uncertainty(client, demo):
    # The camera watched 23:00-23:30 (usable observation) and recorded no sleep there.
    ingest([NewEvidence("rt-conflict", "sleep_interval", seed.at(9, "23:00"), seed.at(9, "23:30"),
                        {"synthetic": True})], source="PARENT")
    b = brief(client, demo["window_id"])
    rs = b["facts"]["recorded_sleep"]
    assert rs["state"] == "contested" and rs["minutes"] is None
    assert rs["contested_minutes"] == 30.0
    assert b["facts"]["status"] == "conflicting_evidence"
    week = client.get(f"/v1/windows/{demo['window_id']}/week", headers=PARENT).json()
    night = next(n for n in week["nights"] if n["local_date"] == "2026-10-09")
    assert night["minutes"] is None and night["excluded_reason"] == "sources disagree"
    assert week["counted"] == week["of"] - 1
    # A CAREGIVER report of the same thing is equally contested.
    f = facts_for("2026-10-09", seed.TZ, [
        ev(1, "camera_availability", "CAMERA", seed.at(9, "19:00"), seed.at(10, "07:00"), usable=True),
        ev(2, "sleep_interval", "CAMERA", seed.at(9, "19:00"), seed.at(9, "23:00")),
        ev(3, "sleep_interval", "CAREGIVER", seed.at(9, "22:00"), seed.at(9, "23:45"))])
    assert f["recorded_sleep"]["minutes"] is None and f["recorded_sleep"]["contested_minutes"] == 45.0


# ---------------------------------------------------------------- DST (I6)

DST_NIGHTS = [
    # zone, evening date, window minutes, sleep 22:00 -> 05:00 local minutes
    ("America/New_York", "2026-03-07", 660, 360),     # spring forward 02:00 -> 03:00
    ("America/New_York", "2026-10-31", 780, 480),     # fall back 02:00 -> 01:00
    ("Europe/London", "2026-03-28", 660, 360),        # 01:00 -> 02:00
    ("Europe/London", "2026-10-24", 780, 480),        # 02:00 -> 01:00
    ("Australia/Lord_Howe", "2026-10-03", 690, 390),  # 30-minute shift 02:00 -> 02:30
    ("Australia/Lord_Howe", "2026-04-04", 750, 450),  # 02:00 -> 01:30
]


@pytest.mark.parametrize("zone,local_date,window_minutes,sleep_minutes", DST_NIGHTS)
def test_rt_dst_nights_use_elapsed_time(zone, local_date, window_minutes, sleep_minutes):
    tz = ZoneInfo(zone)
    d = datetime.fromisoformat(local_date)
    start, end = engine.window_bounds(d.date(), zone)
    sleep_start = datetime(d.year, d.month, d.day, 22, 0, tzinfo=tz)
    nd = d.date().toordinal() + 1
    from datetime import date
    nxt = date.fromordinal(nd)
    sleep_end = datetime(nxt.year, nxt.month, nxt.day, 5, 0, tzinfo=tz)
    f = briefing.compute_facts((start, end), zone, [
        ev(1, "camera_availability", "CAMERA", start, end, usable=True),
        ev(2, "sleep_interval", "CAMERA", sleep_start, sleep_end)])
    assert f["window"]["elapsed_minutes"] == window_minutes
    assert f["observation_coverage"] == {"state": "full", "pct": 100.0, "observed_minutes": window_minutes,
                                         "window_minutes": window_minutes}
    assert f["recorded_sleep"]["minutes"] == sleep_minutes
    assert f["unobserved"]["minutes"] == 0


@pytest.mark.parametrize("local_date,window_minutes,sleep_minutes", [("2026-03-07", 660, 360),
                                                                     ("2026-10-31", 780, 480)])
def test_rt_dst_nights_through_the_database(client, demo, local_date, window_minutes, sleep_minutes):
    """Same nights in the household's zone, through ingestion, storage (UTC sessions) and the API."""
    d = datetime.fromisoformat(local_date)
    start, end = engine.window_bounds(d.date(), seed.TZ)
    from datetime import date
    nxt = date.fromordinal(d.date().toordinal() + 1)
    ingest([NewEvidence(f"rt-dst-obs-{local_date}", "camera_availability", start, end, {"usable": True}),
            NewEvidence(f"rt-dst-sleep-{local_date}", "sleep_interval",
                        datetime(d.year, d.month, d.day, 22, 0, tzinfo=NY),
                        datetime(nxt.year, nxt.month, nxt.day, 5, 0, tzinfo=NY), {})])
    f = brief(client, window_id_for(local_date))["facts"]
    assert f["window"]["elapsed_minutes"] == window_minutes
    assert f["observation_coverage"]["pct"] == 100.0 and f["observation_coverage"]["state"] == "full"
    assert f["recorded_sleep"]["minutes"] == sleep_minutes


# ---------------------------------------------------------------- cross-window corrections (I7)

def test_rt_cross_window_corrections_follow_declared_policy(client, demo):
    w = demo["window_id"]
    target = demo["final_wake_event_id"]
    control = window_id_for("2026-10-08")
    control_target = event_id("cam-1008-sleep")
    # A record spanning this window's end (06:30-07:30 on another night).
    ingest([NewEvidence("rt-span-obs", "camera_availability", at(2026, 10, 26, 19, 0), at(2026, 10, 27, 7, 0),
                        {"usable": True}),
            NewEvidence("rt-span-sleep", "sleep_interval", at(2026, 10, 27, 6, 30), at(2026, 10, 27, 7, 30), {})])
    span_window = window_id_for("2026-10-26")
    span_target = event_id("rt-span-sleep")
    revs, count = window_revisions(), evidence_count()

    attempts = [
        (w, dict(target=target, expected=4, end="2026-10-10T07:30:00-04:00"), "OUTSIDE_WINDOW"),
        (w, dict(target=target, expected=4, start="2026-10-09T18:59:00-04:00"), "OUTSIDE_WINDOW"),
        (w, dict(target=target, expected=4, end="2026-10-11T03:00:00-04:00"), "OUTSIDE_WINDOW"),
        (w, dict(target=control_target, expected=4, end="2026-10-09T06:00:00-04:00"), "OUTSIDE_WINDOW"),
        (control, dict(target=target, expected=revs[control], end="2026-10-10T06:00:00-04:00"), "OUTSIDE_WINDOW"),
        (span_window, dict(target=span_target, expected=revs[span_window], end="2026-10-27T06:45:00-04:00"),
         "TARGET_SPANS_WINDOWS"),
    ]
    for window, kwargs, code in attempts:
        r = correct(client, window, **kwargs)
        assert r.status_code == 422 and r.json()["code"] == code, (kwargs, r.text)
    assert window_revisions() == revs and evidence_count() == count

    ok = correct(client, w, target=target, expected=4, end="2026-10-10T06:40:00-04:00")
    assert ok.status_code == 200
    after = window_revisions()
    assert {k: v for k, v in after.items() if k != w} == {k: v for k, v in revs.items() if k != w}
    assert after[w] == revs[w] + 1
