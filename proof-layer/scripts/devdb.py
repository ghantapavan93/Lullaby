"""Private PostgreSQL cluster for local development and tests.

    python scripts/devdb.py up             # init (once), start, create databases, migrate, seed
    python scripts/devdb.py up video       # also a separate set for a second checkout (proof_layer_video,
                                           # provider_sim_video and their _test pair); pairs with demo.py <port> video
    python scripts/devdb.py status
    python scripts/devdb.py reset [video]  # reseed the demo database
    python scripts/devdb.py down

The cluster lives outside the repository (``PROOF_LAYER_PGDATA``, default
``~/.nanit-proof-layer/pgdata``; on this machine it was created under
%LOCALAPPDATA%), listens on 127.0.0.1:54329 only, and uses trust
authentication for the local user ``proof``. It is a development fixture,
never a deployment.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = "54329"
DATABASES = ("proof_layer", "provider_sim", "proof_layer_test", "provider_sim_test")


def databases(suffix: str | None) -> tuple[str, ...]:
    if not suffix:
        return DATABASES
    if not suffix.isidentifier() or not suffix.islower():
        sys.exit("the suffix must be lowercase letters, digits or underscores")
    return (f"proof_layer_{suffix}", f"provider_sim_{suffix}", f"proof_layer_{suffix}_test", f"provider_sim_{suffix}_test")


def env_for(suffix: str | None) -> dict[str, str]:
    """The demo databases for this suffix, as scripts/demo.py sets them."""
    if not suffix:
        return dict(os.environ)
    return os.environ | {"DATABASE_URL": f"postgresql+psycopg://proof@127.0.0.1:{PORT}/proof_layer_{suffix}",
                         "PROVIDER_DATABASE_URL": f"postgresql+psycopg://proof@127.0.0.1:{PORT}/provider_sim_{suffix}"}


def _default_data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    legacy = Path(local) / "nanit-proof-layer" / "pgdata" if local else None
    if legacy and legacy.exists():
        return legacy
    return Path.home() / ".nanit-proof-layer" / "pgdata"


DATA = Path(os.environ.get("PROOF_LAYER_PGDATA", _default_data_dir()))


def pg_bin(name: str) -> str:
    candidates = [os.environ.get("PG_BIN", ""), r"C:\Program Files\PostgreSQL\17\bin",
                  r"C:\Program Files\PostgreSQL\16\bin", "/usr/lib/postgresql/17/bin", "/opt/homebrew/bin"]
    for folder in candidates:
        exe = Path(folder) / (name + (".exe" if os.name == "nt" else ""))
        if folder and exe.exists():
            return str(exe)
    found = shutil.which(name)
    if not found:
        sys.exit(f"{name} not found: install PostgreSQL 16+ or set PG_BIN")
    return found


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


def running() -> bool:
    return run(pg_bin("pg_isready"), "-h", "127.0.0.1", "-p", PORT, check=False).returncode == 0


def up(suffix: str | None = None) -> None:
    if not (DATA / "PG_VERSION").exists():
        DATA.mkdir(parents=True, exist_ok=True)
        run(pg_bin("initdb"), "-D", str(DATA), "-U", "proof", "-A", "trust", "-E", "UTF8", "--locale=C")
        print(f"initialized cluster at {DATA}")
    if not running():
        # -l keeps postgres from holding this process's stdout open.
        run(pg_bin("pg_ctl"), "-D", str(DATA), "-l", str(DATA.parent / "postgres.log"), "-w",
            "-o", f"-p {PORT} -c listen_addresses=127.0.0.1", "start")
    existing = run(pg_bin("psql"), "-h", "127.0.0.1", "-p", PORT, "-U", "proof", "-d", "postgres", "-Atc",
                   "select datname from pg_database").stdout.split()
    for db in databases(suffix):
        if db not in existing:
            run(pg_bin("psql"), "-h", "127.0.0.1", "-p", PORT, "-U", "proof", "-d", "postgres", "-c",
                f"create database {db}")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True, env=env_for(suffix))
    reset(suffix)
    print("database ready on 127.0.0.1:54329" + (f" ({', '.join(databases(suffix))})" if suffix else ""))


def reset(suffix: str | None = None) -> None:
    subprocess.run([sys.executable, "-m", "app.seed"], cwd=ROOT, check=True, env=env_for(suffix))


def down() -> None:
    if running():
        run(pg_bin("pg_ctl"), "-D", str(DATA), "-m", "fast", "stop")
    print("stopped")


def status() -> None:
    print(f"data dir: {DATA}")
    print("running" if running() else "not running")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    extra = sys.argv[2:3]
    if command in ("up", "reset"):
        {"up": up, "reset": reset}[command](*extra)
    else:
        {"down": down, "status": status}[command]()
