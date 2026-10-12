# Performance and scale

**Measured on one laptop, on synthetic data, on 2026-10-10.** These are bounded local measurements of this prototype. They are not capacity claims, and they say nothing about Nanit's systems.
- Every number links to a JSON file in `artifacts/benchmarks/`, which carries its environment.
- The canonical summary for readers of `docs/` is `docs/BENCHMARKS.md`; this page is the audit.

## Resources and cost (checked before running)

| Item | Value |
|---|---|
| CPU | AMD Ryzen 9 5900HX, 8 cores / 16 threads |
| RAM | 15.4 GB total; under 1 GB free while the research agents ran, more during the benchmarks |
| Disk | 76 GB free on C: before the 100k dataset; the PostgreSQL data directory is 2.6 GB |
| PostgreSQL | 17.8, local, default settings (`shared_buffers` 128 MB, `work_mem` 4 MB, `max_connections` 100) |
| Other load on the machine | Claude and ChatGPT desktop apps, Chrome, and an unrelated local `llama-server` process (2.6 GB, not part of this project, left running). CPU load was sampled around runs and is recorded below |
| Load tools | `bench/load.py`: Python asyncio + httpx, on the same machine. k6 and Locust are not installed; the driver was extended rather than adding a dependency |

| Cost class | Items | Amount |
|---|---|---|
| Already installed, free | PostgreSQL 17, Python 3.13, FastAPI stack, Playwright/Chromium, Hypothesis | $0 |
| Actual infrastructure spend | none; everything ran locally | **$0** |
| Optional, estimated, not used | A separate load-driver VM (for example 4 vCPU, a few hours) or a managed PostgreSQL instance would remove same-machine noise. Prices depend on provider and region and weren't quoted | not incurred |
| Unknown or billing-dependent | none | — |

**Not run, deliberately: the 1,000,000-household profile.**
- It is about 15 GB at the measured 4.9 KB per household-night.
- It would take about 30 minutes to build.
- Free RAM was under 1 GB at the time.
- It would prove storage behavior, which `DATABASE_AND_LINEAGE_REVIEW.md` already projects, not query behavior: query plans were index-bound at 100k.

The workload is defined (`python -m bench.generate --profile` with 1,000,000 households would need a new profile entry) and left **unmeasured**.

## Datasets actually generated

| Profile | Households | Nights | Evidence rows | Revisions | DB size | Built in | Record |
|---|---|---|---|---|---|---|---|
| dev | 1,000 | 7,000 | 41.9k | — | 41.8 MB | 6.3 s | `docs/BENCHMARKS.md` |
| intermediate | 10,000 | 70,000 | 417,864 | 73,236 | 331.5 MB | 68.4 s | `docs/BENCHMARKS.md` |
| **large (maximum generated)** | **100,000** (one with 730 nights) | **300,727** | **1,794,197** | **314,992** | **1,467.5 MB** | **179.4 s** | `dataset-large-20261010.json` |

Households are not users, and rows in storage are not active traffic. A million records stored says nothing about a million concurrent clients.

## Workloads run at 100,000 households

Server: `bench/serve.py`, 4 uvicorn worker processes, pool 10 + 10 each. Driver: one process.

| Scenario | Concurrency | Requests | req/s | p50 ms | p95 ms | p99 ms | Errors (5xx) | Invariant checked | Result |
|---|---|---|---|---|---|---|---|---|---|
| Morning burst (`read_burst`), cold, first run after the build | 32 | 3,000 | 37.1 | 604 | 2,279 | 3,575 | 0 | — | — |
| Morning burst, warm | 32 | 3,000 | 65.3 | 349 | 1,295 | 1,905 | 0 | — | — |
| Same-household contention: both caregivers read, ack and correct one night | 16 | 400 | 51.7 | 234 | 649 | 1,423 | 0 | revisions gap-free; 22 winning corrections = 22 new revisions; 59 conflicts (409); ack heads never ahead of current, all equal each member's highest ack | **held** |
| Reconnect storm: everyone at once, immediate retry, no backoff | 200 | 2,000 | 48.7 | 2,760 | 11,725 | 16,927 | 0 | no request lost | **held** (0 retries, 0 gave up) |
| Large history: week view, 730-night household vs ordinary | 16 | 300 + 300 | 80.9 / 80.0 | 146 / 150 | 462 / 457 | 742 / 602 | 0 | history length doesn't change cost | **held** |
| Commercial concurrency + worker backlog: 2,000 households buy while no worker runs | 32 | 2,000 (decide + buy) | 32.0 | 818 | 2,140 | 3,281 | 0 | — | — |
| … then 4 worker processes drain the queue | — | 2,022 jobs: 2,000 purchase submissions + 22 caregiver notifications left by the contention run | **133 jobs/s** (15.0 s) | — | — | — | 0 | one provider op per intent, attempts = 1, queue empty (505/506/503/508 jobs per worker). The check covered 2,020 intents: these 2,000 plus 20 left by an earlier smoke test of the script, all confirmed once | **held** |
| Soak: 90% reads / 10% acks for 10 minutes (4 processes) | 16 | 30,400 | 47.4 | 160 | 497 | 764 (max 60,303) | 0 server errors; **17 client timeouts** (> 60 s) | no latency drift, bounded connections, no memory growth | **failed** (see below) |
| Soak, **one** server process, 5 minutes (diagnostic) | 16 | 27,200 | 87.5 | 137 | 399 | 579 (max 4,012) | 0; 0 client timeouts | memory 94.1 → 95.1 MB; p50 133 → 130 ms; ≤ 10 connections | **held** |
| Soak, **one** server process, **10 minutes** (like-for-like control) | 16 | 54,400 | 89.1 | 135 | 390 | 571 (max 1,577) | 0; 0 client timeouts | memory 94.2 → 95.6 MB; p50 137 → 133 ms; ≤ 10 connections | **held** |

Lock waits were sampled with `pg_stat_activity` after each run, and about every 20–30 s during the soaks. Every sample showed 0 sessions waiting on locks, and 0 idle in transaction. Connections peaked at 40 (backlog and reconnect storm) and at 29 during the soak. The configured ceiling is 80 for the four server processes (pool 10 + 10 each), plus the workers.

## The soak failure, diagnosed

The 10-minute soak with 4 server processes failed its own check: **17 of 30,400 requests got no response within 60 s.** Everything else was steady:
- p50 went from 185 to 135 ms;
- server memory went from 379 to 388 MB (+2.4%);
- connections peaked at 29 during the soak;
- there were no 5xx responses.

| Evidence | What it rules out |
|---|---|
| Every 30 s sample during the soak: 0 sessions waiting on locks, 0 idle in transaction, longest active query 0.0 s | Database locks, slow queries or leaked transactions |
| Timeouts arrive in clusters (3, 3, 8, 3, at about 196, 369, 495 and 640 s), plus one 24.9 s request at about 407 s. Other requests in the same intervals finish in about 1 s | Gradual saturation or a memory leak |
| The server log has two `OSError: [WinError 10022]` tracebacks from uvicorn's socket handling. They aren't timestamped, and at least one ends in a shutdown `CancelledError`, so they're a hint, not proof | — |
| **Same soak with one server process: 0 timeouts in 27,200 requests, max 4.0 s, and higher throughput (87.5 vs 47.4 req/s)** | The application code |

**Conclusion: cause not established.** The evidence points away from the app and the database. It is consistent with the server's multi-process mode on this machine, but that is a hypothesis; it is not attributed to Windows or uvicorn without a comparable follow-up (2026-10-11 wording).

The first comparison was not like-for-like (5 minutes against 10). A 10-minute one-process run then served 54,400 requests at 89.1 req/s with 0 timeouts, a max of 1.6 s, and memory flat at 94 → 96 MB. That is the same duration and client load as the failed run, so the evidence points at the server's process model on this machine. It is not a proof of any mechanism, and no run on another OS or server has been done. On this machine, one process was both faster and more reliable than four.

**For production,** run Linux workers behind a load balancer, or use `SO_REUSEPORT`. The prototype's own fix is the operational one:
- run one process per port on Windows;
- give every client request a timeout (the driver uses 60 s);
- retry idempotent reads.

The earlier multi-process numbers in this file are still valid as measured. They are a property of this platform, not a capacity estimate.

## What the numbers say

- **The database isn't the bottleneck at this size.**
  - All 46 captured queries run in under 1.2 ms, with no sequential scan of a large table (`explain-large-100k-*.json`).
  - Serial service time was about 16 ms, measured earlier at 10k. Latency under concurrency is queueing in the Python processes and the single driver.
- **Cold vs warm.** The first burst after building 1.5 GB ran at 37 req/s. The warm repeat ran at 65.3 req/s. At 10k households on an idle machine the same test gave 88.9 req/s.
  - Cache state and machine load move these numbers by more than 2×. Read them as relative.
- **Contention is per household and behaves.**
  - 81 correction attempts on one night: 22 won, 59 were told to refresh. No revision was lost or duplicated.
  - The household lock serializes them, and p99 stayed at 1.4 s with 16 clients hammering one family.
- **A reconnect storm is a client problem as much as a server one.**
  - Nothing failed, but p99 reached 17 s because the server queues rather than sheds.
  - In production: clients back off with jitter, and the server needs admission control (a bounded queue with 503 + `Retry-After`).
  - This prototype has neither, and the measurement shows why both matter.
- **History length is free.** A household with 730 nights reads its week as fast as one with 3 (p50 146 vs 150 ms). The week query is household-indexed and returns 7 rows.
- **Server processes don't scale out on this platform** (see the diagnosis above), but **worker processes do.** 4 workers split 2,022 jobs evenly (503–508 each) with `SKIP LOCKED`, without one duplicate charge.
- **The one real scaling defect** was the outbox claim's cost growing with finished jobs: 356 ms → 0.026 ms after a partial index (migration 0004; `DATABASE_AND_LINEAGE_REVIEW.md`).

## Capacity estimate (estimate, not measured)

- **The local ceiling:** about 40–90 req/s of briefing reads with 4 processes on a shared laptop.
- **Scaling out:**
  - Reads are stateless apart from the database, so they scale with more app processes until PostgreSQL CPU or connections bind.
  - The pools allow 80 connections at 4 processes; 40 were the most observed. PgBouncer (transaction pooling) is the first step past roughly 5 hosts.
- **None of this was measured at more than 4 processes or on more than one machine.**

## Reproduce

```bash
cd proof-layer
python -m bench.generate --profile large --history-nights 730 --out ../artifacts/benchmarks/dataset-large-20261010.json
bash bench/run_suite.sh        # contention, reconnect, large history, backlog, explain, outbox index, soak
python -m bench.explain --label large-100k
python -m bench.outbox_index --history 1000000 --pending 200
```
