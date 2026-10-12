"""One request, one pooled connection.

Invariant: no request, allowed or denied, holds more than one application
database connection at a time. Writing audit records on a second connection
starved the pool at 32 concurrent requests (docs/BENCHMARKS.md); this test
pins the repaired behavior for every kind of request.
"""
import threading

import pytest
from sqlalchemy import event

from app.db import engine

from .conftest import CAREGIVER, OTHER, PARENT


@pytest.fixture
def peak_connections():
    lock, state = threading.Lock(), {"open": 0, "peak": 0}

    def checkout(*_):
        with lock:
            state["open"] += 1
            state["peak"] = max(state["peak"], state["open"])

    def checkin(*_):
        with lock:
            state["open"] -= 1

    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
    yield state
    event.remove(engine, "checkout", checkout)
    event.remove(engine, "checkin", checkin)


def test_every_request_kind_uses_one_connection(client, demo, peak_connections):
    w = demo["window_id"]
    rev = client.get(f"/v1/windows/{w}/brief", headers=CAREGIVER).json()["revision_id"]
    calls = [
        lambda: client.get(f"/v1/windows/{w}/brief", headers=PARENT),                       # read, allowed
        lambda: client.get(f"/v1/windows/{w}/brief", headers=OTHER),                        # read, denied (404)
        lambda: client.post(f"/v1/windows/{w}/acknowledgments", headers=CAREGIVER,
                            json={"revision_id": rev, "displayed_facts": ["recorded_sleep"]}),  # write
        lambda: client.post(f"/v1/windows/{w}/corrections", headers=CAREGIVER, json={      # write, denied (403)
            "target_event_id": demo["final_wake_event_id"], "expected_revision": 4,
            "corrected_end": "2026-10-10T06:35:00-04:00", "client_event_id": "budget-check-1"}),
        lambda: client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}),
    ]
    for call in calls:
        peak_connections["peak"] = peak_connections["open"]
        call()
        assert peak_connections["peak"] <= 1, "a request held two application connections"


def test_denials_are_still_recorded_after_the_rollback(client, demo):
    client.get(f"/v1/windows/{demo['window_id']}/brief", headers=OTHER)
    log = client.get("/v1/xray", headers=OTHER).json()["authorization"]
    assert any(e["reason"] == "denied:not_in_household" for e in log)
