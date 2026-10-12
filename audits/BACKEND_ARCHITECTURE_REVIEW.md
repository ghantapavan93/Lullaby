# Backend architecture review

Reviewed 2026-10-10 against the running code in `proof-layer/app/` (4,043 lines, 19 modules, 33 HTTP routes of which 28 are under `/v1`), at migration `0004_outbox_claim_index`. Every claim names a file. Step-by-step flow maps with live line numbers are served by `GET /v1/proof` (X-Ray → **Data & proof → Flows**), resolved from the code with `inspect`, so they can't go stale silently (`tests/test_proof.py`).

## Verdict

A modular monolith on one PostgreSQL database, plus a second database that stands in for the payment provider. The important rules each live in one module. Every write follows the same order: authenticate → lock the household row → recheck membership → authorize → scoped load. That order is the main concurrency mechanism, and it is tested.

Today's review found 2 real defects in the domain rules:
- The household version didn't move when a pending plan was added.
- The app's own unconfirmed purchase was offered again.

It also found 6 input-handling defects that returned 500. All 8 are fixed, with regression tests (see `CONCURRENCY_AND_INVARIANTS.md`). The architecture itself needed no change.

## Runtime and versions

| Item | Value | Where |
|---|---|---|
| Framework | FastAPI 0.143, Starlette 1.7, Pydantic 2.14, uvicorn 0.54 | `requirements.txt` |
| Persistence | SQLAlchemy 2.1, psycopg 3.3, PostgreSQL 17.8, Alembic 1.20 (4 migrations) | `app/db.py`, `migrations/versions/` |
| Connections | One pooled connection per request; pool 10 + 10 per process; sessions in UTC | `app/db.py` (`keep()`, `get_db`, `transaction`) |
| Worker | Separate OS process (`python -m app.worker`), outbox with `FOR UPDATE SKIP LOCKED` and leases | `app/worker.py` |
| Provider | Simulator in its own database (`provider_sim`), idempotent by operation key | `app/provider.py` |
| Auth | Dev-only HMAC tokens `pl1.<member>.<sig>`; household and role read from the DB on every request | `app/security.py` |
| Deployment | Local only. No container, no CI. Docker isn't running on this machine | — |

## Code-level context map

| Context | Module(s) | Owns (tables) | Invariant it protects | Talks to |
|---|---|---|---|---|
| Identity & access | `security.py` | members (read) | Tenant isolation; revocation takes effect under the lock; denials are audited | every write path |
| Evidence & revisions | `engine.py`, `intervals.py`, `briefing.py` | evidence_events, briefing_windows, briefing_revisions | Append-only evidence; one new revision per accepted change; coverage only from usable camera reports; DST-safe UTC algebra | outbox (notify) |
| Acknowledgment | `acks.py` | acknowledgments, ack_heads | Bound to the displayed revision; head is monotonic | evidence (read) |
| Camera plans | `claims.py`, `entitlements.effective_plan` | entitlement_claims | Plan is a pure function of the claim set; one camera per activation code | — |
| Eligibility | `entitlements.evaluate`, `decisions.py` | capability_decisions, commercial_config | Offers only if they unlock the feature on that camera; nothing sold while a purchase is unconfirmed; no offers on safety surfaces | camera plans, purchases (read) |
| Purchase | `purchases.py` | purchase_intents, intent_transitions, outbox | Idempotency per household; one live upgrade per camera; PREPARED→SUBMITTED is the authorization boundary; UNKNOWN is never upgraded without the provider | provider, claims |
| Provider (simulated) | `provider.py` | provider_operations (separate DB) | One operation per key; outcome set only by the provider | — |
| Growth | `growth.py` | experiment_assignments, render_receipts, analytics_events | Sticky assignment; exposure only from render receipts; typed allow-listed events | decisions |
| Operations | `metrics.py`, `proof.py`, `main.py` (X-Ray) | authorization_log (write via `keep()`) | No tenant data in metrics | all (read) |

`main.py` is the application-service layer: HTTP schemas, the write scope, and transaction boundaries. Domain modules take a session and a principal, and they don't import FastAPI.

## Critical flows

The table names the facts a reviewer asks about first. Step-level detail is in the explorer.

| Flow | Source of truth | Auth boundary | Transaction boundary | Idempotency / event identity | Failure states | Tests |
|---|---|---|---|---|---|---|
| 1 Correction | evidence_events + briefing_revisions | `write_scope` (`main.py`) then `load_owned(lock=True)` | one transaction: correction event, revision, outbox notify | `client_event_id` per household (`uq_evidence_client_key`); request hash detects reuse | `REVISION_CONFLICT`, `ALREADY_CORRECTED`, `EVENT_KEY_REUSED`, `OUTSIDE_WINDOW` | `test_corrections_and_acks.py`, critic, redteam |
| 2 Stale ack | acknowledgments + ack_heads | same | one transaction | `uq_ack_member_revision`; conditional upsert on head | `ACK_FACTS_DIFFER`, `INVALID_FACTS` | same |
| 3 Eligibility | capability_decisions (snapshot) | `write_scope` | one transaction under the household lock | decision id; versions recorded (state_revision, catalog, policy, expiry) | UNKNOWN with `PURCHASE_BEING_CONFIRMED`, `PLAN_UNKNOWN`, `HARDWARE_UNKNOWN` | `test_commerce.py`, `test_camera_plans.py`, `test_model_commerce.py` |
| 4 Purchase | purchase_intents + provider ledger | `write_scope` + `can_bill` | prepare = one transaction (intent + outbox); worker: authorize-submission txn → provider call (outside any app txn) → record-outcome txn | `uq_intents_idempotency`; `provider_operation_key = op_<intent>`; `uq_outbox_dedupe` | `STALE_DECISION`, `UPGRADE_IN_PROGRESS`, `CANCELLED_BEFORE_SUBMISSION`, UNKNOWN | `test_recovery.py` (real `os._exit`), `failure_lab.py` |
| 5 Experiment | experiment_assignments, render_receipts | `session_principal` + `growth.render` | per request | `uq_render_once (household, render_id)`; analytics `event_id` | `VARIANT_MISMATCH`, `PROPERTY_NOT_ALLOWED`, `SERVER_ONLY_EVENT` | `test_growth.py` |
| 6 Referral | — | — | — | — | — | **Not built** (deferred by research) |

## Is the architecture good?

### Domain design

| Question | Answer | Evidence |
|---|---|---|
| Meaningful bounded contexts? | Yes, and small. Each context owns its tables and one rule set. | map above |
| Do aggregates protect real invariants? | Yes. The household row lock is the aggregate boundary for every write. That is coarse but correct, and contention is per household, measured in `PERFORMANCE_AND_SCALE.md`. | `security.lock_household` |
| Rules in one place? | Mostly. Eligibility is evaluated in `entitlements.evaluate`. "Being confirmed" was split between store claims and in-flight intents, and that split caused RT-8. Now one function (`decisions.unconfirmed_plans`) covers both. | `decisions.py` |
| Application vs domain? | Separated. `main.py` owns HTTP and transactions; domain modules are plain functions over a session. | imports |
| Readable by another engineer? | Yes. Module docstrings state their invariants, and tests are named after them. `main.py` (678 lines) is the largest file and the first one to split by router if it grows. | file sizes |

### Transactions and events

- **Corrections and briefings commit atomically.** The correction event, the revision and the notify job are written in one transaction. The test `test_revision_and_notification_work_commit_together` checks that the job carries the new revision, and that a rejected correction leaves neither a revision nor a job behind. A crash between the two writes isn't injected; atomicity rests on the single transaction.
- **Outbox records commit with the state they describe.** The intent and its submit job are written together; the revision and its notify job are written together.
- **Partial projections can't happen.** The week view is computed on read from current revisions, so no projection is stored.
- **Event identity is stable:**
  - client keys for evidence;
  - (channel, external_ref, status) for store events;
  - operation keys for the provider;
  - render ids for exposure.
- **Duplicates are no-ops.** This is proven by property tests, the model-based test and the red team.
- **Late events:**
  - A late camera report recomputes the night. The failure lab covers the out-of-order case.
  - A late store event changes the derived plan, and order is irrelevant by construction.
- **Ordering assumption, written down:** a camera's plan depends only on the claim set at `now`. Claims carry the store's own dates.
- **Recovery preserves the original operation.** Reconciliation re-queues the **same** operation key, and the provider recognizes it. Retries can't create a second charge.

### API

| Question | Answer |
|---|---|
| Versioned? | Path-versioned (`/v1`). Decisions and intents also carry catalog and policy versions. |
| Safe for stale mobile clients? | Yes. Writes need `expected_revision` or a decision id. A stale screen gets `REVISION_CONFLICT` or `STALE_DECISION`, plus what changed. Acks bind to the displayed revision. |
| Structured, actionable errors? | Stable `code`, human `message`, `detail`, `trace_id`. Invalid input is 422, never 500, after RT-1 to RT-6. |
| Response size? | `/v1/windows/{id}/brief` returns facts, fingerprints and evidence ids for one night. That's fine. `/v1/xray` is a debug surface and is not meant for mobile. |
| Server-side validation and authorization? | Every route. Foreign ids get 404 on every id route; the red team checked all of them. |

### Operations

| Question | Answer |
|---|---|
| Critical failures observable? | `/metrics`: request counts and latency, purchase states, oldest unconfirmed purchase age, outbox depth. |
| Traces match requests? | A `trace_id` is written on every row and returned on every response. The X-Ray shows them side by side. There's no distributed tracing. |
| Can an operator find an affected operation? | Yes: intent → transitions → provider record by operation key (`RUNBOOK.md`). |
| Worker restart without losing state? | Yes. Leases expire and the job is reclaimed. A real process kill is tested. |
| Bounded, safe retries? | Submit jobs retry with the same key and are never dead-lettered. UNKNOWN is re-asked every 5 s. Notify jobs are idempotent. There is no retry cap on submit: deliberate, because giving up on a possibly-charged purchase is worse. Alerting on the age metric is the backstop. |

## Trade-offs, argued from evidence

- **PostgreSQL.**
  - The guarantees are database features: row locks, `ON CONFLICT`, partial unique indexes, `SKIP LOCKED`, update and delete triggers, and composite foreign keys.
  - At 100,000 households (1.79M evidence rows), every captured app query ran in under 0.15 ms on the freshly built data, and in under 1.2 ms after a 2,000-purchase backlog. The slowest was the outbox claim, since fixed by migration 0004. No sequential scan touched a large table (`artifacts/benchmarks/explain-large-100k-*.json`).
- **Caching.** Not justified yet.
  - Serial service time is about 16 ms, and roughly 7 ms of that is in the handler.
  - Latency under load is queueing in one process, not database time.
  - A cache would add invalidation risk on revision-bound data, which is exactly what this app protects.
  - The first candidate, if read volume demanded one, is an immutable `(window_id, revision)` briefing. Immutable data can be cached safely.
- **Message broker.** Not needed at this scale.
  - The outbox gives atomic enqueue with the state change, which a broker alone would not.
  - The measured limit is the claim query's cost as finished jobs pile up (`PERFORMANCE_AND_SCALE.md`). That's fixed with an index, not a broker.
  - A broker becomes worth it when several services must consume the same events.
- **Service extraction.** Not yet.
  - Splitting Purchase from Eligibility would turn one transaction into a distributed protocol. The stale-offer recheck currently runs under one lock.
  - The extraction seam is clear if it's ever needed: `purchases.py` plus the outbox already talk to the provider through a narrow interface.
- **Modular monolith.** This is the right shape for one team in its first year. Seams are explicit, tests run against the real database, and every guarantee has one owner.

## Smallest valuable fixes (applied today)

| Fix | Why | Test |
|---|---|---|
| Pending plans move the household version | Earlier offers must go stale when a higher plan starts confirming | `test_a_second_pending_purchase_makes_earlier_offers_stale` (fails on old code) |
| In-flight purchases count as "being confirmed" | Never show a paywall for a plan already being paid for (RT-8) | `test_rt_purchase_being_confirmed_is_not_offered_again`, model test |
| Fingerprint ignores re-delivered duplicates | A re-sent record shouldn't force re-review (RT-7) | `test_rt_duplicate_record_does_not_require_renewed_review` |
| Input edge cases are 4xx, not 500 | NUL bytes, long ids, int overflow, naive dates, non-ASCII tokens (RT-1 to RT-6) | `tests/redteam/test_rt_security.py` |

## What a senior reviewer would still challenge

- **One household lock serializes all of a household's writes.** That's fine for a family, but it would be a hotspot for a shared account with many devices. Measured contention is in `PERFORMANCE_AND_SCALE.md`.
- **Dev tokens aren't auth.** Real sessions, device binding and token rotation are out of scope.
- **No CI pipeline or container image.** Tests are run by hand, and their results are committed as JUnit files.
- **Store notifications are simulated.** Real Apple and Google payloads, signatures and replay windows aren't modelled.
- **No referral flow or trial state machine.** The experiment kill switch was added after the final review (migration 0005).
