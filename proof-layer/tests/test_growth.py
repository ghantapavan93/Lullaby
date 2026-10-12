"""Experiment assignment, exposure receipts, analytics ingestion.

Invariants
- Exposure exists only when the client reports a render; GET never creates it.
- A retried receipt (same render id) is recorded once.
- The rendered variant must equal the household's sticky assignment.
- Analytics are idempotent and allow-listed; free text is rejected.
"""
from sqlalchemy import func, select, text

from app import growth, seed
from app.db import SessionLocal, transaction
from app.models import ExperimentAssignment, RenderReceipt

from .conftest import PARENT


def receipts():
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(RenderReceipt)).scalar_one()


def test_reading_data_never_creates_exposure(client, demo):
    client.get(f"/v1/windows/{demo['window_id']}/brief", headers=PARENT)
    d = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    client.get(f"/v1/capability-decisions/{d['decision_id']}", headers=PARENT)
    assert receipts() == 0


def test_render_receipt_is_recorded_once_per_render(client, demo):
    d = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    body = {"render_id": "render-abc-123", "surface": "feature_availability", "subject_id": d["decision_id"],
            "experiment_key": "availability_explanation", "variant": d["experiment"]["variant"]}
    first = client.post("/v1/render-receipts", headers=PARENT, json=body).json()
    again = client.post("/v1/render-receipts", headers=PARENT, json=body).json()
    assert first["created"] is True and again["created"] is False and receipts() == 1


def test_rendered_variant_must_match_assignment(client, demo):
    d = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    wrong = next(v for v in growth.EXPERIMENTS["availability_explanation"]["variants"]
                 if v != d["experiment"]["variant"])
    r = client.post("/v1/render-receipts", headers=PARENT, json={
        "render_id": "render-bad-1", "surface": "feature_availability", "subject_id": d["decision_id"],
        "experiment_key": "availability_explanation", "variant": wrong})
    assert r.status_code == 422 and r.json()["code"] == "VARIANT_MISMATCH" and receipts() == 0


def test_assignment_is_sticky_even_if_the_hash_would_change(client, demo):
    first = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    with transaction() as db:   # simulate a later code change to the split: the stored row must win
        flipped = next(v for v in growth.EXPERIMENTS["availability_explanation"]["variants"]
                       if v != first["experiment"]["variant"])
        db.execute(text("UPDATE experiment_assignments SET variant=:v"), {"v": flipped})
    second = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    assert second["experiment"]["variant"] == flipped
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(ExperimentAssignment)).scalar_one() == 1


def test_analytics_are_idempotent_and_allow_listed(client, demo):
    ok = {"event_id": "evt-client-0001", "name": "briefing_viewed", "properties": {"revision": 4, "status": "incomplete_observation"}}
    assert client.post("/v1/analytics", headers=PARENT, json=ok).json()["created"] is True
    assert client.post("/v1/analytics", headers=PARENT, json=ok).json()["created"] is False
    leak = {"event_id": "evt-client-0002", "name": "briefing_viewed",
            "properties": {"revision": 4, "note": "baby had a fever"}}
    r = client.post("/v1/analytics", headers=PARENT, json=leak)
    assert r.status_code == 422 and r.json()["code"] == "PROPERTY_NOT_ALLOWED"


def test_rejected_purchases_are_counted_even_though_the_request_rolled_back(client, demo):
    d = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
    client.post("/v1/dev/mount", headers=PARENT, json={"mount": "flex_stand"})
    client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": "rejected-001"},
                json={"decision_id": d["decision_id"], "offer_id": d["offer"]["offer_id"]})
    names = [a["name"] for a in client.get("/v1/xray", headers=PARENT).json()["analytics"]]
    assert "purchase_rejected" in names


def test_metrics_expose_operations_without_tenant_data(client, demo):
    client.get(f"/v1/windows/{demo['window_id']}/brief", headers=PARENT)
    body = client.get("/metrics").text
    assert 'proof_layer_http_requests_total{method="GET",route="/v1/windows/{window_id}/brief",status="200"}' in body
    assert "proof_layer_outbox_jobs" in body and "proof_layer_undecided_purchase_oldest_seconds" in body
    assert "hh_demo" not in body and demo["window_id"] not in body


def test_no_offer_is_ever_attached_on_a_safety_surface(client, demo):
    for surface in ("live_view", "safety_alert"):
        d = client.post("/v1/capability-decisions", headers=PARENT,
                        json={"feature": "developmental_milestones", "surface": surface}).json()
        assert d["status"] == "BLOCKED" and d["offer"] is None
        assert d["inputs"]["offer_suppressed"] == "no offers on safety surfaces"
    control = client.post("/v1/capability-decisions", headers=PARENT,
                          json={"feature": "developmental_milestones"}).json()
    assert control["offer"] is not None


def test_sample_ratio_check_flags_broken_exposure_and_passes_a_fair_split():
    fair = growth.sample_ratio({"a": 5030, "b": 4970}, {"a": 0.5, "b": 0.5})
    broken = growth.sample_ratio({"a": 5000, "b": 4600}, {"a": 0.5, "b": 0.5})
    assert fair["mismatch"] is False and fair["p_value"] > 0.4
    assert broken["mismatch"] is True and broken["p_value"] < 0.001
