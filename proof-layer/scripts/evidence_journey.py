"""Bring a running demo server to "recorded evidence analysed", through the
real API: build the evidence-journey scenario (Milestones ended), deliver the
store's renewal, start the worker as its own process, wait for the run.

    python scripts/evidence_journey.py [--base http://127.0.0.1:8710]

Prints the asset id and run state. Used by the media consistency and playback
checks; it changes only the synthetic demo household.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx


def run(base: str, timeout_s: float = 120.0) -> dict:
    http = httpx.Client(base_url=base, timeout=60)
    token = http.post("/v1/dev/sessions", json={"member_id": "mem_parent_a"}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    http.post("/v1/dev/access-scenario", headers=auth, json={"scenario": "evidence_journey"}).raise_for_status()
    start = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
             - timedelta(days=5)).isoformat()
    http.post("/v1/dev/purchase-evidence", headers=auth, json={
        "channel": "apple", "external_ref": "apple-txn-1505", "plan_code": "milestones",
        "provider_event": "DID_RENEW", "provider_version": 2, "starts_at": start, "days": 365}).raise_for_status()
    http.post("/v1/dev/video/worker", headers=auth).raise_for_status()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        assets = http.get("/v1/video/assets", headers=auth).json()["assets"]
        if assets and assets[0]["run"] and assets[0]["run"]["state"] in ("COMPLETED", "FAILED"):
            return {"asset_id": assets[0]["id"], "state": assets[0]["run"]["state"], "token": token}
        time.sleep(1)
    raise SystemExit("the analysis didn't finish in time")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8710")
    r = run(ap.parse_args().base)
    print(r["asset_id"], r["state"])
    sys.exit(0 if r["state"] == "COMPLETED" else 1)
