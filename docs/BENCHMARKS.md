# Benchmarks

Measured on one laptop on 2026-10-10. These are bounded local measurements of this prototype. They are not capacity claims for production, and say nothing about Nanit's systems. Raw results with full environment records are in `artifacts/benchmarks/*.json`.

## Environment

| Item | Value |
|---|---|
| Machine | AMD Ryzen 9 5900HX, 16 logical CPUs, 15.4 GB RAM, Windows 11 |
| Software | Python 3.13.2, PostgreSQL 17.8 (local, trust auth), FastAPI 0.143, uvicorn 0.54 |
| Database | `proof_layer_bench`, isolated from the demo |
| Load driver | `bench/load.py`: one Python asyncio + httpx process on the same machine |
| Noise | Four research agents and the demo server were running at the same time. Total CPU sampled at 63–69% during the two-driver run. |

## Dataset (synthetic, seed 20261010)

`python -m bench.generate --profile intermediate`

| Households | Members | Windows (nights) | Evidence rows | Revisions | Corrections | Late arrivals | DB size | Time |
|---|---|---|---|---|---|---|---|---|
| 10,000 | 20,000 | 70,000 | 417,864 | 73,236 | 3,236 | 690 | 331.5 MB | 68.4 s |

The 1,000-household profile measured 41.9k evidence rows, 41.8 MB and 6.3 s. The 100,000-household profile was not run at first, because the system drive had 3.5 GB free and the generator refuses to leave less than 2 GB. It was run later, once space was available (below). Facts and fingerprints come from the app's own `briefing.compute_facts`, so the stored revisions are what the API would write.

## Results

| Run | Workers | Concurrency | Requests | req/s | p50 ms | p95 ms | p99 ms | Errors | Invariant |
|---|---|---|---|---|---|---|---|---|---|
| read_burst, **before fix** | 1 | 32 | 3,000 | 27.8 | 567 | 2,507 | **30,557** | **32 × 500** | — |
| read_burst, serial | 1 | 1 | 300 | 59.4 | 16.1 | 20.8 | 23.2 | 0 | — |
| read_burst, after fix | 1 | 32 | 3,000 | 44.3 | 520 | 1,929 | 2,825 | 0 | — |
| read_burst | 4 | 32 | 3,000 | 52.0 | 427 | 1,654 | 2,508 | 0 | — |
| read_burst, two drivers | 4 | 2 × 16 | 2 × 1,500 | 64.5 total | 301–313 | — | 2,250–2,383 | 0 | — |
| mixed (90% read, 10% ack) | 4 | 32 | 2,000 | 41.6 | 527 | 2,155 | 3,292 | 0 | one ack per member and revision: **held** |
| correction_storm (one window) | 4 | 50 | 50 | 39.1 | 584 | 835 | 869 | 0 | 1 × 200, 49 × 409, exactly one new revision: **held** |
| duplicate_purchase (one key) | 4 | 20 | 20 | 31.9 | 464 | 538 | 538 | 0 | 1 × 201, 19 replays, one intent, one submit job: **held** |

### Re-measured after migration 0003 (plans per camera), machine otherwise idle

| Run | Workers | Concurrency | Requests | req/s | p50 ms | p99 ms | Errors | Invariant |
|---|---|---|---|---|---|---|---|---|
| read_burst | 4 | 32 | 3,000 | 88.9 | 251 | 1,414 | 0 | — |
| duplicate_purchase (one key, camera claims) | 4 | 20 | 20 | — | — | — | 0 | 1 × 201, 19 replays, one intent, one job: **held** |

The read burst is 71% faster than the 52 req/s measured earlier in the day, with the same code path for reads. The difference is the four research agents that were sharing the CPU then. That gap is why these numbers are relative, not capacity claims.

### Experiment health (synthetic, `python -m bench.experiment_check`)

10,000 benchmark households assigned with the app's sticky assignment, 90% rendering, and one variant losing 6% of its receipts to an injected bug:

| Check | Counts | χ² | p | Verdict |
|---|---|---|---|---|
| Assignment | 5,042 / 4,958 | 0.71 | 0.40 | balanced |
| Exposure | 4,532 / 4,170 | 15.06 | 0.0001 | **mismatch: do not read the outcome** |

## At 100,000 households (proof gate, 2026-10-10 evening)

Full audit: [audits/PERFORMANCE_AND_SCALE.md](../audits/PERFORMANCE_AND_SCALE.md). Dataset: `python -m bench.generate --profile large --history-nights 730`.

| Dataset | Value |
|---|---|
| Households / nights | 100,000 / 300,727 (one household has 730 nights) |
| Evidence rows / revisions | 1,794,197 / 314,992 |
| Size / build time | 1,467.5 MB / 179.4 s |

| Run (4 server processes) | Result | Invariant |
|---|---|---|
| Morning burst, 32 concurrent | 37.1 req/s cold, 65.3 req/s warm; p99 3.6 s cold, 1.9 s warm; 0 errors | — |
| Same-household contention, 16 concurrent | 22 corrections won, 59 told to refresh; p99 1.4 s | gap-free revisions, ack heads correct: **held** |
| Reconnect storm, 200 concurrent, no backoff | 0 lost; p99 16.9 s (queueing, no load shedding) | **held** |
| Week view, 730-night vs 3-night household | p50 146 vs 150 ms | **held** |
| 2,000 purchases queued, then 4 workers | drained in 15.0 s (133 jobs/s), 503–508 jobs each | one provider op per intent, 1 attempt: **held** |
| Soak, 10 min, 4 processes | 30,400 requests, p50 160 ms; memory +2.4%; **17 requests never answered within 60 s** | **failed** |
| Soak, 5 min, 1 process (diagnostic) | 27,200 requests, 87.5 req/s, max 4.0 s, 0 timeouts | **held** |
| Soak, 10 min, 1 process (like-for-like control) | 54,400 requests, 89.1 req/s, max 1.6 s, 0 timeouts, memory 94 → 96 MB | **held** |

**Cause: not established.** What was measured:
- 0 lock waits and 0 slow queries during the stalls;
- no timeouts with one process on the same machine;
- on this machine, one process was faster than four.

This points away from the application code and the database. It does not prove the mechanism, and it does not show that Windows or uvicorn is responsible. That needs a comparable follow-up: the same four-process soak on another OS or another server, which has not been run. The soak stays a failed release gate. Details are in the audit.

**Query plans.**
- All 46 captured queries stay under 1.2 ms.
- No sequential scan touches a large table (`explain-large-100k-*.json`).

**Optimization.** The outbox claim went from 356 ms to 0.026 ms with 1,000,000 finished jobs, after a partial index (migration 0004). It claims the same job, and writes cost 26% more (`optimization-outbox-claim-*.json`).

**Noise.** An unrelated local `llama-server` process was running, and CPU load was sampled at 13–24% around the runs.

## The measured bottleneck and the fix

**Symptom.** At concurrency 32, 1.07% of reads failed after exactly 30 s.

**Cause.** The server log showed SQLAlchemy `QueuePool` timeouts. Every request held one pooled connection, then opened a second one to write its authorization audit record. With 15 pool slots, 32 concurrent requests each held a slot while waiting for another. That is pool starvation, a resource deadlock that resolves only by timeout.

**Rule broken.** A request may hold at most one application connection at a time.

**Fix (`app/db.py`, `keep()`).** Audit records and server analytics now ride on the request's own connection: inside its transaction on success, or right after its rollback on failure, so denials are still recorded. The pool is 10 + 10.

**Proof.**
- `tests/test_connection_budget.py` counts connection checkouts for allowed and denied reads and writes. It fails on the old code and passes now.
- Re-measured: 0 errors, and p99 fell from 30.6 s to 2.8 s.

## Reading the numbers honestly

- **Serial service time is about 16 ms.** Profiling puts about 7 ms in the handler: five indexed queries plus the audit commit, each about 1 ms round-trip. The rest is HTTP and framework overhead.
- **Latency above that under concurrency is queueing.** One process saturates near 45–60 req/s.
- **Four worker processes raised throughput only modestly** (52 req/s), and a second driver raised the total to 64.5 req/s. So the single-process driver is part of the ceiling, and the machine was shared with other work.
- **Next step.** Run the driver on a separate machine, or use k6, before drawing any capacity conclusion.
- **Not measured in this first pass:** lock waits, worker backlog and soak. The 100k section above adds lock-wait samples, a worker backlog and a soak. Transaction duration distribution and browser timing are still not measured.

## Reproduce

```bash
cd proof-layer
python -m bench.generate --profile intermediate          # seeded data in proof_layer_bench
python -m bench.serve 8721 4                              # isolated server, 4 workers
BENCH_SERVER_WORKERS=4 python -m bench.load --base http://127.0.0.1:8721 --scenario read_burst --concurrency 32 --requests 3000
python -m bench.load --base http://127.0.0.1:8721 --scenario correction_storm --concurrency 50 --requests 50
python -m bench.load --base http://127.0.0.1:8721 --scenario duplicate_purchase --concurrency 20 --requests 20
```
