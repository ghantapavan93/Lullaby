"""Shared helpers for the app interaction audit (not part of the app).

Targets a demo server started on port 8711 against the *_lab databases.
"""
import json
import time
from pathlib import Path

import httpx
import psycopg

BASE = "http://127.0.0.1:8711"
APP_DB = "postgresql://proof@127.0.0.1:54329/proof_layer_lab"
PROVIDER_DB = "postgresql://proof@127.0.0.1:54329/provider_sim_lab"
OUT = Path(__file__).resolve().parent.parent
SHOTS = OUT / "screens"
SHOTS.mkdir(parents=True, exist_ok=True)


def token(member="mem_parent_a"):
    return httpx.post(f"{BASE}/v1/dev/sessions", json={"member_id": member}).json()["token"]


def reset():
    r = httpx.post(f"{BASE}/v1/dev/reset", headers={"Authorization": f"Bearer {token()}"}, timeout=60)
    r.raise_for_status()


def q(sql, params=(), db=APP_DB):
    with psycopg.connect(db) as c:
        return c.execute(sql, params).fetchall()


class Recorder:
    """Captures console errors, page errors and every /v1 request per page."""

    def __init__(self):
        self.console, self.pageerrors, self.requests, self.failed = [], [], [], []

    def attach(self, page, label):
        page.on("console", lambda m: m.type in ("error", "warning") and self.console.append(
            {"page": label, "type": m.type, "text": m.text[:300]}))
        page.on("pageerror", lambda e: self.pageerrors.append({"page": label, "error": str(e)[:300]}))
        page.on("requestfailed", lambda r: self.failed.append(
            {"page": label, "url": r.url, "failure": r.failure}))

        def on_response(resp):
            if "/v1/" in resp.url or resp.status >= 400:
                self.requests.append({"page": label, "method": resp.request.method,
                                      "url": resp.url.replace(BASE, ""), "status": resp.status, "t": time.time()})
        page.on("response", on_response)

    def since(self, t0, label=None):
        return [r for r in self.requests if r["t"] >= t0 and (label is None or r["page"] == label)]


def calls(reqs, skip_poll=True):
    """Compact 'METHOD path status' list, optionally dropping the 4 s poll reads."""
    poll = ("/brief", "/changes", "/v1/xray", "/revisions", "/evidence")
    out = []
    for r in reqs:
        path = r["url"].split("?")[0]
        if skip_poll and r["method"] == "GET" and path.endswith(poll):
            continue
        out.append(f'{r["method"]} {path} {r["status"]}')
    return out


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
