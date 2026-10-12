"""Household isolation.

Invariant: a member of one household can never read or change anything that
belongs to another household, whatever identifiers they send. Foreign
identifiers are indistinguishable from missing ones (404). The database
rejects cross-household references even if application code were bypassed.
"""
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import seed
from app.db import engine
from app.models import BriefingWindow, EvidenceEvent

from .conftest import OTHER, PARENT


@pytest.fixture
def foreign(client, demo):
    """Identifiers that belong to hh_demo, to be used by hh_other's owner."""
    brief = client.get(f"/v1/windows/{demo['window_id']}/brief", headers=PARENT).json()
    decision = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    intent = client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": "isolation-key-1"},
                         json={"decision_id": decision["decision_id"],
                               "offer_id": decision["offer"]["offer_id"]})
    assert intent.status_code == 201, intent.text
    return {"window": demo["window_id"], "revision": brief["revision_id"], "event": demo["final_wake_event_id"],
            "decision": decision["decision_id"], "offer": decision["offer"]["offer_id"],
            "intent": intent.json()["intent_id"]}


def test_foreign_reads_look_like_missing_resources(client, foreign):
    for path in (f"/v1/windows/{foreign['window']}/brief", f"/v1/windows/{foreign['window']}/evidence",
                 f"/v1/windows/{foreign['window']}/revisions", f"/v1/windows/{foreign['window']}/changes",
                 f"/v1/capability-decisions/{foreign['decision']}", f"/v1/purchase-intents/{foreign['intent']}"):
        r = client.get(path, headers=OTHER)
        assert r.status_code == 404, (path, r.status_code, r.text)
        assert "hh_demo" not in r.text and "Baby R" not in r.text


def test_foreign_writes_are_rejected_and_change_nothing(client, foreign, demo):
    w = foreign["window"]
    attempts = [
        client.post(f"/v1/windows/{w}/corrections", headers=OTHER, json={
            "target_event_id": foreign["event"], "corrected_end": "2026-10-10T06:35:00-04:00",
            "expected_revision": 4, "client_event_id": "foreign-corr-1"}),
        client.post(f"/v1/windows/{w}/acknowledgments", headers=OTHER, json={
            "revision_id": foreign["revision"], "displayed_facts": ["recorded_sleep"]}),
        client.post("/v1/purchase-intents", headers=OTHER | {"Idempotency-Key": "foreign-buy-1"},
                    json={"decision_id": foreign["decision"], "offer_id": foreign["offer"]}),
        client.post("/v1/render-receipts", headers=OTHER, json={
            "render_id": "foreign-render-1", "surface": "night_briefing", "subject_id": foreign["revision"]}),
    ]
    assert [r.status_code for r in attempts] == [404, 404, 404, 404], [r.text for r in attempts]
    after = client.get(f"/v1/windows/{w}/brief", headers=PARENT).json()
    assert after["revision"] == 4


def test_own_window_cannot_be_corrected_with_a_foreign_event(client, demo):
    other_window = client.get("/v1/me", headers=OTHER).json()["windows"][0]["window_id"]
    r = client.post(f"/v1/windows/{other_window}/corrections", headers=OTHER, json={
        "target_event_id": demo["final_wake_event_id"], "corrected_end": "2026-10-10T06:35:00-04:00",
        "expected_revision": 1, "client_event_id": "mixed-tenant-1"})
    assert r.status_code == 404


def test_own_window_cannot_acknowledge_a_foreign_revision(client, foreign):
    other_window = client.get("/v1/me", headers=OTHER).json()["windows"][0]["window_id"]
    r = client.post(f"/v1/windows/{other_window}/acknowledgments", headers=OTHER, json={
        "revision_id": foreign["revision"], "displayed_facts": ["recorded_sleep"]})
    assert r.status_code == 404


def test_denials_are_recorded_for_xray(client, foreign):
    client.get(f"/v1/windows/{foreign['window']}/brief", headers=OTHER)
    log = client.get("/v1/xray", headers=OTHER).json()["authorization"]
    assert any(e["reason"] == "denied:not_in_household" for e in log)
    assert all(e["member_id"] == seed.OTHER_OWNER for e in log)


def test_database_rejects_cross_household_references(demo):
    """Bypass the application entirely: composite foreign keys still hold."""
    with engine.connect() as conn:
        demo_window = conn.execute(select(BriefingWindow.id).where(
            BriefingWindow.household_id == seed.HOUSEHOLD)).scalars().first()
        conn.rollback()
        cases = [
            ("INSERT INTO evidence_events (id, household_id, child_id, client_event_id, request_hash, kind, "
             "source, started_at, ended_at) VALUES ('evt_x', 'hh_other', 'child_demo', 'k', 'h', "
             "'sleep_interval', 'PARENT', now(), now() + interval '1 hour')"),
            ("INSERT INTO evidence_events (id, household_id, child_id, client_event_id, request_hash, kind, "
             "source, started_at, ended_at, supersedes_event_id) VALUES ('evt_y', 'hh_other', 'child_other', "
             f"'k2', 'h', 'sleep_interval', 'PARENT', now(), now() + interval '1 hour', '{demo['final_wake_event_id']}')"),
            ("INSERT INTO acknowledgments (id, household_id, window_id, revision_id, revision, member_id, "
             f"fingerprints) SELECT 'ack_x', 'hh_other', '{demo_window}', id, revision, 'mem_other_owner', "
             "'{}'::jsonb FROM briefing_revisions WHERE household_id = 'hh_demo' LIMIT 1"),
        ]
        for sql in cases:
            with pytest.raises(IntegrityError):
                with conn.begin():
                    conn.execute(text(sql))


def test_evidence_listing_never_mixes_households(client, demo):
    rows = client.get(f"/v1/windows/{demo['window_id']}/evidence", headers=PARENT).json()
    with engine.connect() as conn:
        own = set(conn.execute(select(EvidenceEvent.id).where(
            EvidenceEvent.household_id == seed.HOUSEHOLD)).scalars())
    assert rows and {r["id"] for r in rows} <= own
