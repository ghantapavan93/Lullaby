"""A synthetic Growth run: six journeys through the real API, and the funnel
the app computes from what they stored.

    DATABASE_URL=... PROVIDER_DATABASE_URL=... python scripts/growth_run.py

Each journey resets the demo, builds a Scenario Studio scenario, takes the
steps a parent (or the store) would take next, and reads GET /v1/access/metrics.
Writes artifacts/growth/synthetic-funnel.json. Every number is synthetic: it
shows what the instrumentation can count, not what customers do.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PROOF_LAYER_DEV", "1")
os.environ.setdefault("PROOF_LAYER_SAMPLE", "staged")

from fastapi.testclient import TestClient  # noqa: E402

from app import provider, seed, worker  # noqa: E402
from app.main import app  # noqa: E402
from app.security import issue_token  # noqa: E402

PARENT = {"Authorization": f"Bearer {issue_token(seed.PARENT_A)}"}
CAMERA = seed.CAMERA


def day(offset: int) -> str:
    return (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=offset)).isoformat()


def build(c: TestClient, key: str) -> None:
    c.post("/v1/dev/access-scenario", headers=PARENT, json={"scenario": key}).raise_for_status()


def verify(c: TestClient) -> str:
    return c.post("/v1/access/verify", headers=PARENT, json={"device_id": CAMERA}).json()["check"]["status"]


def opened(c: TestClient) -> None:
    c.post("/v1/analytics", headers=PARENT, json={"event_id": f"growth-{time.time_ns()}", "name": "access_feature_opened",
                                                  "properties": {"device_id": CAMERA, "feature": "nextnap"}})


def connect(c: TestClient, channel: str, ref: str) -> int:
    rev = c.get("/v1/access", headers=PARENT).json()["state_revision"]
    return c.post("/v1/access/bind", headers=PARENT | {"Idempotency-Key": f"growth-{ref}"},
                  json={"device_id": CAMERA, "channel": channel, "external_ref": ref,
                        "expected_state_revision": rev}).status_code


def decide(c: TestClient, feature: str = "nextnap") -> dict:
    return c.post("/v1/capability-decisions", headers=PARENT,
                  json={"feature": feature, "device_id": CAMERA}).json()


def checkout(c: TestClient, decision: dict) -> str:
    """Try to pay against a decision; returns the refusal code, or the intent state."""
    offer = decision.get("offer") or {}
    r = c.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": f"growth-{time.time_ns()}"},
               json={"decision_id": decision["decision_id"], "offer_id": offer.get("offer_id", "memories-annual")})
    body = r.json()
    return body.get("code") or body.get("detail", {}).get("code") or body.get("state", str(r.status_code))


def store(c: TestClient, body: dict) -> int:
    return c.post("/v1/dev/purchase-evidence", headers=PARENT, json=body).status_code


SHOWN: dict = {}

JOURNEYS = {
    "paid, not connected → connect → verify → opened": [
        lambda c: build(c, "paid_not_active"), lambda c: connect(c, "web", "web-ord-1001"), verify, opened],
    "plan ended → offer shown → store renewal → old offer paid → verify → opened": [
        lambda c: build(c, "plan_ended"),
        lambda c: SHOWN.update(offer=decide(c)),
        lambda c: store(c, {"channel": "apple", "external_ref": "apple-txn-8008", "plan_code": "memories",
                            "status": "verified", "provider_version": 2, "starts_at": day(-5), "days": 365}),
        lambda c: checkout(c, SHOWN["offer"]),
        verify, opened],
    "payment on hold → store recovery → verify": [
        lambda c: build(c, "billing_retry"),
        lambda c: store(c, {"channel": "google", "external_ref": "gpa-1202", "plan_code": "memories",
                            "provider_event": "SUBSCRIPTION_RECOVERED", "provider_version": 3, "starts_at": day(0),
                            "days": 365}),
        verify],
    "purchase pending (unresolved)": [lambda c: build(c, "purchase_uncertain"), verify],
    "paid twice → checkout for a feature the plan already has": [
        lambda c: build(c, "paid_twice"),
        lambda c: checkout(c, decide(c, "sleep_analysis"))],
    "renewal → recorded evidence → report": [
        lambda c: build(c, "evidence_journey"),
        lambda c: store(c, {"channel": "apple", "external_ref": "apple-txn-1505", "plan_code": "milestones",
                            "provider_event": "DID_RENEW", "provider_version": 2, "starts_at": day(-5), "days": 365}),
        lambda c: worker.run_once("growth-run"),
        lambda c: c.post(f"/v1/video/assets/{c.get('/v1/video/assets', headers=PARENT).json()['assets'][0]['id']}"
                         "/media-link", headers=PARENT).status_code,
        lambda c: c.post("/v1/analytics", headers=PARENT, json={
            "event_id": f"growth-ev-{time.time_ns()}", "name": "evidence_review_opened",
            "properties": {"device_id": CAMERA,
                           "asset_id": c.get("/v1/video/assets", headers=PARENT).json()["assets"][0]["id"]}}).status_code,
    ],
}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    provider.create_schema()
    results = []
    with TestClient(app) as c:
        for name, steps in JOURNEYS.items():
            started = datetime.now(timezone.utc).isoformat()
            for step in steps:
                step(c)
            m = c.get("/v1/access/metrics", headers=PARENT).json()
            results.append({"journey": name, "started_at": started, "metrics": m})
    out = ROOT.parent / "artifacts" / "growth" / "synthetic-funnel.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "synthetic": True,
                               "journeys": results}, indent=1), encoding="utf-8")
    for r in results:
        t, u, b, v = (r["metrics"][k] for k in ("time_to_usable", "unresolved", "billing_recovery",
                                                "verification_outcomes"))
        print(f"{r['journey']}: usable {t['confirmed_to_usable']['count']}/{len(t['journeys'])} "
              f"(median {t['confirmed_to_usable']['median_s']} s), unresolved {u['count']}, "
              f"recovered {b['recovered']}/{len(b['purchases'])}, checks usable {v['usable']}/{v['checks']}, "
              f"refused before payment {r['metrics']['refused_before_payment']}, "
              f"video {r['metrics'].get('video', {}).get('runs')}")
    print(out)


if __name__ == "__main__":
    main()
