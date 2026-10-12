"""Shared helpers for the red-team reproductions. No production code is touched."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import claims, seed
from app.db import SessionLocal, transaction
from app.main import app
from app.models import BriefingWindow, EvidenceEvent, PurchaseIntent

from ..conftest import PARENT

ALL_FACTS = ["recorded_sleep", "sleep_onset", "final_wake", "observation_coverage", "unobserved"]


@pytest.fixture
def lenient_client():
    """A client that returns 500s instead of raising, so a crash is an assertable status."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def event_id(client_event_id: str) -> str:
    with SessionLocal() as db:
        return db.execute(select(EvidenceEvent.id).where(
            EvidenceEvent.client_event_id == client_event_id)).scalar_one()


def brief(client, window_id, headers=PARENT):
    r = client.get(f"/v1/windows/{window_id}/brief", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def correct(client, window_id, *, target, expected, end=None, start=None, headers=PARENT, key=None):
    body = {"target_event_id": target, "expected_revision": expected,
            "client_event_id": key or f"critic-{uuid.uuid4().hex}"}
    if end is not None:
        body["corrected_end"] = end
    if start is not None:
        body["corrected_start"] = start
    return client.post(f"/v1/windows/{window_id}/corrections", headers=headers, json=body)


def ack(client, window_id, revision_id, headers, facts=None):
    return client.post(f"/v1/windows/{window_id}/acknowledgments", headers=headers,
                       json={"revision_id": revision_id, "displayed_facts": facts or ALL_FACTS})


def member_facts(client, window_id, member_id):
    status = client.get(f"/v1/windows/{window_id}/changes", headers=PARENT).json()
    m = next(x for x in status["members"] if x["member_id"] == member_id)
    return m, {f["fact"]: f["state"] for f in m["facts"]}


def decide(client, feature, headers=PARENT):
    r = client.post("/v1/capability-decisions", headers=headers, json={"feature": feature})
    assert r.status_code == 200, r.text
    return r.json()


def buy(client, decision, key, headers=PARENT):
    return client.post("/v1/purchase-intents", headers=headers | {"Idempotency-Key": key},
                       json={"decision_id": decision["decision_id"],
                             "offer_id": (decision.get("offer") or {}).get("offer_id", "none")})


def plan_code():
    with SessionLocal() as db:
        # Plans are per camera and derived from claims (contract change, migration 0003).
        return claims.camera_plan(db, seed.HOUSEHOLD, seed.CAMERA, "cat-2026-10-public")["plan_code"]


def intents_by_state():
    with SessionLocal() as db:
        return [(i.id, i.state, i.terms["plan_code"]) for i in db.execute(
            select(PurchaseIntent).where(PurchaseIntent.household_id == seed.HOUSEHOLD)).scalars()]


def make_jobs_due():
    with transaction() as db:
        db.execute(text("UPDATE outbox SET available_at = now(), lease_until = now() - interval '1 second' "
                        "WHERE state IN ('pending','in_progress')"))


def window_for(local_date):
    with SessionLocal() as db:
        return db.execute(select(BriefingWindow).where(BriefingWindow.child_id == seed.CHILD,
                                                       BriefingWindow.local_date == local_date)).scalar_one()
