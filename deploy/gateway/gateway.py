"""Review gateway: one private, expiring synthetic workspace per visitor.

Behind it runs a fixed pool of isolated workspaces ("slots"). Each slot is a
full copy of the Proof Layer: its own databases, app process (so its own
signing key), worker, and media folder. The gateway:

- gives a visitor a slot on POST /v1/workspace, reseeded to the demo's starting
  state, and a random 256-bit cookie (only its SHA-256 is stored);
- sends that visitor's requests to that slot and nowhere else, streaming
  bodies both ways (video Range requests included);
- frees a slot after 30 idle minutes or 2 hours, and reseeds it before anyone
  else gets it;
- limits new workspaces per address, requests per workspace, and worker
  launches; refuses uploads; hides debug routes;
- reports health honestly: database, each slot's API, each worker's heartbeat.

Nothing about one visitor's slot is reachable from another's cookie, and a
slot's session tokens are signed by its own process, so they mean nothing
anywhere else. State lives in a small SQLite file, so leases survive a restart.

    GATEWAY_SLOTS=1,2 GATEWAY_PORT_BASE=9100 uvicorn gateway:app --port 8800
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

ENV = os.environ.get
SLOTS = [int(s) for s in ENV("GATEWAY_SLOTS", "1,2").split(",") if s.strip()]
PORT_BASE = int(ENV("GATEWAY_PORT_BASE", "9100"))
STATE = Path(ENV("GATEWAY_STATE", str(Path(__file__).with_name("gateway.sqlite3"))))
IDLE_S = float(ENV("GATEWAY_IDLE_MINUTES", "30")) * 60
MAX_S = float(ENV("GATEWAY_MAX_MINUTES", "120")) * 60
NEW_PER_IP_PER_HOUR = int(ENV("GATEWAY_NEW_PER_IP_PER_HOUR", "6"))
REQUESTS_PER_MINUTE = int(ENV("GATEWAY_REQUESTS_PER_MINUTE", "900"))
WORKER_LAUNCH_EVERY_S = float(ENV("GATEWAY_WORKER_LAUNCH_EVERY_S", "10"))
SECURE_COOKIE = ENV("GATEWAY_SECURE_COOKIE", "1") == "1"
HEARTBEATS = Path(ENV("GATEWAY_HEARTBEATS")) if ENV("GATEWAY_HEARTBEATS") else None
HEARTBEAT_STALE_S = float(ENV("GATEWAY_HEARTBEAT_STALE_S", "30"))
PG_DSN = ENV("GATEWAY_PG_DSN")
REPO = Path(__file__).resolve().parents[2]
STATIC = Path(ENV("GATEWAY_STATIC", str(REPO / "proof-layer" / "static")))
LANDING = Path(ENV("GATEWAY_LANDING", str(REPO / "deploy" / "landing" / "index.html")))
COOKIE = "pl_ws"

BLOCKED = {"/v1/dev/video/upload": "Uploads are off in the public review: only the generated test clip is used."}
HIDDEN = ("/metrics", "/docs", "/redoc", "/openapi.json")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host"}


# ------------------------------------------------------------------ state

class Store:
    """Slot leases in SQLite. One lock: every change is a few rows."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS slots (slot INTEGER PRIMARY KEY, state TEXT NOT NULL, "
                            "token_hash TEXT, created REAL, last_seen REAL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS starts (ip TEXT NOT NULL, at REAL NOT NULL)")
            for slot in SLOTS:   # a new slot starts dirty: it is reseeded before first use
                self.db.execute("INSERT OR IGNORE INTO slots (slot, state) VALUES (?, 'dirty')", (slot,))
            self.db.execute(f"DELETE FROM slots WHERE slot NOT IN ({','.join('?' * len(SLOTS))})", SLOTS)

    def lease_of(self, token: Optional[str]) -> Optional[tuple[int, float, float]]:
        if not token:
            return None
        with self.lock:
            row = self.db.execute("SELECT slot, created, last_seen FROM slots WHERE state='leased' AND token_hash=?",
                                  (digest(token),)).fetchone()
        return row

    def touch(self, slot: int) -> None:
        with self.lock:
            self.db.execute("UPDATE slots SET last_seen=? WHERE slot=?", (time.time(), slot))

    def starts_last_hour(self, ip: str) -> int:
        with self.lock:
            self.db.execute("DELETE FROM starts WHERE at < ?", (time.time() - 3600,))
            return self.db.execute("SELECT count(*) FROM starts WHERE ip=?", (ip,)).fetchone()[0]

    def claim(self, token: str, ip: str) -> Optional[tuple[int, str]]:
        """Lease a free slot to this token: a clean one if any, else a dirty one."""
        with self.lock:
            row = self.db.execute("SELECT slot, state FROM slots WHERE state IN ('clean','dirty') "
                                  "ORDER BY state='dirty', slot LIMIT 1").fetchone()
            if row is None:
                return None
            now = time.time()
            self.db.execute("UPDATE slots SET state='leased', token_hash=?, created=?, last_seen=? WHERE slot=?",
                            (digest(token), now, now, row[0]))
            self.db.execute("INSERT INTO starts (ip, at) VALUES (?, ?)", (ip, now))
            return row[0], row[1]

    def release(self, slot: int) -> None:
        with self.lock:
            self.db.execute("UPDATE slots SET state='dirty', token_hash=NULL, created=NULL, last_seen=NULL "
                            "WHERE slot=?", (slot,))

    def expired(self) -> list[int]:
        now = time.time()
        with self.lock:
            rows = self.db.execute("SELECT slot FROM slots WHERE state='leased' AND (last_seen < ? OR created < ?)",
                                   (now - IDLE_S, now - MAX_S)).fetchall()
        return [r[0] for r in rows]

    def with_state(self, state: str) -> list[int]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT slot FROM slots WHERE state=?", (state,)).fetchall()]

    def mark(self, slot: int, state: str) -> None:
        with self.lock:
            self.db.execute("UPDATE slots SET state=? WHERE slot=? AND state<>'leased'", (state, slot))

    def counts(self) -> dict[str, int]:
        with self.lock:
            rows = self.db.execute("SELECT state, count(*) FROM slots GROUP BY state").fetchall()
        return {"total": len(SLOTS)} | {state: n for state, n in rows}


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def slot_url(slot: int) -> str:
    return f"http://127.0.0.1:{PORT_BASE + slot}"


store = Store(STATE)
client: httpx.AsyncClient
recent: dict[int, deque] = defaultdict(deque)        # requests per slot in the last minute
last_worker_launch: dict[int, float] = {}
resetting: set[int] = set()


# ------------------------------------------------------------------ slot operations

async def reset_slot(slot: int) -> bool:
    """Reseed a slot to the demo's starting state through its own dev API."""
    if slot in resetting:
        return False
    resetting.add(slot)
    try:
        base = slot_url(slot)
        token = (await client.post(base + "/v1/dev/sessions", json={"member_id": "mem_parent_a"}, timeout=30)).json()["token"]
        r = await client.post(base + "/v1/dev/reset", headers={"Authorization": f"Bearer {token}"}, timeout=120)
        return r.status_code == 200
    except (httpx.HTTPError, KeyError, ValueError):
        return False
    finally:
        resetting.discard(slot)


async def janitor() -> None:
    """Free expired leases and reseed free slots, so the next visitor starts clean."""
    while True:
        try:
            for slot in store.expired():
                store.release(slot)
                recent.pop(slot, None)
            for slot in store.with_state("dirty"):
                if await reset_slot(slot):
                    store.mark(slot, "clean")
        except Exception as exc:   # the janitor must outlive any one failure; the next pass retries
            print("janitor:", repr(exc), flush=True)
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5), follow_redirects=False)
    task = asyncio.create_task(janitor())
    yield
    task.cancel()
    await client.aclose()


app = FastAPI(title="Proof Layer review gateway", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def refuse(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"code": code, "message": message} | extra, status_code=status,
                        headers={"Cache-Control": "no-store"})


def lease_view(slot: int, created: float, last_seen: float) -> dict[str, Any]:
    return {"expires_at": min(created + MAX_S, last_seen + IDLE_S), "ends_at_latest": created + MAX_S,
            "idle_minutes": IDLE_S / 60, "synthetic": True}


# ------------------------------------------------------------------ routes

@app.post("/v1/workspace")
async def start_workspace(request: Request):
    """Give this browser a private workspace, or say why not."""
    lease = store.lease_of(request.cookies.get(COOKIE))
    if lease is not None and time.time() - lease[2] < IDLE_S and time.time() - lease[1] < MAX_S:
        store.touch(lease[0])
        return JSONResponse(lease_view(*lease) | {"new": False}, headers={"Cache-Control": "no-store"})
    ip = client_ip(request)
    if store.starts_last_hour(ip) >= NEW_PER_IP_PER_HOUR:
        return refuse(429, "TOO_MANY_WORKSPACES", "Too many new review workspaces from this address in the last hour. "
                      "Try again later.", retry_after_s=600)
    token = secrets.token_urlsafe(32)
    claimed = store.claim(token, ip)
    if claimed is None:
        return refuse(503, "ALL_WORKSPACES_BUSY", "Every review workspace is in use right now. Try again in a few "
                      "minutes.", retry_after_s=120)
    slot, state = claimed
    if state != "clean" and not await reset_slot(slot):
        store.release(slot)
        return refuse(503, "WORKSPACE_NOT_READY", "A review workspace couldn't be prepared. Try again shortly.")
    now = time.time()
    response = JSONResponse(lease_view(slot, now, now) | {"new": True}, status_code=201,
                            headers={"Cache-Control": "no-store"})
    response.set_cookie(COOKIE, token, max_age=int(MAX_S), httponly=True, secure=SECURE_COOKIE, samesite="lax",
                        path="/")
    return response


@app.get("/healthz")
async def health():
    """Database, each slot's API and each worker's heartbeat. 503 when any is down."""
    db_ok: Optional[bool] = None
    if PG_DSN:
        def ping() -> bool:
            import psycopg
            try:
                with psycopg.connect(PG_DSN, connect_timeout=3) as conn:
                    return conn.execute("SELECT 1").fetchone()[0] == 1
            except Exception:
                return False
        db_ok = await asyncio.to_thread(ping)
    slots = []
    for slot in SLOTS:
        try:
            api = (await client.get(slot_url(slot) + "/v1/dev/accounts", timeout=3)).status_code == 200
        except httpx.HTTPError:
            api = False
        worker = None
        if HEARTBEATS is not None:
            beat = HEARTBEATS / f"worker-{slot}"
            worker = beat.exists() and time.time() - beat.stat().st_mtime < HEARTBEAT_STALE_S
        slots.append({"slot": slot, "api": api, "worker": worker})
    ok = db_ok is not False and all(s["api"] and s["worker"] is not False for s in slots)
    return JSONResponse({"status": "ok" if ok else "degraded", "database": db_ok, "slots": slots,
                         "workspaces": store.counts(), "checked_at": time.time()},
                        status_code=200 if ok else 503, headers={"Cache-Control": "no-store"})


@app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
@app.api_route("/architecture{rest:path}", methods=["GET", "HEAD"])
async def proxy(request: Request, rest: str = ""):
    path = request.url.path
    if path in BLOCKED:
        return refuse(403, "DISABLED_IN_REVIEW", BLOCKED[path])
    lease = store.lease_of(request.cookies.get(COOKIE))
    if lease is None:
        return refuse(401, "NO_WORKSPACE", "Start a review workspace first.")
    slot, created, last_seen = lease
    now = time.time()
    if now - last_seen >= IDLE_S or now - created >= MAX_S:
        store.release(slot)
        return refuse(401, "WORKSPACE_EXPIRED", "This review workspace has ended. Start a fresh one.")
    window = recent[slot]
    while window and window[0] < now - 60:
        window.popleft()
    if len(window) >= REQUESTS_PER_MINUTE:
        return refuse(429, "SLOW_DOWN", "Too many requests from this workspace. Wait a moment.", retry_after_s=30)
    window.append(now)
    if path == "/v1/dev/video/worker" and request.method == "POST":
        if now - last_worker_launch.get(slot, 0) < WORKER_LAUNCH_EVERY_S:
            return refuse(429, "WORKER_ALREADY_STARTED", "The worker was just started for this workspace.",
                          retry_after_s=WORKER_LAUNCH_EVERY_S)
        last_worker_launch[slot] = now
    store.touch(slot)
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP and k.lower() != "cookie"]
    others = "; ".join(f"{k}={v}" for k, v in request.cookies.items() if k != COOKIE)
    if others:
        headers.append(("cookie", others))
    headers.append(("x-forwarded-proto", request.headers.get("x-forwarded-proto", request.url.scheme)))
    upstream = client.build_request(request.method, slot_url(slot) + path, params=request.query_params,
                                    headers=headers, content=request.stream())
    try:
        resp = await client.send(upstream, stream=True)
    except httpx.HTTPError:
        return refuse(502, "WORKSPACE_UNAVAILABLE", "This review workspace isn't answering. Try again shortly.")
    out = [(k, v) for k, v in resp.headers.multi_items() if k.lower() not in HOP]

    async def body():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
    response = StreamingResponse(body(), status_code=resp.status_code)
    response.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in out]
    return response


@app.api_route("/metrics", methods=["GET"])
@app.api_route("/docs", methods=["GET"])
@app.api_route("/redoc", methods=["GET"])
@app.api_route("/openapi.json", methods=["GET"])
async def hidden():
    return refuse(404, "NOT_FOUND", "Not found.")


# ------------------------------------------------------------------ the static frontend, for direct access

@app.get("/")
async def landing():
    return FileResponse(LANDING) if LANDING.is_file() else FileResponse(STATIC / "index.html")


@app.get("/app")
async def frontend():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/privacy")
async def privacy():
    return FileResponse(STATIC / "privacy.html")


@app.get("/static/{name:path}")
async def static(name: str):
    target = (STATIC / name).resolve()
    if STATIC.resolve() not in target.parents or not target.is_file():
        return Response(status_code=404)
    return FileResponse(target, headers={"Cache-Control": "no-cache"})
