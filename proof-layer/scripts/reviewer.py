"""Isolated reviewer instances: one per reviewer, each with its own databases,
process, signing key, media folder and port, and a hard expiry.

    python scripts/reviewer.py create [--minutes 60]   # prints the URL
    python scripts/reviewer.py list
    python scripts/reviewer.py destroy <id>
    python scripts/reviewer.py reap                    # destroy every expired instance

Why instances and not tenants inside one database: the demo's synthetic ids
are shared constants, its reset truncates every table, and its append-only
tables refuse deletes. Separate databases and processes give isolation that
doesn't depend on any of that being right. What one reviewer does (a reset, a
scenario, a report) can't reach another; a token signed by one instance is
meaningless to another; a clip stored by one is not in another's folder.

Limits, on purpose:
- Each instance listens on 127.0.0.1 only. Making one reachable from outside
  is a deployment decision this script does not make.
- Dev controls are on inside an instance (that is the demo), but act only on
  that instance's own synthetic data.
- The real CC0 clip is never used: the sample is forced to the staged clip.

The registry lives in git-ignored local-media/reviewers/registry.json.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
import devdb  # noqa: E402

HOME = ROOT.parent / "local-media" / "reviewers"
REGISTRY = HOME / "registry.json"
DB_URL = "postgresql+psycopg://proof@127.0.0.1:" + devdb.PORT + "/{}"


def _load() -> dict:
    try:
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(reg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=1), encoding="utf-8")
    tmp.replace(REGISTRY)


def _psql(sql: str) -> None:
    devdb.run(devdb.pg_bin("psql"), "-h", "127.0.0.1", "-p", devdb.PORT, "-U", "proof", "-d", "postgres", "-c", sql)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(rid: str, media: Path, expires_at: float) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PROOF_LAYER_SECRET", "PROOF_LAYER_SAMPLE")}
    return env | {"DATABASE_URL": DB_URL.format(f"proof_layer_{rid}"),
                  "PROVIDER_DATABASE_URL": DB_URL.format(f"provider_sim_{rid}"),
                  "PROOF_LAYER_DEV": "1", "PROOF_LAYER_SAMPLE": "staged", "PROOF_LAYER_MEDIA_ROOT": str(media),
                  "PROOF_LAYER_EXPIRES_AT": str(expires_at), "PROOF_LAYER_LEASE_SECONDS": "3"}


def create(minutes: float = 60, wait_s: float = 60) -> dict:
    if not devdb.running():
        raise SystemExit("PostgreSQL isn't running: python scripts/devdb.py up")
    rid = "rv" + secrets.token_hex(4)
    expires_at = time.time() + minutes * 60
    media = HOME / rid
    media.mkdir(parents=True, exist_ok=True)
    env = _env(rid, media, expires_at)
    entry = {"id": rid, "databases": [f"proof_layer_{rid}", f"provider_sim_{rid}"], "media_root": str(media),
             "created_at": datetime.now(timezone.utc).isoformat(),
             "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(), "pid": None}
    reg = _load()
    reg[rid] = entry
    _save(reg)                                   # recorded first, so a failed start can still be destroyed
    for db in entry["databases"]:
        _psql(f"create database {db}")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True,
                   capture_output=True)
    subprocess.run([sys.executable, "-m", "app.seed"], cwd=ROOT, env=env, check=True, capture_output=True)
    port = _free_port()
    log = open(media / "server.log", "ab")
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            creationflags=flags, start_new_session=os.name != "nt")
    log.close()                                  # the server has its own handle; ours would block cleanup on Windows
    entry |= {"pid": proc.pid, "port": port, "url": f"http://127.0.0.1:{port}"}
    reg = _load()
    reg[rid] = entry
    _save(reg)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            # 410 is a live server past its expiry: started, just already over.
            if httpx.get(entry["url"] + "/v1/dev/accounts", timeout=2).status_code in (200, 410):
                return entry
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    destroy(rid)
    raise SystemExit(f"instance {rid} didn't start; destroyed")


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(pid, 9)
        except OSError:
            pass


def destroy(rid: str) -> bool:
    reg = _load()
    entry = reg.get(rid)
    if entry is None:
        return False
    if entry.get("pid"):
        _kill(entry["pid"])
    for db in entry["databases"]:
        try:
            _psql(f"drop database if exists {db} with (force)")
        except subprocess.CalledProcessError:
            pass
    media = Path(entry["media_root"])
    if media.is_dir() and HOME in media.parents:   # never remove anything outside the reviewers folder
        shutil.rmtree(media, ignore_errors=True)
    reg = _load()
    reg.pop(rid, None)
    _save(reg)
    return True


def reap(now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    gone = [rid for rid, e in _load().items() if datetime.fromisoformat(e["expires_at"]) <= now]
    for rid in gone:
        destroy(rid)
    return gone


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--minutes", type=float, default=60)
    sub.add_parser("list")
    d = sub.add_parser("destroy")
    d.add_argument("id")
    sub.add_parser("reap")
    a = ap.parse_args()
    if a.cmd == "create":
        e = create(a.minutes)
        print(f"{e['id']}  {e['url']}  until {e['expires_at']}")
    elif a.cmd == "list":
        for e in _load().values():
            print(f"{e['id']}  {e.get('url', '-')}  until {e['expires_at']}  pid {e.get('pid')}")
    elif a.cmd == "destroy":
        print("destroyed" if destroy(a.id) else "no such instance")
    else:
        print("reaped:", ", ".join(reap()) or "none")
