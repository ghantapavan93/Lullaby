#!/usr/bin/env bash
# The bounded benchmark suite used for audits/PERFORMANCE_AND_SCALE.md.
# Prerequisite: python -m bench.generate --profile large --history-nights 730
# Runs on the isolated bench databases only. Each step writes its own JSON to
# artifacts/benchmarks/. Expect about 25 minutes, most of it the 10-minute soak.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/Scripts/python}"
PORT=8721
BASE="http://127.0.0.1:$PORT"
export BENCH_SERVER_WORKERS=4

"$PY" -m bench.serve "$PORT" "$BENCH_SERVER_WORKERS" &
SERVER=$!
stop_server() {   # uvicorn's worker processes outlive the launcher on Windows; stop whatever owns the port
  powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue | ForEach-Object { taskkill /F /T /PID \$_.OwningProcess }" >/dev/null 2>&1 || true
  kill $SERVER 2>/dev/null || true
}
trap stop_server EXIT
for _ in $(seq 1 60); do curl -sf "$BASE/robots.txt" >/dev/null && break; sleep 1; done

run() { echo "== $*"; "$PY" -m bench.load --base "$BASE" "$@" | tail -n 40; }

run --scenario read_burst          --concurrency 32  --requests 3000 --label large-100k
run --scenario household_contention --concurrency 16 --requests 400  --label large-100k
run --scenario reconnect_storm     --concurrency 200 --requests 2000 --label large-100k
run --scenario large_history       --concurrency 16  --requests 300  --label large-100k
run --scenario purchase_backlog    --concurrency 32  --requests 2000 --workers 4 --label large-100k
"$PY" -m bench.explain --label large-100k-after-backlog | tail -n 12
"$PY" -m bench.outbox_index --history 1000000 --pending 200
run --scenario soak                --concurrency 16  --duration 600 --label large-100k
