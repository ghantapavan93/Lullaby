"""Run the whole Proof Layer in one container, for Render's free web service.

Render's free tier gives one web service (512 MB, a fraction of a CPU, no
background workers, no disk) and one free PostgreSQL database. This supervisor
fits the same pieces the VM runs under systemd into that one container:

1. the database: DATABASE_URL from Render. The simulated provider gets its own
   database when the role may create one; otherwise its own tables in the
   same database (the names don't overlap), and /healthz says which;
2. migrations, and the synthetic seed when the database is empty;
3. one workspace slot (app on 127.0.0.1:9101), its outbox worker, and the
   review gateway on $PORT, which gives one visitor at a time a private,
   expiring workspace;
4. restarts: a child that exits is started again; SIGTERM stops them all.

Media lives in /tmp: Render's free disk is ephemeral, so the generated demo
clip is made again after a restart. Nothing here is a secret: each app
process signs with its own random key.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "proof-layer"
GATEWAY = ROOT / "deploy" / "gateway"
DATA = Path(os.environ.get("RENDER_DATA", "/tmp/proof-layer"))
SLOT_PORT = 9101


def sqlalchemy_url(url: str) -> str:
    """Render hands out postgres:// or postgresql://; the app's driver is psycopg."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def libpq_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def provider_database(app_url: str) -> tuple[str, str]:
    """A separate provider database when allowed; the same one otherwise."""
    import psycopg
    base, _, query = app_url.partition("?")
    head, _, dbname = base.rpartition("/")
    provider = f"{dbname}_provider"
    try:
        with psycopg.connect(libpq_url(app_url), autocommit=True, connect_timeout=10) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (provider,)).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{provider}"')
        return f"{head}/{provider}" + (f"?{query}" if query else ""), "separate database"
    except Exception as exc:                       # no CREATEDB on this plan: share, with distinct tables
        print("provider database: sharing the app database:", type(exc).__name__, flush=True)
        return app_url, "same database, separate tables"


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", flush=True)
        return 1
    app_url = sqlalchemy_url(url)
    provider_url, boundary = provider_database(app_url)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "beats").mkdir(exist_ok=True)
    slot_env = os.environ | {
        "DATABASE_URL": app_url, "PROVIDER_DATABASE_URL": provider_url,
        "PROOF_LAYER_DEV": "1", "PROOF_LAYER_SAMPLE": "staged", "PROOF_LAYER_HTTPS": "1",
        "PROOF_LAYER_LEASE_SECONDS": "3", "PROOF_LAYER_MEDIA_ROOT": str(DATA / "slot1"),
        "PROOF_LAYER_HEARTBEAT": str(DATA / "beats" / "worker-1")}
    slot_env.pop("PROOF_LAYER_SECRET", None)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=APP, env=slot_env, check=True)
    import psycopg
    with psycopg.connect(libpq_url(app_url), connect_timeout=10) as conn:
        members = conn.execute("SELECT count(*) FROM members").fetchone()[0]
    if members == 0:
        subprocess.run([sys.executable, "-m", "app.seed"], cwd=APP, env=slot_env, check=True)
    gateway_env = os.environ | {
        "GATEWAY_SLOTS": "1", "GATEWAY_PORT_BASE": str(SLOT_PORT - 1), "GATEWAY_STATE": str(DATA / "gateway.sqlite3"),
        "GATEWAY_HEARTBEATS": str(DATA / "beats"), "GATEWAY_PG_DSN": libpq_url(app_url),
        "GATEWAY_SECURE_COOKIE": "1", "GATEWAY_STATIC": str(APP / "static"),
        "GATEWAY_LANDING": str(ROOT / "deploy" / "landing" / "index.html"),
        "GATEWAY_HOST_NOTE": f"free host; provider: {boundary}",
        # One workspace only: release it sooner, so a visitor who walks away blocks no one for long.
        "GATEWAY_IDLE_MINUTES": os.environ.get("GATEWAY_IDLE_MINUTES", "10"),
        "GATEWAY_MAX_MINUTES": os.environ.get("GATEWAY_MAX_MINUTES", "45")}
    port = os.environ.get("PORT", "10000")
    commands = {
        "slot": ([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(SLOT_PORT)],
                 APP, slot_env),
        "worker": ([sys.executable, "-m", "app.worker", "--id", "slot1"], APP, slot_env),
        "gateway": ([sys.executable, "-m", "uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", port,
                     "--proxy-headers", "--forwarded-allow-ips", "*"], GATEWAY, gateway_env),
    }
    # On a tenth of a CPU, video analysis in the worker starved the API: page
    # requests waited past the gateway's 60 s and got 502 (live sweep,
    # 2026-10-12). Background work yields to the visitor's requests.
    background = {"worker", "warm"}

    def lower_priority() -> None:
        os.nice(10)

    def spawn(name: str, cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
        return subprocess.Popen(cmd, cwd=cwd, env=env, preexec_fn=lower_priority if name in background else None)

    children: dict[str, subprocess.Popen] = {}
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        for p in children.values():
            p.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # The demo clip takes about half a minute to encode on a tenth of a CPU: make it
    # once, in the background, so a visitor's scenario finds it ready (Render's
    # disk is ephemeral, so this runs after every start).
    warm = spawn("warm", [sys.executable, "-c", "from app import video_evidence; video_evidence.sample_source()"],
                 APP, slot_env)
    while not stopping:
        if warm is not None and warm.poll() is not None:
            print("demo clip ready" if warm.returncode == 0 else f"demo clip failed ({warm.returncode})", flush=True)
            warm = None
        for name, (cmd, cwd, env) in commands.items():
            p = children.get(name)
            if p is None or p.poll() is not None:
                if p is not None:
                    print(f"{name} exited with {p.returncode}; starting it again", flush=True)
                children[name] = spawn(name, cmd, cwd, env)
        time.sleep(2)
    for p in children.values():
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
