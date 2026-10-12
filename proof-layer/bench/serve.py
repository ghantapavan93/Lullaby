"""Run the API against the benchmark databases for load tests.

    python -m bench.serve                 # http://127.0.0.1:8720, one worker
    python -m bench.serve 8721 4          # port 8721, four worker processes

Uses a fixed benchmark-only token secret so bench/load.py can sign tokens
for synthetic members. Never point this at the demo or a shared database.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    env = os.environ | {
        "DATABASE_URL": "postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_bench",
        "PROVIDER_DATABASE_URL": "postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_bench",
        "PROOF_LAYER_SECRET": "bench-only-secret",
        "PROOF_LAYER_DEV": "0",
    }
    port = sys.argv[1] if len(sys.argv) > 1 else "8720"
    workers = sys.argv[2] if len(sys.argv) > 2 else "1"
    sys.exit(subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                              "--port", port, "--workers", workers, "--log-level", "warning"], cwd=ROOT, env=env))
