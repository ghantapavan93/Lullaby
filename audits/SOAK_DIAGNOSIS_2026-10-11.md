# Soak diagnosis, 2026-10-11

**Question.** Why did the 10-minute soak with 4 uvicorn worker processes have 17 client timeouts in 30,400 requests (`artifacts/benchmarks/soak-large-100k-20261010T131314Z.json`), while one process had none (`soak-large-100k-one-process-10min-20261010T134625Z.json`)?

**Answer, as far as measured.**
- With 4 workers on this Windows host, a worker's event-loop thread sometimes blocks inside `socket.accept()` on the shared listening socket. It stays blocked until the next new TCP connection arrives, for up to 68.7 s.
- While it is blocked, every request already on that worker's keep-alive connections waits. When the wait passes 60 s, the client times out.
- With 1 worker, this never happened, whichever event loop was used.
- **Still unknown:** why Winsock blocks in `accept()` when Python has the socket in non-blocking mode. The mechanism is located; the root cause inside Windows or CPython is not.

These are 3-minute runs (16 clients, same mix as the original). They are not equal to the 10-minute soak. All data is synthetic. The host was shared with another session the whole time, and that load is recorded per run.

## Method

| Item | Value |
|---|---|
| Code under test | tag `interview-candidate-2026-10-11` (`0620500`), in a temporary git worktree. The main tree was not changed apart from this file and the artifact folder |
| Database | `proof_layer_bench` (100,000 households, 1.6 GB), PostgreSQL 17 at 127.0.0.1:54329, `max_connections` 100 |
| Server | uvicorn 0.54.0, Python 3.13.2, ports 8801–8813, `--log-level warning`. Pool 10 + 10 per process (`app/db.py`) |
| Load | `bench/diag_soak.py`: a copy of the `bench.load` soak with the same seed (7), mix (90% reads, 10% acks), 2,000 households, batches of 1,600, between-batch `pg_stats()` + `server_memory_mb()`, 60 s client timeout and concurrency 16. Each run is 180 s |
| Added client instrumentation | Per-request httpx trace (new or reused TCP connection, last phase reached, error type), and client event-loop lag (50 ms ticker) |
| Added server instrumentation | `bench/diag_app.py` wraps `app.main:app`. Each worker writes a 1 s status line (event-loop lag, in-flight requests, oldest in-flight age, requests served). When the loop lags > 2 s or a request is in flight > 5 s, it dumps an all-thread `faulthandler` traceback. `bench/diag_app_accept.py` also times every `socket.accept()` and logs calls > 0.25 s |
| 1 s sampler | `bench/diag_sampler.py`: host CPU% and memory (Win32 `GetSystemTimes` / `GlobalMemoryStatusEx`); CPU% and RSS of the server tree, the load driver and all `postgres.exe`; `pg_stat_activity` counts by state and wait event for the bench DB; cluster connections; ungranted `pg_locks` by type; longest active query and transaction. Every 10 s it also records `Win32_Processor LoadPercentage` (PowerShell CIM) |
| Ordering | Interleaved: 4w, 1w, 4w, 1w, 1w-selector, 4w, 1w-selector, then accept-log / accept-fix alternating, then hot households |
| Tools | Copied to `artifacts/benchmarks/soak-diagnosis-2026-10-11/tools/`; the worktree was removed afterwards |

Per run, the artifact folder has `<run>.json` (result plus `diagnosis_summary`), `<run>-samples.csv` (1 s server, DB and host samples), `<run>-client.csv` (1 s client view), `<run>-workers.jsonl` (1 s per-worker status), `<run>-stall-*.txt` (thread dumps), `<run>-slow-accepts.jsonl` and `<run>-server.log`.

## Environment noise (measured, not removed)

- Host CPU averaged 39–68% per run, with peaks of 73–100%. CIM LoadPercentage averaged 45–74%.
- Host memory load peaked at 84–99%. Free memory fell to **53 MB** during r06.
- Another session's processes were active throughout. Ungranted locks seen cluster-wide (tuple, transactionid, relation) were not in the bench database: bench-DB lock waits were 0 in every sample.
- The load driver used 48–93% of one core on average and hit 100% in every run. It caps throughput: 1-worker runs reached 42–48 req/s here, against 89 req/s on 2026-10-10.

**Throughput and latency percentiles are not comparable with the 2026-10-10 numbers.** Timeouts, stalls and lock counts are the comparison.

## Every run

`4 / default` is uvicorn's own choice for 4 workers: `SelectorEventLoop`, with the listening socket shared via `socket.share()`/`fromshare()`. `1 / default` uses `ProactorEventLoop` and no sharing. `selector` forces `SelectorEventLoop` with 1 worker.

`60 s TO` counts real `ReadTimeout`s. `load.py`'s `client_timeouts` counts every httpx error as 599; one `RemoteProtocolError` after 2.7 s is listed separately. `Max loop lag` is the worst server event-loop block in any worker. `Lock-wait samples` counts 1 s samples with any bench-DB session in `wait_event_type = 'Lock'`.

| Run | UTC | Workers / loop | Variant | Requests | 60 s TO | > 5 s | Max ms | Max loop lag s | Requests served per worker | Slow accepts (> 0.25 s) | Stall dumps | Host CPU mean / max % | Mem max % / min free MB | Loader CPU mean / max % of a core | Bench / cluster conns max | Lock-wait samples |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| r01 | 08:58 | 4 / default | — | 6,400 | **1** | 3 | 60,051 | **67.3** | 749 / 1,144 / 1,247 / 3,260 | not measured | 3 | 41 / 96 | 94 / 826 | 58 / 102 | 17 / 26 | 0 of 182 |
| r02 | 09:02 | 1 / default | — | 9,600 | 0 | 0 | 2,005 | 0.06 | 9,601 | not measured | 0 | 57 / 95 | 93 / 1,037 | 92 / 100 | 11 / 20 | 0 of 201 |
| r03 | 09:05 | 4 / default | — | 9,600 | 0 | 0 | 4,724 | **22.7** | **25** / 2,719 / 3,155 / 3,702 | not measured | 3 | 63 / 97 | 95 / 674 | 92 / 100 | 17 / 35 | 0 of 215 |
| r04 | 09:09 | 1 / default | — | 9,600 | 0 | 0 | 2,113 | 0.02 | 9,601 | not measured | 0 | 54 / 94 | 87 / 2,037 | 93 / 100 | 11 / 30 | 0 of 200 |
| r05 | 09:13 | 1 / selector | — | 8,000 | 0 (1 × RemoteProtocolError, 2.7 s) | 0 | 3,485 | 0.02 | 8,000 | not measured | 0 | 67 / 100 | 89 / 1,644 | 87 / 100 | 11 / 30 | 0 of 192 |
| r06 | 09:16 | 4 / default | — | 3,200 | **5** | 13 | 61,416 | **66.8** | 606 / 721 / 876 / 995 | not measured | 4 | 66 / 100 | 99 / **53** | 38 / 100 | 21 / 40 | 0 of 200 |
| r07 | 09:20 | 1 / selector | — | 9,600 | 0 | 0 | 2,180 | 0.05 | 9,601 | not measured | 0 | 59 / 99 | 96 / 501 | 86 / 100 | 14 / 35 | 0 of 213 |
| r08 | 09:24 | 4 / default | accept timing | 6,400 | **2** | 12 | 60,319 | **68.7** | 53 / 956 / 1,516 / 3,874 | 5 (max 12.1 s)* | 4 | 68 / 98 | 95 / 665 | 71 / 100 | 19 / 38 | 0 of 192 |
| r09 | 09:28 | 4 / default | accept timing + **re-assert non-blocking** | 6,400 | **2** | 5 | 60,161 | **61.4** | 732 / 985 / 1,969 / 2,715 | 6 (max **61.5 s**) | 2 | 65 / 99 | 87 / 2,008 | 65 / 100 | 20 / 38 | 0 of 201 |
| r10 | 09:31 | 4 / default | accept timing | 4,800 | **5** | 9 | 60,785 | **68.7** | 289 / 428 / 1,525 / 2,557 | 6 (max 61.1 s) | 5 | 50 / 94 | 88 / 1,765 | 48 / 100 | 21 / 33 | 0 of 204 |
| r11 | 09:35 | 4 / default | accept timing + **re-assert non-blocking** | 6,400 | **3** | 13 | 60,113 | **61.0** | 1,119 / 1,141 / 1,995 / 2,146 | 9 (max **61.1 s**) | 7 | 39 / 73 | 84 / 2,389 | 59 / 102 | 23 / 31 | 0 of 182 |
| r12 | 09:38 | 4 / default | **8 hot households**, FOR UPDATE | 9,600 | 0 | 12 | 20,558 | **20.4** | 113 / 721 / 3,621 / 5,146 | 9 (max 20.5 s) | 5 | 59 / 100 | 96 / 509 | 86 / 100 | 17 / 27 | 0 of 205 |
| r13 | 09:42 | 4 / default | **8 hot households, FOR NO KEY UPDATE** | 6,400 | **5** | 5 | 60,326 | **68.5** | 383 / 1,422 / 2,024 / 2,571 | 5 (max 61.2 s) | 4 | 54 / 100 | 97 / 394 | 51 / 102 | 23 / 33 | 0 of 223 |

\* r08: worker 9008 lagged 68.7 s, but the run ended before its `accept()` returned, so the call was never logged. Its thread dump shows it inside `accept()`.

**Totals.**

| Configuration | Requests | 60 s timeouts | Requests > 5 s | Worst loop block |
|---|---|---|---|---|
| 4 workers, uniform traffic, no fix (r01, r03, r06, r08, r10) | 30,400 | 13 | 37 | 68.7 s |
| 1 worker, any loop (r02, r04, r05, r07) | 36,800 | 0 | 0 | 0.06 s |

The 4-worker figure is 13 timeouts in 15 minutes of soak. The original was 17 in 10 minutes. The rate is the same order of magnitude.

**Extra observations.**
- **Thread dumps.** All 37 stall dumps from all 4-worker runs show the event-loop thread in `socket.accept()`, called from `asyncio/selector_events.py:178 _accept_connection` (`socket.py:295`). At those moments the worker had 0 requests in its application handler: the oldest in-flight request was ≤ 0.26 s old in every run. So the loop was not blocked by application code, a database call or the pool.
- **Python's view of the socket.** Every logged slow accept has `gettimeout() == 0.0`, so Python considered the socket non-blocking. The same workers also got `BlockingIOError` from `accept()` on most of their other calls, for example 9 of 14 and 26 of 40.
- **Stalls end on a new connection.** 11 of 11 long accepts (≥ 1 s) in r08–r10 ended within ±1 s of the client opening a new TCP connection, and 10 of 11 began within ±1 s of one. New connections were opened in only 10–14 of about 200 seconds per run.
- **Timeouts match the stalls.** In r01, the three slow requests (12.5 s, 12.0 s and the 60 s timeout) were each issued within 1 s before one of worker 18420's three loop blocks (11.8 s, 11.6 s, 67.3 s). They ran on reused keep-alive connections owned by that worker.
- **Why new connections appear mid-run.** They do, in 1-worker runs too: 33–55 per run. Their cause was not investigated; uvicorn's 5 s keep-alive expiry of idle pooled connections is the likely one. With one worker they are harmless.

### Minimal reproduction without the app (`repro/`)

`tools/repro_accept.py` removes uvicorn, FastAPI and PostgreSQL. N spawned processes receive one listening socket the way uvicorn passes it (pickled, then `share()`/`fromshare()`) and serve it with `asyncio.SelectorEventLoop`. 4 clients each open a fresh connection about once per second for 40 s. Runs were interleaved.

| Run | Processes | Requests | Requests > 1 s | Slow accepts (> 0.25 s) | Max accept |
|---|---|---|---|---|---|
| a | 4 | 159 | 1 | 3 | 1.02 s |
| b | 1 | 160 | 0 | 0 | — |
| c | 4 | 155 | 5 | 11 | 1.03 s |
| d | 1 | 160 | 0 | 0 | — |

With sparse arrivals, a blocked `accept()` lasts until the next connection (about 1 s here, up to 68 s in the soak). This reproduces the mechanism in roughly 40 lines of standard-library code.

### Smoke check: 4 workers forced onto ProactorEventLoop (20 s, `smoke-w4-proactor/`)

- Three of the four workers could not use the shared socket: `OSError [WinError 87]` in `CreateIoCompletionPort` ("Accept failed on a socket"), and `[WinError 10022]` in `sock.listen`.
- One worker served all 1,601 requests.
- So "4 workers with Proactor" is not a working configuration, and it could not be used to separate the loop type from process count. `WinError 10022` is the error the 2026-10-10 server log contained. Whether the original tracebacks came from this startup path is not known: they were not timestamped.

## Verdicts

| # | Hypothesis | Verdict | Key numbers |
|---|---|---|---|
| 1 | Multiple uvicorn workers on this Windows host | **Supported**, and narrowed to the shared listening socket | 4 workers: 13 timeouts / 30,400 requests, loop blocks up to 68.7 s in every 4-worker run (9 of 9). 1 worker: 0 / 36,800, loop lag ≤ 0.06 s. 1 worker on the *same* SelectorEventLoop (r05, r07): 0 timeouts, lag ≤ 0.05 s, so the loop type alone does not cause it. Stack location: `socket.accept()` in 37 of 37 dumps. Reproduced without the app (repro a, c vs b, d). |
| 2 | Hot-household lock contention | **Not supported** | The soak picks uniformly from 2,000 households (`rng.choice(windows)`). With all traffic on 8 households (r12, r13): 0 bench-DB lock-wait samples in 428. Stalls had the same `accept()` signature (r12: 5 dumps, all `accept`). |
| 3 | DB connection pool exhaustion | **Not supported** | Bench connections peaked at 23, against a ceiling of 80 for 4 × (10 + 10) pools. Cluster connections peaked at 40 of 100. 0 `QueuePool` errors in 13 server logs. Stalled workers had no request in the handler (oldest in-flight ≤ 0.26 s), so nothing was waiting on the pool. The rise in connections after stalls (16 → 29 in the original) follows the client reconnecting to other workers. It is an effect, not the cause. |
| 4 | Host CPU / memory contention and load-generator saturation | **Present, but not supported as the cause** | Host CPU peaked at 73–100% and free memory fell to 53 MB. The driver was CPU-bound: 48–93% of a core on average, 100% peaks. That limits throughput and inflates p95/p99 everywhere. But 1-worker runs under the same or heavier load (r05: host mean 67%, peak 100%) had no stalls. The longest client event-loop lag was 1.35 s, never near 60 s. The 60 s waits are a server worker blocked in `accept()`, shown by the server-side watchdog, not client lag. r11, the least loaded 4-worker run (host mean 39%), still had 3 timeouts and a 61 s block. |
| 5 | FK row-lock interaction (FOR UPDATE vs FOR KEY SHARE) | **Not supported**; not applicable to this workload | In the catalog, only `children`, `devices`, `experiment_assignments` and `members` reference `households`. The soak writes `authorization_log`, `acknowledgments` and `ack_heads`, which take no FOR KEY SHARE on `households`. Lock-wait samples: 0 in about 2,600 bench-DB samples across all 13 runs. A/B on 8 hot households: FOR UPDATE (r12) 0 lock-wait samples, FOR NO KEY UPDATE (r13) 0. Timeouts differed (0 vs 5) only through `accept()` stalls. **The main repo's locking code was not changed.** |

## A correction to the 2026-10-10 evidence

`bench.load`'s soak calls `pg_stats()` **after** each batch finishes. A batch with a stuck request finishes only after its 60 s timeout. So the earlier "0 lock waits during the stalls" was sampled after the stalls, not during them. The 1 s samples here close that gap, and they still show 0 lock waits. The earlier conclusion survives; its evidence was weaker than the audit said.

## What remains unknown

1. **Why a non-blocking `accept()` blocks.** Python reports a timeout of 0.0, and re-asserting `setblocking(False)` immediately before every `accept()` did not help: r09 and r11 still blocked for 61.5 s and 61.1 s. So it is not simply another process flipping the socket back to blocking between calls. It might be how Winsock handles a listening socket duplicated by `WSADuplicateSocket` (`share`/`fromshare`) across processes, or CPython's `select()`-based accept path on such a socket. Neither has been tested.
2. Whether Linux (`SO_REUSEPORT` or the classic pre-fork accept) behaves differently with the same app. Not run.
3. Why httpx opens new connections mid-run. This is the trigger, not the cause, and it was not investigated.
4. Throughput. The driver was CPU-bound and the host was shared, so no throughput claim from these runs.

## The next experiment that would settle it

Run `tools/repro_accept.py` (4 processes, sparse arrivals) in four variants on an otherwise idle host:
- **(a)** as-is;
- **(b)** child processes inherit the raw handle without a second `share()`/`fromshare()`;
- **(c)** the accept done through `ctypes` as `ioctlsocket(FIONBIO, 1)` + `WSAAccept`, bypassing CPython's socket object;
- **(d)** the same script on Linux.

Each variant is one minute. Reading the result:

| Result | Conclusion |
|---|---|
| Blocks in (a) and (c), not in (d) | Windows shared-listener semantics |
| Blocks only in (a) | CPython's socket layer |
| (b) clean | Uvicorn's double sharing |

Then confirm the operational fix with the full soak: rerun `bench.load --scenario soak --duration 600`.
- 4 single-process servers on separate ports behind a local round-robin proxy, against 1 process.
- Expected: 0 timeouts, if this diagnosis is right.

## Practical consequence (unchanged recommendation, now with a mechanism)

On this Windows host:
- run one uvicorn process per port;
- do not use `--workers > 1`;
- keep the 60 s client timeout and retries for idempotent reads.

The soak remains a failed release gate for the 4-worker configuration. The defect is in the server's process model on Windows, below the application. That is now demonstrated with stack evidence and a standalone reproduction, not only inferred.
