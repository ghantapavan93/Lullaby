"""Run the gateway and N workspace slots on this machine, the way the VM runs
them under systemd: one database pair, app process, worker and media folder
per slot, and the gateway in front. For tests and local trials.

    python deploy/gateway/stack.py up --slots 2 --port 8800   # prints the URL
    python deploy/gateway/stack.py down

Everything it creates is named with a run prefix and recorded in a state file,
so `down` removes exactly what `up` made: processes, databases, folders.
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
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "proof-layer"
sys.path.insert(0, str(APP / "scripts"))
import devdb  # noqa: E402

HOME = REPO / "local-media" / "gateway-stack"
STATE = HOME / "stack.json"
DB = "postgresql+psycopg://proof@127.0.0.1:" + devdb.PORT + "/{}"
FLAGS = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0


def _psql(sql: str) -> None:
    devdb.run(devdb.pg_bin("psql"), "-h", "127.0.0.1", "-p", devdb.PORT, "-U", "proof", "-d", "postgres", "-c", sql)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn(args: list[str], env: dict, log: Path, cwd: Path) -> int:
    with open(log, "ab") as out:
        return subprocess.Popen(args, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=FLAGS, start_new_session=os.name != "nt").pid


def _wait(url: str, ok=(200,), seconds: float = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code in ok:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} didn't come up")


def up(slots: int = 2, port: int = 0, idle_minutes: float = 30, max_minutes: float = 120,
       per_ip: int = 6, extra_env: dict | None = None) -> dict:
    if STATE.exists():
        down()
    run = "gw" + secrets.token_hex(3)
    home = HOME / run
    (home / "beats").mkdir(parents=True, exist_ok=True)
    port_base = _free_port() - 1          # slot n listens on port_base + n
    state = {"run": run, "home": str(home), "pids": [], "databases": [], "port_base": port_base}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state), encoding="utf-8")
    base_env = {k: v for k, v in os.environ.items() if k not in ("PROOF_LAYER_SECRET", "PROOF_LAYER_SAMPLE")}
    for n in range(1, slots + 1):
        app_db, prov_db = f"proof_layer_{run}_{n}", f"provider_sim_{run}_{n}"
        for db in (app_db, prov_db):
            _psql(f"create database {db}")
            state["databases"].append(db)
        STATE.write_text(json.dumps(state), encoding="utf-8")
        media = home / f"slot{n}"
        media.mkdir(exist_ok=True)
        env = base_env | {"DATABASE_URL": DB.format(app_db), "PROVIDER_DATABASE_URL": DB.format(prov_db),
                          "PROOF_LAYER_DEV": "1", "PROOF_LAYER_SAMPLE": "staged", "PROOF_LAYER_MEDIA_ROOT": str(media),
                          "PROOF_LAYER_LEASE_SECONDS": "3",
                          "PROOF_LAYER_HEARTBEAT": str(home / "beats" / f"worker-{n}")}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=APP, env=env, check=True,
                       capture_output=True)
        subprocess.run([sys.executable, "-m", "app.seed"], cwd=APP, env=env, check=True, capture_output=True)
        state["pids"].append(_spawn([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                     "--port", str(port_base + n)], env, home / f"slot{n}.log", APP))
        state["pids"].append(_spawn([sys.executable, "-m", "app.worker", "--id", f"slot{n}"], env,
                                    home / f"worker{n}.log", APP))
        STATE.write_text(json.dumps(state), encoding="utf-8")
    for n in range(1, slots + 1):
        _wait(f"http://127.0.0.1:{port_base + n}/v1/dev/accounts")
    gw_port = port or _free_port()
    gw_env = base_env | {"GATEWAY_SLOTS": ",".join(str(n) for n in range(1, slots + 1)),
                         "GATEWAY_PORT_BASE": str(port_base), "GATEWAY_STATE": str(home / "gateway.sqlite3"),
                         "GATEWAY_IDLE_MINUTES": str(idle_minutes), "GATEWAY_MAX_MINUTES": str(max_minutes),
                         "GATEWAY_NEW_PER_IP_PER_HOUR": str(per_ip), "GATEWAY_SECURE_COOKIE": "0",
                         "GATEWAY_HEARTBEATS": str(home / "beats"),
                         "GATEWAY_PG_DSN": f"postgresql://proof@127.0.0.1:{devdb.PORT}/postgres"} | (extra_env or {})
    state |= {"url": f"http://127.0.0.1:{gw_port}", "slots": slots, "gateway_port": gw_port, "gateway_env": gw_env}
    start_gateway(state)
    return state


def start_gateway(state: dict) -> None:
    """Start (or restart) only the gateway; its leases are in SQLite, so they survive."""
    if state.get("gateway_pid"):
        kill(state["gateway_pid"])
    pid = _spawn([sys.executable, "-m", "uvicorn", "gateway:app", "--host", "127.0.0.1",
                  "--port", str(state["gateway_port"])], state["gateway_env"], Path(state["home"]) / "gateway.log",
                 Path(__file__).parent)
    state["gateway_pid"] = pid
    state["pids"].append(pid)
    STATE.write_text(json.dumps(state), encoding="utf-8")
    _wait(state["url"] + "/healthz", ok=(200, 503))


def kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(pid, 9)
        except OSError:
            pass


def down() -> None:
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for pid in state.get("pids", []):
        kill(pid)
    for db in state.get("databases", []):
        try:
            _psql(f"drop database if exists {db} with (force)")
        except subprocess.CalledProcessError:
            pass
    home = Path(state.get("home", ""))
    if home.is_dir() and HOME in home.parents:
        shutil.rmtree(home, ignore_errors=True)
    STATE.unlink(missing_ok=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("up")
    u.add_argument("--slots", type=int, default=2)
    u.add_argument("--port", type=int, default=0)
    sub.add_parser("down")
    a = ap.parse_args()
    if a.cmd == "up":
        s = up(a.slots, a.port)
        print(s["url"])
    else:
        down()
        print("down")
