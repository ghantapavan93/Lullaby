"""Pure briefing rules: coverage, recorded sleep, conflicts, fingerprints.

Invariants
- Coverage counts only explicit usable camera observation, clipped to the window.
- No observation report at all is UNKNOWN; a measured zero is a different state.
- Parent or caregiver sleep records never add coverage.
- Duplicated or overlapping sleep records never add minutes.
- Reported sleep where the camera observed no sleep stays unresolved: no exact total.
- Durations are real elapsed time, including across daylight-saving changes.
- A fact's fingerprint changes when its value, provenance or uncertainty changes, and only then.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hypothesis import given, settings, strategies as st

from app import intervals as iv
from app.briefing import EvidenceView, compute_facts, fingerprints
from app.engine import window_bounds

NY = ZoneInfo("America/New_York")
WINDOW = window_bounds(date(2026, 10, 9), "America/New_York")


def t(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, h, m, tzinfo=NY)


def ev(i, kind, source, start, end, **payload):
    return EvidenceView(i, kind, source, start, end, payload, payload.pop("supersedes", None))


S3_BASIS = {"camera_basis": [["2026-10-10T04:20:00-04:00", "2026-10-10T06:50:00-04:00"]]}


def corrected_s3(end):
    """A parent correction of the camera record s3, carrying its camera basis."""
    return EvidenceView("c3", "sleep_interval", "PARENT", t(10, "04:20"), t(10, end), S3_BASIS, "s3")


def facts(*evidence):
    return compute_facts(WINDOW, "America/New_York", list(evidence))


CANONICAL = [
    ev("o1", "camera_availability", "CAMERA", t(9, "19:00"), t(10, "03:40"), usable=True),
    ev("o2", "camera_availability", "CAMERA", t(10, "04:20"), t(10, "07:00"), usable=True),
    ev("s1", "sleep_interval", "CAMERA", t(9, "19:00"), t(9, "23:00")),
    ev("s2", "sleep_interval", "CAMERA", t(9, "23:30"), t(10, "03:40")),
    ev("s3", "sleep_interval", "CAMERA", t(10, "04:20"), t(10, "06:50")),
]


def test_canonical_night_matches_the_specification():
    f = facts(*CANONICAL)
    assert f["window"]["elapsed_minutes"] == 720
    assert f["recorded_sleep"]["minutes"] == 640            # 10h 40m
    assert f["observation_coverage"] == {"state": "partial", "pct": 94.4,
                                         "observed_minutes": 680.0, "window_minutes": 720.0}
    assert f["unobserved"]["minutes"] == 40
    assert f["unobserved"]["intervals"][0]["reason"] == "no_observation_reported"
    assert f["final_wake"]["at"] == "2026-10-10T06:50:00-04:00"
    assert f["status"] == "incomplete_observation"


def test_no_observation_evidence_is_unknown_not_full_and_not_zero():
    f = facts(ev("s", "sleep_interval", "CAMERA", t(9, "20:00"), t(9, "21:00")))
    assert f["observation_coverage"]["state"] == "unknown"
    assert f["observation_coverage"]["pct"] is None
    assert f["unobserved"]["minutes"] is None
    assert f["status"] == "unknown_observation"


def test_empty_night_is_unknown():
    f = facts()
    assert f["observation_coverage"]["pct"] is None
    assert f["recorded_sleep"]["state"] == "no_records" and f["recorded_sleep"]["minutes"] is None


def test_confirmed_zero_is_distinct_from_unknown():
    f = facts(ev("o", "camera_availability", "CAMERA", WINDOW[0], WINDOW[1], usable=False))
    assert f["observation_coverage"]["state"] == "none" and f["observation_coverage"]["pct"] == 0.0
    assert f["unobserved"]["intervals"][0]["reason"] == "camera_reported_unavailable"


def test_parent_reported_sleep_never_adds_coverage():
    base = facts(*CANONICAL)
    with_parent = facts(*CANONICAL, ev("p", "sleep_interval", "PARENT", t(10, "03:40"), t(10, "04:20")))
    assert with_parent["observation_coverage"] == base["observation_coverage"]
    assert with_parent["unobserved"] == base["unobserved"]
    # It is recorded sleep with parent provenance, inside unobserved time: not contested.
    assert with_parent["recorded_sleep"]["minutes"] == 680
    assert "PARENT" in with_parent["recorded_sleep"]["sources"]


def test_parent_report_is_not_camera_observation_even_without_camera_data():
    f = facts(ev("p", "sleep_interval", "PARENT", t(9, "20:00"), t(9, "21:00")))
    assert f["observation_coverage"]["pct"] is None


def test_overlapping_and_duplicated_records_are_not_double_counted():
    f = facts(*CANONICAL,
              ev("dup", "sleep_interval", "CAMERA", t(9, "19:00"), t(9, "23:00")),
              ev("overlap", "sleep_interval", "CAMERA", t(9, "22:00"), t(9, "23:15")))
    assert f["recorded_sleep"]["minutes"] == 655  # 640 + the extra 15 minutes only


def test_conflicting_evidence_has_no_exact_total():
    # Camera watched 23:00-23:30 and recorded no sleep; a caregiver claims sleep then.
    f = facts(*CANONICAL, ev("c", "sleep_interval", "CAREGIVER", t(9, "23:00"), t(9, "23:30")))
    assert f["status"] == "conflicting_evidence"
    assert f["recorded_sleep"]["minutes"] is None
    assert f["recorded_sleep"]["contested_minutes"] == 30
    assert f["recorded_sleep"]["uncontested_minutes"] == 640
    assert f["conflicts"][0]["minutes"] == 30


def test_explicit_correction_is_a_selection_not_a_conflict():
    corrected = [e for e in CANONICAL if e.id != "s3"] + [corrected_s3("06:35")]
    f = facts(*corrected)
    assert f["status"] == "incomplete_observation"
    assert f["recorded_sleep"]["minutes"] == 625              # 10h 25m
    assert f["observation_coverage"]["pct"] == 94.4           # unchanged
    assert f["final_wake"] == {"at": "2026-10-10T06:35:00-04:00", "sources": ["PARENT"],
                               "at_window_edge": False}


def test_stretching_a_correction_into_watched_awake_time_is_contested():
    """Critic F-03: the camera watched 06:50-07:00 and saw no sleep; a correction
    that stretches s3 to 07:00 is a claim against the camera, not a selection."""
    f = facts(*[e for e in CANONICAL if e.id != "s3"], corrected_s3("07:00"))
    assert f["status"] == "conflicting_evidence"
    assert f["recorded_sleep"]["minutes"] is None and f["recorded_sleep"]["contested_minutes"] == 10


def test_a_correction_without_camera_basis_is_weighed_like_a_report():
    plain = EvidenceView("c3", "sleep_interval", "PARENT", t(10, "04:20"), t(10, "06:35"), {}, "s3")
    f = facts(*[e for e in CANONICAL if e.id != "s3"], plain)
    assert f["recorded_sleep"]["contested_minutes"] == 135


def test_reported_sleep_in_unobserved_time_is_labelled_not_hidden():
    f = facts(*CANONICAL, ev("p", "sleep_interval", "PARENT", t(10, "03:40"), t(10, "04:20")))
    assert f["recorded_sleep"]["reported_in_unobserved_minutes"] == 40


def test_moving_a_sleep_block_changes_its_fingerprint():
    """Critic F-08: same total, same sources, different time: still a change."""
    moved = [e for e in CANONICAL if e.id != "s2"] + [
        ev("s2b", "sleep_interval", "CAMERA", t(9, "23:20"), t(10, "03:30"))]
    a, b = facts(*CANONICAL), facts(*moved)
    assert a["recorded_sleep"]["minutes"] == b["recorded_sleep"]["minutes"]
    assert fingerprints(a)["recorded_sleep"] != fingerprints(b)["recorded_sleep"]


def test_dst_window_measures_real_elapsed_time():
    # US clocks fall back on 2026-11-01: 19:00 Oct 31 -> 07:00 Nov 1 is 13 hours.
    start, end = window_bounds(date(2026, 10, 31), "America/New_York")
    f = compute_facts((start, end), "America/New_York",
                      [EvidenceView("o", "camera_availability", "CAMERA", start, end, {"usable": True})])
    assert f["window"]["elapsed_minutes"] == 780
    assert f["observation_coverage"]["pct"] == 100.0
    spring = window_bounds(date(2026, 3, 7), "America/New_York")
    assert iv.minutes([spring]) == 660


def test_fingerprints_track_meaning_not_formatting():
    before = fingerprints(facts(*CANONICAL))
    corrected = [e for e in CANONICAL if e.id != "s3"] + [corrected_s3("06:35")]
    after = fingerprints(facts(*corrected))
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"recorded_sleep", "final_wake"}


def test_same_value_with_changed_provenance_changes_the_fingerprint():
    same_time = [e for e in CANONICAL if e.id != "s3"] + [corrected_s3("06:50")]
    before, after = fingerprints(facts(*CANONICAL)), fingerprints(facts(*same_time))
    assert facts(*same_time)["final_wake"]["at"] == facts(*CANONICAL)["final_wake"]["at"]
    assert before["final_wake"] != after["final_wake"]           # provenance moved CAMERA -> PARENT
    assert before["observation_coverage"] == after["observation_coverage"]


def test_unobserved_uncertainty_change_changes_its_fingerprint():
    reported = CANONICAL + [ev("off", "camera_availability", "CAMERA", t(10, "03:40"), t(10, "04:20"), usable=False)]
    a, b = fingerprints(facts(*CANONICAL)), fingerprints(facts(*reported))
    assert a["unobserved"] != b["unobserved"] and a["observation_coverage"] == b["observation_coverage"]


# ---------------------------------------------------------------- properties

base = datetime(2026, 10, 9, 23, 0, tzinfo=timezone.utc)
spans = st.lists(st.tuples(st.integers(0, 900), st.integers(1, 300)), max_size=12).map(
    lambda xs: [(base + timedelta(minutes=a), base + timedelta(minutes=a + d)) for a, d in xs])


def brute_minutes(intervals):
    covered = set()
    for s, e in intervals:
        start = int((s - base).total_seconds() // 60)
        covered.update(range(start, start + int((e - s).total_seconds() // 60)))
    return len(covered)


@settings(max_examples=300, deadline=None)
@given(spans, spans)
def test_interval_algebra_matches_a_minute_grid(a, b):
    assert iv.minutes(a) == brute_minutes(a)
    assert iv.minutes(a + a) == iv.minutes(a)                          # duplicates add nothing
    assert iv.minutes(a + b) <= iv.minutes(a) + iv.minutes(b)
    assert iv.minutes(iv.subtract(a, b)) + iv.minutes(iv.intersect(a, b)) == iv.minutes(a)
    window = (base + timedelta(minutes=100), base + timedelta(minutes=700))
    assert iv.minutes(iv.clip(a, window)) <= 600


@settings(max_examples=150, deadline=None)
@given(spans, spans)
def test_reported_sleep_never_changes_coverage(observed, reported):
    win = (base, base + timedelta(minutes=1200))
    cam = [EvidenceView(f"o{i}", "camera_availability", "CAMERA", s, e, {"usable": True})
           for i, (s, e) in enumerate(observed)]
    rep = [EvidenceView(f"r{i}", "sleep_interval", "PARENT", s, e, {}) for i, (s, e) in enumerate(reported)]
    a = compute_facts(win, "UTC", cam)
    b = compute_facts(win, "UTC", cam + rep)
    assert a["observation_coverage"] == b["observation_coverage"]
