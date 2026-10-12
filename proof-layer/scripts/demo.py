"""Start the demo server with scenario controls enabled.

    python scripts/demo.py            # http://127.0.0.1:8710
    python scripts/demo.py 8720 video # port 8720, databases proof_layer_video / provider_sim_video

Scenario controls (/v1/dev/*) are off unless PROOF_LAYER_DEV=1; this script
sets it for the local demo only. Never set it on a shared host.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "8710"
    env = os.environ | {"PROOF_LAYER_DEV": "1", "PROOF_LAYER_LEASE_SECONDS": os.environ.get("PROOF_LAYER_LEASE_SECONDS", "3")}
    if len(sys.argv) > 2:   # a separate pair of databases, for a second checkout running side by side
        suffix = sys.argv[2]
        env |= {"DATABASE_URL": f"postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_{suffix}",
                "PROVIDER_DATABASE_URL": f"postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_{suffix}"}
    sys.exit(subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port],
                             cwd=ROOT, env=env))
