"""Red team: experiments, exposure and analytics (I16, I17, I28)."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import growth, seed
from app.db import SessionLocal
from app.main import app
from app.models import ExperimentAssignment, RenderReceipt

from ..conftest import CAREGIVER, OTHER, PARENT

KEY = "availability_explanation"


def decide(client, feature="nextnap", headers=PARENT, **extra):
    r = client.post("/v1/capability-decisions", headers=headers, json={"feature": feature} | extra)
    assert r.status_code == 200, r.text
    return r.json()


def receipts(*where):
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(RenderReceipt).where(*where)).scalar_one()


def assignments(household=None):
    with SessionLocal() as db:
        stmt = select(func.count()).select_from(ExperimentAssignment)
        if household:
            stmt = stmt.where(ExperimentAssignment.household_id == household)
        return db.execute(stmt).scalar_one()


def test_rt_assignment_is_stable_across_decisions_and_code_changes(client, demo, monkeypatch):
    first = decide(client)["experiment"]
    for feature in ("sleep_analysis", "video_history_7d", "nextnap"):
        assert decide(client, feature)["experiment"] == first
    # A later code change to the split (same config version) must not reassign anyone.
    spec = dict(growth.EXPERIMENTS[KEY])
    spec["variants"] = tuple(reversed(spec["variants"]))
    monkeypatch.setitem(growth.EXPERIMENTS, KEY, spec)
    assert decide(client)["experiment"] == first
    assert assignments(seed.HOUSEHOLD) == 1


def test_rt_versions_are_recorded_on_decisions_assignments_and_receipts(client, demo):
    d = decide(client)
    assert d["catalog_version"] == "cat-2026-10-public" and d["policy_version"] == "pol-2026-10-public"
    assert d["offer"]["catalog_version"] == d["catalog_version"] and d["offer"]["policy_version"] == d["policy_version"]
    assert d["experiment"]["config_version"] == growth.EXPERIMENTS[KEY]["config_version"]
    r = client.post("/v1/render-receipts", headers=PARENT, json={
        "render_id": "rt-versions-render", "surface": "feature_availability", "subject_id": d["decision_id"],
        "experiment_key": KEY, "variant": d["experiment"]["variant"]})
    assert r.status_code == 200
    with SessionLocal() as db:
        row = db.execute(select(RenderReceipt)).scalar_one()
        assert row.config_version == "v1" and row.variant == d["experiment"]["variant"]


def test_rt_render_is_never_inferred_from_reads(client, demo):
    w = demo["window_id"]
    d = decide(client)
    for path in (f"/v1/windows/{w}/brief", f"/v1/windows/{w}/brief?revision=1", f"/v1/windows/{w}/revisions",
                 f"/v1/windows/{w}/changes", f"/v1/windows/{w}/week", f"/v1/windows/{w}/evidence",
                 f"/v1/capability-decisions/{d['decision_id']}", "/v1/xray", "/v1/me", "/v1/features",
                 f"/v1/devices/{seed.CAMERA}/plan-preview", "/v1/household/commercial"):
        for headers in (PARENT, CAREGIVER):
            client.get(path, headers=headers)
    assert receipts() == 0


def test_rt_duplicate_exposure_receipts_do_not_inflate(client, demo):
    d = decide(client)
    body = {"render_id": "rt-dup-render-01", "surface": "feature_availability", "subject_id": d["decision_id"],
            "experiment_key": KEY, "variant": d["experiment"]["variant"]}
    gate = Barrier(8)

    def send(_):
        with TestClient(app) as c:
            gate.wait(timeout=20)
            return c.post("/v1/render-receipts", headers=PARENT, json=body)

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(send, range(8)))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert sum(r.json()["created"] for r in results) == 1 and receipts() == 1
    # Several renders of the same subject (two tabs): the experiment's exposure
    # count is per household, so it still counts one unit.
    client.post("/v1/render-receipts", headers=PARENT, json=body | {"render_id": "rt-dup-render-02"})
    with SessionLocal() as db:
        health = growth.experiment_health(db, KEY)
    assert health["exposed"]["total"] == 1


def test_rt_receipt_cannot_claim_a_variant_other_than_the_assignment(client, demo):
    d = decide(client)
    wrong = next(v for v in growth.EXPERIMENTS[KEY]["variants"] if v != d["experiment"]["variant"])
    base = {"surface": "feature_availability", "subject_id": d["decision_id"]}
    cases = [({"render_id": "rt-wrong-1", "experiment_key": KEY, "variant": wrong}, "VARIANT_MISMATCH"),
             ({"render_id": "rt-wrong-2", "variant": wrong}, "VARIANT_WITHOUT_EXPERIMENT"),
             ({"render_id": "rt-wrong-3", "experiment_key": "made_up", "variant": wrong}, "INVALID_EXPERIMENT"),
             ({"render_id": "rt-wrong-4", "experiment_key": KEY, "variant": None}, "VARIANT_MISMATCH")]
    for extra, code in cases:
        r = client.post("/v1/render-receipts", headers=PARENT, json=base | extra)
        assert r.status_code == 422 and r.json()["code"] == code, r.text
    # Night briefing is not this experiment's surface.
    r = client.post("/v1/render-receipts", headers=PARENT, json={
        "render_id": "rt-wrong-5", "surface": "night_briefing", "subject_id": d["decision_id"],
        "experiment_key": KEY, "variant": d["experiment"]["variant"]})
    assert r.status_code in (404, 422)
    assert receipts() == 0


def test_rt_experiment_eligibility_is_honored(client, demo):
    """Eligible: households whose owner/admin opened a capability decision."""
    w = demo["window_id"]
    rev = client.get(f"/v1/windows/{w}/brief", headers=CAREGIVER).json()
    assert client.post("/v1/capability-decisions", headers=CAREGIVER, json={"feature": "nextnap"}).status_code == 403
    client.post("/v1/render-receipts", headers=CAREGIVER, json={
        "render_id": "rt-elig-render", "surface": "night_briefing", "subject_id": rev["revision_id"]})
    client.post("/v1/analytics", headers=CAREGIVER, json={
        "event_id": "rt-elig-evt-1", "name": "availability_viewed",
        "properties": {"feature": "nextnap", "status": "BLOCKED", "variant": "reason_sentence"}})
    assert assignments() == 0
    decide(client)
    assert assignments(seed.HOUSEHOLD) == 1 and assignments(seed.OTHER) == 0


def test_rt_clients_cannot_post_server_events_or_prose(client, demo):
    for name, props, code in (("purchase_confirmed", {"intent_id": "pi_x", "plan_code": "memories"}, "SERVER_ONLY_EVENT"),
                              ("purchase_rejected", {"code": "X"}, "SERVER_ONLY_EVENT"),
                              ("briefing_viewed", {"revision": "four"}, "PROPERTY_INVALID"),
                              ("briefing_viewed", {"revision": 4.0}, "PROPERTY_INVALID"),
                              ("briefing_viewed", {"revision": True}, "PROPERTY_INVALID"),
                              ("briefing_viewed", {"revision": -1}, "PROPERTY_INVALID"),
                              ("availability_viewed", {"feature": "Baby has a fever"}, "PROPERTY_INVALID"),
                              ("availability_viewed", {"feature": {"note": "x"}}, "PROPERTY_INVALID")):
        r = client.post("/v1/analytics", headers=PARENT, json={"event_id": f"rt-ev-{name}-{abs(hash(str(props)))}"[:80],
                                                               "name": name, "properties": props})
        assert r.status_code == 422 and r.json()["code"] == code, (name, props, r.text)


def test_rt_kill_switch(client, demo):
    """Was a skip ("no kill switch exists"); built in migration 0005. While
    killed: everyone gets the control variant, no assignment is written, and
    exposure is refused. Reviving resumes the stored assignment."""
    first = decide(client)["experiment"]
    assert first["killed"] is False and assignments(seed.HOUSEHOLD) == 1
    switch = lambda killed: client.post(f"/v1/dev/experiments/{KEY}", headers=PARENT,  # noqa: E731
                                        json={"killed": killed, "reason": "guardrail breached in test"})
    assert switch(True).status_code == 200

    other = decide(client, headers=OTHER)["experiment"]
    control = growth.EXPERIMENTS[KEY]["control"]
    assert other == {"key": KEY, "variant": control, "config_version": first["config_version"], "killed": True}
    assert assignments(seed.OTHER) == 0                          # nobody new enters a killed experiment
    d = decide(client)
    r = client.post("/v1/render-receipts", headers=PARENT, json={
        "render_id": "rt-killed-1", "surface": "feature_availability", "subject_id": d["decision_id"],
        "experiment_key": KEY, "variant": control})
    assert r.status_code == 409 and r.json()["code"] == "EXPERIMENT_KILLED" and receipts() == 0

    assert switch(False).status_code == 200
    again = decide(client)["experiment"]
    assert again["killed"] is False and again["variant"] == first["variant"]   # sticky across the kill
    assert client.post(f"/v1/dev/experiments/{KEY}", headers=CAREGIVER,
                       json={"killed": True, "reason": "caregiver tries"}).status_code == 403
