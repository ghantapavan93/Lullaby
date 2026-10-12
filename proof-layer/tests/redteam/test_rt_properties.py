"""Red team: property-based attacks (Hypothesis) on the pure core and on acknowledgments."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hypothesis import HealthCheck, given, settings, strategies as st

from app import briefing, engine, intervals as iv, seed
from app.briefing import EvidenceView
from app.db import transaction

from ..conftest import CAREGIVER, PARENT
from .conftest import ALL_FACTS, ack, brief, correct, member_facts

BASE = datetime(2026, 10, 9, 23, 0, tzinfo=timezone.utc)      # 19:00 New York
WINDOW = engine.window_bounds(date(2026, 10, 9), "America/New_York")

offsets = st.integers(min_value=-120, max_value=900)
spans = st.tuples(offsets, st.integers(min_value=1, max_value=400)).map(
    lambda p: (BASE + timedelta(minutes=p[0]), BASE + timedelta(minutes=p[0] + p[1])))
# Same instants expressed in different offsets must behave identically.
zones = st.sampled_from([timezone.utc, ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata"),
                         ZoneInfo("Australia/Lord_Howe"), timezone(timedelta(hours=-11))])


# ---------------------------------------------------------------- 1. interval algebra

@settings(database=None, max_examples=400, deadline=None)
@given(st.lists(st.tuples(spans, zones), max_size=12), st.randoms(use_true_random=False))
def test_rt_union_never_exceeds_sum_and_ignores_duplicates_order_and_offsets(raw, rnd):
    intervals = [(s.astimezone(z), e.astimezone(z)) for (s, e), z in raw]
    total = iv.minutes(intervals)
    assert total <= sum((e - s).total_seconds() / 60 for s, e in intervals) + 1e-9
    shuffled = intervals + intervals[: len(intervals) // 2]
    rnd.shuffle(shuffled)
    assert iv.minutes(shuffled) == total
    utc_only = [(s.astimezone(timezone.utc), e.astimezone(timezone.utc)) for s, e in intervals]
    assert iv.minutes(utc_only) == total
    # Exact against a minute grid.
    grid = set()
    for s, e in utc_only:
        a = int((s - BASE).total_seconds() // 60)
        grid.update(range(a, a + int((e - s).total_seconds() // 60)))
    assert total == len(grid)


# ---------------------------------------------------------------- 2. facts: coverage bounds and invariance

kinds = st.sampled_from(["camera_availability", "sleep_interval"])
sources = st.sampled_from(["CAMERA", "PARENT", "CAREGIVER"])
event = st.tuples(kinds, sources, spans, st.sampled_from([True, False, None]))


def views(raw, prefix="e"):
    out = []
    for i, (kind, source, (s, e), usable) in enumerate(raw):
        payload = {"usable": usable} if kind == "camera_availability" and usable is not None else {}
        out.append(EvidenceView(f"{prefix}{i}", kind, source, s, e, payload))
    return out


def essence(f):
    rs = f["recorded_sleep"]
    return (f["status"], f["observation_coverage"]["state"], f["observation_coverage"]["pct"],
            rs["state"], rs["minutes"], rs["contested_minutes"], rs["uncontested_minutes"],
            f["unobserved"]["minutes"], f["sleep_onset"]["at"], f["final_wake"]["at"])


@settings(database=None, max_examples=300, deadline=None)
@given(st.lists(event, max_size=10), st.randoms(use_true_random=False))
def test_rt_coverage_bounded_and_facts_invariant_to_duplicates_and_order(raw, rnd):
    evidence = views(raw)
    f = briefing.compute_facts(WINDOW, "America/New_York", evidence)
    cov = f["observation_coverage"]
    has_camera_report = any(e.kind == "camera_availability" and e.source == "CAMERA" for e in evidence)
    if not has_camera_report:
        assert cov["pct"] is None and cov["state"] == "unknown" and f["status"] == "unknown_observation"
    else:
        assert 0.0 <= cov["pct"] <= 100.0 and cov["observed_minutes"] <= cov["window_minutes"]
    rs = f["recorded_sleep"]
    if rs["minutes"] is not None:
        assert 0.0 <= rs["minutes"] <= f["window"]["elapsed_minutes"]
    # Re-delivered copies (new ids) and any order: same meaning.
    dup = evidence + views(raw[: len(raw) // 2 + 1], prefix="dup") if raw else evidence
    rnd.shuffle(dup)
    assert essence(briefing.compute_facts(WINDOW, "America/New_York", dup)) == essence(f)
    # Reports from people never change what the camera observed.
    people = [e for e in evidence if e.source != "CAMERA"]
    camera = [e for e in evidence if e.source == "CAMERA"]
    g = briefing.compute_facts(WINDOW, "America/New_York", camera + people + people)
    assert g["observation_coverage"] == briefing.compute_facts(WINDOW, "America/New_York", camera)["observation_coverage"]


# ---------------------------------------------------------------- 3. DST: elapsed time in every zone

DST_ZONES = ["America/New_York", "Europe/London", "Australia/Lord_Howe", "America/Santiago",
             "Pacific/Chatham", "America/St_Johns", "Asia/Tehran", "Europe/Chisinau"]


@settings(database=None, max_examples=400, deadline=None)
@given(st.sampled_from(DST_ZONES), st.dates(min_value=date(2026, 1, 1), max_value=date(2027, 12, 31)))
def test_rt_window_length_is_real_elapsed_time(zone, day):
    tz = ZoneInfo(zone)
    start, end = engine.window_bounds(day, zone)
    local_start = datetime(day.year, day.month, day.day, 19, 0, tzinfo=tz)
    nxt = day + timedelta(days=1)
    local_end = datetime(nxt.year, nxt.month, nxt.day, 7, 0, tzinfo=tz)
    shift = (local_end.utcoffset() - local_start.utcoffset()).total_seconds() / 60
    f = briefing.compute_facts((start, end), zone, [
        EvidenceView("o", "camera_availability", "CAMERA", start, end, {"usable": True})])
    assert f["window"]["elapsed_minutes"] == 720 - shift
    assert f["observation_coverage"]["pct"] == 100.0
    assert start.astimezone(tz).hour == 19 and end.astimezone(tz).hour == 7


# ---------------------------------------------------------------- 4. an ack of revision N never acknowledges N+1

op = st.one_of(
    st.tuples(st.just("correct"), st.integers(min_value=1, max_value=40)),
    st.tuples(st.just("ack"), st.sampled_from(["caregiver", "parent"]), st.integers(min_value=1, max_value=8),
              st.lists(st.sampled_from(ALL_FACTS), min_size=1, max_size=5, unique=True)),
)


@settings(database=None, max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture,
                                                                 HealthCheck.too_slow])
@given(st.lists(op, min_size=1, max_size=8))
def test_rt_ack_of_revision_n_never_acknowledges_a_later_revision(client, ops):
    with transaction() as db:
        demo = seed.load(db)
    w = demo["window_id"]
    target, end_minute = demo["final_wake_event_id"], 50
    who = {"caregiver": (CAREGIVER, seed.CAREGIVER_B), "parent": (PARENT, seed.PARENT_A)}
    revisions = {4: brief(client, w)}
    first_facts: dict[tuple[str, int], set] = {}
    submitted: dict[str, dict[str, int]] = {"caregiver": {}, "parent": {}}
    for o in ops:
        current = max(revisions)
        if o[0] == "correct":
            end_minute = max(1, end_minute - o[1])
            r = correct(client, w, target=target, expected=current, end=f"2026-10-10T06:{end_minute:02d}:00-04:00")
            assert r.status_code == 200, r.text
            target = r.json()["correction_event_id"]
            revisions[current + 1] = brief(client, w)
            continue
        _, member, pick, facts = o
        rev_no = min(pick, current) if pick >= 4 else 4
        headers, _ = who[member]
        r = ack(client, w, revisions[rev_no]["revision_id"], headers, facts=facts)
        key = (member, rev_no)
        if key in first_facts and first_facts[key] != set(facts):
            assert r.status_code == 409 and r.json()["code"] == "ACK_FACTS_DIFFER"
            continue
        assert r.status_code == 200, r.text
        first_facts.setdefault(key, set(facts))
        body = r.json()
        assert body["acknowledged_revision"] == rev_no and body["current_revision"] == current
        assert body["newer_available"] == (current > rev_no)
        for fact in first_facts[key]:
            submitted[member][fact] = max(submitted[member].get(fact, 0), rev_no)
    latest = revisions[max(revisions)]
    for member, (_, member_id) in who.items():
        m, facts = member_facts(client, w, member_id)
        for fact, info in facts.items():
            standing = submitted[member].get(fact)
            assert info["acknowledged_revision"] == standing             # never a revision not submitted
            if standing is None:
                assert info["state"] == "not_acknowledged"
            else:
                same = revisions[standing]["fingerprints"][fact] == latest["fingerprints"][fact]
                assert info["state"] == ("acknowledged" if same else "changed_since_acknowledgment")
        assert m["acknowledged_revision"] == (max(rev for (mem, rev) in first_facts if mem == member)
                                              if any(mem == member for mem, _ in first_facts) else None)
        assert m["has_acknowledged_current"] == (m["acknowledged_revision"] == max(revisions))
