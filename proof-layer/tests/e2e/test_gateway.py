"""The public review gateway (deploy/gateway), with real processes: two
workspace slots, each with its own databases, app and worker, behind the
gateway. Two visitors are two cookie jars.

What a public demo needs, checked here:
- each visitor gets a private workspace that starts from the same synthetic state;
- one visitor's scenario or reset never changes what the other sees;
- tokens, clip ids and media links don't cross workspaces;
- clip bytes stream through the gateway, Range requests included;
- capacity, per-address and per-workspace limits hold; uploads and debug
  routes are refused;
- leases survive a gateway restart; an idle workspace ends and is reseeded;
- health reports a dead worker as degraded.
"""
from __future__ import annotations

import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from ..conftest import ROOT

sys.path.insert(0, str(ROOT.parent / "deploy" / "gateway"))
import stack  # noqa: E402


VISITORS: list = []        # the two browsers, shared by the tests that follow the isolation test


@pytest.fixture(scope="module")
def gw():
    state = stack.up(slots=2, per_ip=1, extra_env={"GATEWAY_HEARTBEAT_STALE_S": "6"})
    yield state
    stack.down()


def visitor(gw, ip):
    c = httpx.Client(base_url=gw["url"], timeout=120, headers={"X-Forwarded-For": ip})
    return c


def start(c):
    return c.post("/v1/workspace")


def bearer(c, member="mem_parent_a"):
    return {"Authorization": "Bearer " + c.post("/v1/dev/sessions", json={"member_id": member}).json()["token"]}


def access(c, h):
    body = c.get("/v1/access", headers=h).json()
    return body["state_revision"], sorted((cam["device"]["id"], cam["status"]) for cam in body["cameras"])


def test_without_a_workspace_nothing_reaches_the_app(gw):
    c = visitor(gw, "10.0.0.9")
    assert c.get("/v1/dev/accounts").json()["code"] == "NO_WORKSPACE"
    c.cookies.set("pl_ws", "forged-token-value")
    assert c.get("/v1/access").status_code == 401
    for path in ("/metrics", "/docs", "/openapi.json", "/redoc"):
        assert c.get(path).status_code == 404, path
    assert c.post("/v1/dev/video/upload", content=b"x").status_code in (401, 403)


def test_two_visitors_are_isolated_and_the_limits_hold(gw):
    a, b, c = visitor(gw, "10.0.0.1"), visitor(gw, "10.0.0.2"), visitor(gw, "10.0.0.3")
    VISITORS[:] = [a, b]                                             # the same two browsers in the next tests
    ra = start(a)
    assert ra.status_code == 201 and "httponly" in ra.headers["set-cookie"].lower()
    again = visitor(gw, "10.0.0.1")
    assert start(again).json()["code"] == "TOO_MANY_WORKSPACES"        # one new workspace per address per hour
    assert start(b).status_code == 201
    assert start(c).json()["code"] == "ALL_WORKSPACES_BUSY"            # two slots, both leased
    assert start(a).json()["new"] is False                              # the same browser keeps its workspace

    with sqlite3.connect(Path(gw["home"]) / "gateway.sqlite3") as db:   # only hashes are stored
        stored = {r[0] for r in db.execute("SELECT token_hash FROM slots WHERE token_hash IS NOT NULL")}
    assert a.cookies["pl_ws"] not in stored and len(stored) == 2

    ha, hb = bearer(a), bearer(b)
    assert access(a, ha)[1] == access(b, hb)[1]                         # the same starting point
    before_b = access(b, hb)
    seen, stop = [], threading.Event()

    def read_b():
        while not stop.is_set():
            r = b.get("/v1/access", headers=hb)
            seen.append((r.status_code, r.json().get("state_revision")))
    readers = [threading.Thread(target=read_b) for _ in range(2)]
    for t in readers:
        t.start()
    try:
        for key in ("paid_not_active", "plan_ended"):
            a.post("/v1/dev/access-scenario", headers=ha, json={"scenario": key}).raise_for_status()
        a.post("/v1/dev/reset", headers=ha).raise_for_status()
        ha = bearer(a)
        a.post("/v1/dev/access-scenario", headers=ha, json={"scenario": "paid_not_active"}).raise_for_status()
    finally:
        stop.set()
        for t in readers:
            t.join(timeout=60)
    assert access(a, ha) != before_b
    assert access(b, hb) == before_b
    assert seen and all(s == (200, before_b[0]) for s in seen), set(seen)
    assert b.get("/v1/access", headers=ha).status_code == 401           # A's token means nothing in B's workspace


def test_media_streams_through_the_gateway_and_stays_in_its_workspace(gw):
    if not VISITORS:
        pytest.skip("run with the module: it reuses the two visitors")
    a, b = VISITORS
    assert start(a).json()["new"] is False
    ha = bearer(a)
    a.post("/v1/dev/access-scenario", headers=ha, json={"scenario": "evidence_journey"}).raise_for_status()
    day0 = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=5))
    a.post("/v1/dev/purchase-evidence", headers=ha, json={
        "channel": "apple", "external_ref": "apple-txn-1505", "plan_code": "milestones", "provider_event": "DID_RENEW",
        "provider_version": 2, "starts_at": day0.isoformat(), "days": 365}).raise_for_status()
    assert a.post("/v1/dev/video/worker", headers=ha).status_code in (200, 202)
    assert a.post("/v1/dev/video/worker", headers=ha).json()["code"] == "WORKER_ALREADY_STARTED"
    for _ in range(120):
        assets = a.get("/v1/video/assets", headers=ha).json()["assets"]
        if assets and assets[0]["run"] and assets[0]["run"]["state"] == "COMPLETED":
            break
        time.sleep(1)
    asset = assets[0]["id"]
    assert a.post(f"/v1/video/assets/{asset}/media-link", headers=ha).status_code == 200
    part = a.get(f"/v1/video/media/{asset}", headers={"Range": "bytes=0-4095"})
    assert part.status_code == 206 and len(part.content) == 4096 and part.content[:4] == b"\x1a\x45\xdf\xa3"
    assert part.headers["cache-control"] == "private, no-store"
    assert start(b).json()["new"] is False
    b.cookies.set("pl_media", a.cookies.get("pl_media", path=f"/v1/video/media/{asset}") or "", path=f"/v1/video/media/{asset}")
    assert b.get(f"/v1/video/media/{asset}").status_code == 404          # A's clip isn't in B's workspace


def test_leases_survive_a_gateway_restart(gw):
    a, _ = VISITORS
    with sqlite3.connect(Path(gw["home"]) / "gateway.sqlite3") as db:
        leased = db.execute("SELECT count(*) FROM slots WHERE state='leased'").fetchone()[0]
    stack.start_gateway(gw)
    with sqlite3.connect(Path(gw["home"]) / "gateway.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM slots WHERE state='leased'").fetchone()[0] == leased
    assert start(a).json()["new"] is False                              # the same browser, the same workspace
    assert a.get("/v1/access", headers=bearer(a)).status_code == 200


def test_health_reports_a_dead_worker():
    state = stack.up(slots=1, per_ip=5, idle_minutes=0.1, extra_env={"GATEWAY_HEARTBEAT_STALE_S": "6"})
    try:
        c = visitor(state, "10.0.0.7")
        assert c.get("/healthz").json()["status"] == "ok"
        # Expiry: idle for longer than six seconds ends the workspace, and it is reseeded for the next visitor.
        assert start(c).status_code == 201
        time.sleep(8)
        assert c.get("/v1/dev/accounts").json()["code"] == "WORKSPACE_EXPIRED"
        for _ in range(60):
            if c.get("/healthz").json()["workspaces"].get("clean") == 1:
                break
            time.sleep(1)
        assert c.get("/healthz").json()["workspaces"].get("clean") == 1
        stack.kill(state["pids"][1])                                    # slot 1's worker
        time.sleep(8)
        h = c.get("/healthz")
        assert h.status_code == 503 and h.json()["slots"][0]["worker"] is False
    finally:
        stack.down()
