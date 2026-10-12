"""Red-team fixtures and helpers. Production code is never modified.

Run against isolated databases:
    DATABASE_URL=postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_redteam
    PROVIDER_DATABASE_URL=postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_redteam
"""
import uuid
from datetime import datetime
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import engine, seed
from app.db import SessionLocal, transaction
from app.main import app
from app.models import BriefingWindow, EvidenceEvent
from app.security import lock_household

from ..conftest import PARENT

ALL_FACTS = ["recorded_sleep", "sleep_onset", "final_wake", "observation_coverage", "unobserved"]


@pytest.fixture
def lenient_client():
    """Returns 500 responses instead of raising, so a crash is an assertable status."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def ingest(items: list, *, source: str = "CAMERA", household: str = seed.HOUSEHOLD,
           child: str = seed.CHILD, tz: str = seed.TZ) -> dict:
    """Append evidence through the real engine (there is no HTTP ingestion route)."""
    with transaction() as db:
        lock_household(db, household)
        return engine.ingest(db, household_id=household, child_id=child, timezone=tz, source=source,
                             actor_member_id=None if source == "CAMERA" else seed.PARENT_A,
                             device_id=seed.CAMERA if source == "CAMERA" and household == seed.HOUSEHOLD
                             else (seed.OTHER_CAMERA if source == "CAMERA" else None),
                             items=items)


def window_id_for(local_date: str, child: str = seed.CHILD) -> str:
    with SessionLocal() as db:
        return db.execute(select(BriefingWindow.id).where(
            BriefingWindow.child_id == child,
            BriefingWindow.local_date == datetime.fromisoformat(local_date).date())).scalar_one()


def window_revisions() -> dict[str, int]:
    with SessionLocal() as db:
        return {w.id: w.current_revision for w in db.execute(select(BriefingWindow)).scalars()}


def evidence_count() -> int:
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(EvidenceEvent)).scalar_one()


def event_id(client_event_id: str) -> str:
    with SessionLocal() as db:
        return db.execute(select(EvidenceEvent.id).where(
            EvidenceEvent.client_event_id == client_event_id)).scalar_one()


def brief(client, window_id: str, headers=PARENT, revision: Optional[int] = None) -> dict:
    q = f"?revision={revision}" if revision is not None else ""
    r = client.get(f"/v1/windows/{window_id}/brief{q}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def correct(client, window_id: str, *, target: str, expected: int, start: Optional[str] = None,
            end: Optional[str] = None, headers=PARENT, key: Optional[str] = None):
    body = {"target_event_id": target, "expected_revision": expected,
            "client_event_id": key or f"rt-{uuid.uuid4().hex}"}
    if start is not None:
        body["corrected_start"] = start
    if end is not None:
        body["corrected_end"] = end
    return client.post(f"/v1/windows/{window_id}/corrections", headers=headers, json=body)


def ack(client, window_id: str, revision_id: str, headers, facts=None):
    return client.post(f"/v1/windows/{window_id}/acknowledgments", headers=headers,
                       json={"revision_id": revision_id, "displayed_facts": facts or ALL_FACTS})


def member_facts(client, window_id: str, member_id: str) -> tuple[dict, dict]:
    status = client.get(f"/v1/windows/{window_id}/changes", headers=PARENT).json()
    m = next(x for x in status["members"] if x["member_id"] == member_id)
    return m, {f["fact"]: f for f in m["facts"]}
