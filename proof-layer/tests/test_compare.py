"""Same symptom, two causes: the comparison reports what the backend really
decided and refused, and its refusal probes leave nothing behind."""
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import AccessAction, AnalyticsEvent, CapabilityDecision, EntitlementClaim, PurchaseIntent

from .conftest import CAREGIVER, PARENT


def counts():
    with SessionLocal() as db:
        return {m.__tablename__: db.execute(select(func.count()).select_from(m)).scalar_one()
                for m in (AccessAction, CapabilityDecision, AnalyticsEvent, EntitlementClaim, PurchaseIntent)}


def test_billing_retry_and_a_wrong_mount_look_alike_and_are_decided_differently(client, demo):
    r = client.post("/v1/dev/compare", headers=PARENT, json={"a": "billing_retry", "b": "incompatible_hardware"})
    assert r.status_code == 200, r.text
    c = r.json()
    a, b = c["a"], c["b"]
    assert (a["status"], b["status"]) == ("BILLING_RETRY", "ACTIVE_HARDWARE_BLOCKED")
    assert "nextnap" not in a["usable_features"] and "nextnap" not in b["usable_features"]   # the same symptom
    assert "status" in c["differs_in"] and a["offer"] is None and b["offer"] is None
    buy_a, buy_b = a["probes"][0], b["probes"][0]
    assert not buy_a["offer_made"] and "PAYMENT_RETRYING" in buy_a["blockers"]
    assert not buy_b["offer_made"] and "MOUNT_NOT_SUPPORTED" in buy_b["blockers"]
    connect_a = next(p for p in a["probes"] if p["action"].startswith("Connect"))
    assert connect_a["refused"] and connect_a["code"] == "PURCHASE_PAYMENT_RETRYING"


def test_probes_are_rolled_back(client, demo):
    client.post("/v1/dev/compare", headers=PARENT, json={"a": "plan_ended", "b": "billing_retry"})
    after_compare = counts()
    client.post("/v1/dev/access-scenario", headers=PARENT, json={"scenario": "billing_retry"})
    assert counts() == after_compare            # the same state as building billing_retry alone


def test_only_listed_pairs_and_only_dev_controllers(client, demo):
    same = client.post("/v1/dev/compare", headers=PARENT, json={"a": "billing_retry", "b": "billing_retry"})
    assert same.status_code == 422 and same.json()["code"] == "NOT_COMPARABLE"
    assert client.post("/v1/dev/compare", headers=CAREGIVER,
                       json={"a": "billing_retry", "b": "plan_ended"}).status_code == 403
