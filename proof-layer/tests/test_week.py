"""The 7-night view and past-night corrections.

Invariant: a correction to any past night is reflected in every multi-night
value the next time it is read, and the corrected night is marked. Nights
without an exact total are excluded from averages and named, never silently
counted. (Nanit documents that past days "may not recalculate"; this is the
alternative design, not a claim about Nanit's internals.)
"""
from sqlalchemy import select

from app import seed
from app.db import transaction
from app.engine import NewEvidence, ingest
from app.models import BriefingWindow

from .conftest import PARENT


def window_on(client, local_date):
    me = client.get("/v1/me", headers=PARENT).json()
    return next(w["window_id"] for w in me["windows"] if w["local_date"] == local_date)


def week(client, demo):
    return client.get(f"/v1/windows/{demo['window_id']}/week", headers=PARENT).json()


def test_week_is_seven_nights_with_a_visible_denominator(client, demo):
    w = week(client, demo)
    assert [n["local_date"] for n in w["nights"]][0] == "2026-10-03" and w["of"] == 7 and w["counted"] == 7
    assert w["average_minutes"] == round(sum(n["minutes"] for n in w["nights"]) / 7, 1)


def test_past_night_correction_is_reflected_on_the_next_read(client, demo):
    before = week(client, demo)
    oct8 = window_on(client, "2026-10-08")
    brief = client.get(f"/v1/windows/{oct8}/brief", headers=PARENT).json()
    target = brief["facts"]["recorded_sleep"]["intervals"][-1]["event_id"]
    r = client.post(f"/v1/windows/{oct8}/corrections", headers=PARENT, json={
        "target_event_id": target, "corrected_end": "2026-10-09T06:15:00-04:00",
        "expected_revision": brief["revision"], "client_event_id": "past-night-001"})
    assert r.status_code == 200, r.text
    after = week(client, demo)
    night = next(n for n in after["nights"] if n["local_date"] == "2026-10-08")
    assert night["corrected"] is True and night["minutes"] == 655          # 685 - 30
    assert after["average_minutes"] == round(before["average_minutes"] - 30 / 7, 1)
    # The night the parent is looking at did not get a new revision.
    assert client.get(f"/v1/windows/{demo['window_id']}/brief", headers=PARENT).json()["revision"] == 4


def test_nights_without_an_exact_total_are_excluded_and_named(client, demo):
    with transaction() as db:   # a caregiver claims sleep the camera watched as awake on Oct 7
        ingest(db, household_id=seed.HOUSEHOLD, child_id=seed.CHILD, timezone=seed.TZ, source="CAREGIVER",
               actor_member_id=seed.CAREGIVER_B, device_id=None, items=[NewEvidence(
                   "care-oct7-claim", "sleep_interval", seed.at(8, "00:35"), seed.at(8, "00:55"), {"synthetic": True})])
    w = week(client, demo)
    oct7 = next(n for n in w["nights"] if n["local_date"] == "2026-10-07")
    assert oct7["minutes"] is None and oct7["excluded_reason"] == "sources disagree"
    assert w["counted"] == 6 and w["of"] == 7
