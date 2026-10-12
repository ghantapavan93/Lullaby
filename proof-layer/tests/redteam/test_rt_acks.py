"""Red team: acknowledgments (I9, I10) and revocation."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import seed
from app.db import SessionLocal, transaction
from app.main import app
from app.models import Acknowledgment, AckHead

from ..conftest import CAREGIVER, PARENT
from .conftest import ALL_FACTS, ack, brief, correct, member_facts, window_id_for

EARLIER_END = "2026-10-10T06:35:00-04:00"


def test_rt_ack_binds_to_the_displayed_revision_even_when_newer_exists(client, demo):
    w = demo["window_id"]
    shown = brief(client, w, CAREGIVER)                       # caregiver's screen shows revision 4
    assert correct(client, w, target=demo["final_wake_event_id"], expected=4, end=EARLIER_END).status_code == 200
    r = ack(client, w, shown["revision_id"], CAREGIVER)       # clicks on the stale screen
    body = r.json()
    assert r.status_code == 200
    assert (body["acknowledged_revision"], body["current_revision"], body["newer_available"]) == (4, 5, True)
    assert set(body["changed_since"]) == {"recorded_sleep", "final_wake"}
    with SessionLocal() as db:
        rows = list(db.execute(select(Acknowledgment).where(Acknowledgment.member_id == seed.CAREGIVER_B)).scalars())
    assert [(a.revision, a.revision_id) for a in rows] == [(4, shown["revision_id"])]


def test_rt_stale_screen_cannot_acknowledge_unseen_update(client, demo):
    w = demo["window_id"]
    shown = brief(client, w, CAREGIVER)
    correct(client, w, target=demo["final_wake_event_id"], expected=4, end=EARLIER_END)
    ack(client, w, shown["revision_id"], CAREGIVER)
    m, facts = member_facts(client, w, seed.CAREGIVER_B)
    assert m["has_acknowledged_current"] is False and m["acknowledged_revision"] == 4
    assert facts["final_wake"]["state"] == "changed_since_acknowledgment"
    assert facts["recorded_sleep"]["state"] == "changed_since_acknowledgment"
    assert facts["observation_coverage"]["state"] == "acknowledged"     # unchanged by the correction
    assert set(m["needs_review"]) == {"recorded_sleep", "final_wake"}


def test_rt_delayed_older_ack_cannot_override_newer(client, demo):
    w = demo["window_id"]
    rev4 = brief(client, w, CAREGIVER)
    correct(client, w, target=demo["final_wake_event_id"], expected=4, end=EARLIER_END)
    rev5 = brief(client, w, CAREGIVER)
    assert ack(client, w, rev5["revision_id"], CAREGIVER).status_code == 200
    late = ack(client, w, rev4["revision_id"], CAREGIVER)     # delivered after the newer one
    assert late.status_code == 200 and late.json()["newer_available"] is True
    m, facts = member_facts(client, w, seed.CAREGIVER_B)
    assert m["acknowledged_revision"] == 5 and m["has_acknowledged_current"] is True
    assert all(f["state"] == "acknowledged" and f["acknowledged_revision"] == 5 for f in facts.values())
    with SessionLocal() as db:
        assert db.get(AckHead, (seed.CAREGIVER_B, w)).revision == 5


def test_rt_racing_old_and_new_acks_leave_the_newest_standing(demo):
    """The two clicks arrive concurrently on separate clients; the head never moves backwards."""
    with TestClient(app) as c:
        w = demo["window_id"]
        rev4 = brief(c, w, CAREGIVER)
        correct(c, w, target=demo["final_wake_event_id"], expected=4, end=EARLIER_END)
        rev5 = brief(c, w, CAREGIVER)
    gate = Barrier(6)

    def send(i):
        with TestClient(app) as ci:
            gate.wait(timeout=10)
            return ack(ci, w, (rev5 if i % 2 else rev4)["revision_id"], CAREGIVER).status_code

    with ThreadPoolExecutor(6) as pool:
        assert list(pool.map(send, range(6))) == [200] * 6
    with SessionLocal() as db:
        assert db.get(AckHead, (seed.CAREGIVER_B, w)).revision == 5


def test_rt_partial_newer_ack_does_not_mark_unseen_facts_acknowledged(client, demo):
    w = demo["window_id"]
    correct(client, w, target=demo["final_wake_event_id"], expected=4, end=EARLIER_END)
    rev5 = brief(client, w, CAREGIVER)
    assert ack(client, w, rev5["revision_id"], CAREGIVER, facts=["observation_coverage"]).status_code == 200
    _, facts = member_facts(client, w, seed.CAREGIVER_B)
    assert facts["observation_coverage"]["state"] == "acknowledged"
    assert {k for k, f in facts.items() if f["state"] == "not_acknowledged"} == set(ALL_FACTS) - {"observation_coverage"}


def test_rt_changed_provenance_requires_renewed_review(client, demo):
    """A correction that keeps the exact same times (same minutes) but replaces the
    camera's record with the parent's: provenance changed, so review is needed."""
    w = demo["window_id"]
    rev4 = brief(client, w, CAREGIVER)
    assert ack(client, w, rev4["revision_id"], CAREGIVER).status_code == 200
    r = correct(client, w, target=demo["final_wake_event_id"], expected=4)      # no new times at all
    assert r.status_code == 200, r.text
    rev5 = brief(client, w, CAREGIVER)
    assert rev5["facts"]["recorded_sleep"]["minutes"] == rev4["facts"]["recorded_sleep"]["minutes"] == 640
    assert rev5["facts"]["final_wake"]["at"] == rev4["facts"]["final_wake"]["at"]
    assert rev5["facts"]["final_wake"]["sources"] == ["PARENT"]
    _, facts = member_facts(client, w, seed.CAREGIVER_B)
    assert facts["final_wake"]["state"] == "changed_since_acknowledgment"
    assert facts["recorded_sleep"]["state"] == "changed_since_acknowledgment"


def test_rt_ack_cannot_borrow_a_revision_from_another_window(client, demo):
    control = window_id_for("2026-10-08")
    control_rev = brief(client, control, CAREGIVER)["revision_id"]
    r = ack(client, demo["window_id"], control_rev, CAREGIVER)
    assert r.status_code == 404
    with SessionLocal() as db:
        assert db.execute(select(Acknowledgment)).first() is None


def test_rt_revoked_caregiver_cannot_read_or_acknowledge(client, demo):
    """Revocation exists only as members.revoked_at (no API to revoke); set it directly."""
    w = demo["window_id"]
    rev = brief(client, w, CAREGIVER)["revision_id"]
    with transaction() as db:
        db.execute(text("UPDATE members SET revoked_at = now() WHERE id = :m"), {"m": seed.CAREGIVER_B})
    for r in (client.get(f"/v1/windows/{w}/brief", headers=CAREGIVER),
              client.get("/v1/me", headers=CAREGIVER),
              ack(client, w, rev, CAREGIVER),
              client.post("/v1/render-receipts", headers=CAREGIVER, json={
                  "render_id": "rt-revoked-render", "surface": "night_briefing", "subject_id": rev}),
              client.post("/v1/analytics", headers=CAREGIVER, json={
                  "event_id": "rt-revoked-evt", "name": "briefing_viewed", "properties": {"revision": 4}})):
        assert r.status_code == 403 and r.json()["code"] == "ACCESS_REVOKED", r.text
    with SessionLocal() as db:
        assert db.execute(select(Acknowledgment)).first() is None
        assert db.execute(text("SELECT count(*) FROM render_receipts")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM analytics_events WHERE event_id='rt-revoked-evt'")).scalar_one() == 0
    # The revoked member also disappears from who-has-reviewed.
    members = client.get(f"/v1/windows/{w}/changes", headers=PARENT).json()["members"]
    assert seed.CAREGIVER_B not in {m["member_id"] for m in members}
