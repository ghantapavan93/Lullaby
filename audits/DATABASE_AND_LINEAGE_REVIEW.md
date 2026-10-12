# Database and lineage review

PostgreSQL 17.8, schema at migration `0004_outbox_claim_index` (4 migrations, all applied by `alembic upgrade head` in every test session). Facts below come from the live catalog of the 100k-household benchmark database. They come through `app/proof.py` `schema()`, which is the same code the explorer's **Domain model** tab uses.

## Verdict

The tenant boundary is enforced by the database, not just by queries:
- composite `(household_id, id)` foreign keys on every household-owned relation;
- append-only triggers on 5 history tables;
- partial unique indexes for "one live upgrade per camera" and "one camera per activation code".

Every query the app sent in the measured workloads used an index on the large tables.

The one access pattern that degrades with history is the worker's outbox claim. It was measured, fixed with a partial index, and re-measured (below). A storage projection shows where partitioning would eventually be needed. That is an estimate, not a measurement.

## Constraints

| Table | Rows (100k profile) | Size | Append-only | Tenant FKs | Uniqueness that carries a rule |
|---|---|---|---|---|---|
| evidence_events | 1,793,874 | 724 MB | yes | child, actor member, superseded event (all `(household_id, …)`) | `uq_evidence_client_key` (idempotent ingest), `uq_evidence_single_correction` (one correction per event) |
| briefing_revisions | 314,864 | 546 MB | yes | window | `uq_revisions_number` (gap-free per window) |
| briefing_windows | 300,634 | 78 MB | — | child | `uq_windows_child_date` (one night per child and date) |
| entitlement_claims | 95,018 | 27 MB | yes | device | `uq_claim_event` (one row per channel event); partial `uq_activation_code_one_camera` |
| members / children / devices / households | 200,000 / 99,941 / 99,959 / 99,913 | 34 / 17 / 22 / 12 MB | — | household | `uq_*_tenant`; partial `uq_devices_one_primary` |
| purchase_intents | (workload-dependent) | — | — | decision, member, device | `uq_intents_idempotency`, `uq_intents_provider_key`; partial `uq_intents_one_live_upgrade` |
| intent_transitions | | | yes | intent | — |
| acknowledgments / ack_heads | | | yes / — | member, revision, window | `uq_ack_member_revision` |
| capability_decisions | | | — | device, member | — |
| outbox | | | — | none (infrastructure) | `uq_outbox_dedupe` |
| authorization_log, analytics_events | | | — | none, deliberately | Denials for unknown households must still be written |

- **Cross-household references are refused by the database.** `test_database_rejects_cross_household_references` inserts a revision pointing at another household's window and gets an FK violation.
- **The append-only triggers are tested.** UPDATE and DELETE raise errors (`test_original_evidence_is_retained_and_database_refuses_rewrites`, `test_intent_history_is_append_only`).
- **Mutation testing removed three database backstops one at a time.** The results show which backstops the application layer already covers (`CONCURRENCY_AND_INVARIANTS.md`).

## Migrations

| Migration | Change | Survives existing data? |
|---|---|---|
| 0001 | Initial schema, triggers, composite FKs | — |
| 0002 | One live upgrade per camera (partial unique index); analytics scoped per household | Yes, index build only |
| 0003 | Plans per camera from append-only claims: `entitlement_claims`, `device_id` on decisions and intents | Backfill: each household's plan becomes a `seed` claim on its primary camera (`migrations/versions/0003_camera_plans_from_claims.py`) |
| 0004 | Partial index for claimable outbox jobs (measured, below) | Yes, index build only; `IF NOT EXISTS` |

Reproducibility: every test session runs `alembic upgrade head` on its database. That includes the mutation runner's temporary copy and the failure lab's database.

**Gap:** `downgrade()` exists but is not exercised by any test.

## Query plans at 100,000 households

Source: `artifacts/benchmarks/explain-large-100k-*.json`, produced by `python -m bench.explain`.

The SQL is the app's own. A SQLAlchemy hook recorded 46 distinct SELECTs while real requests ran in-process. Each was then re-run under `EXPLAIN (ANALYZE, BUFFERS)`.

| Workload | Slowest statement | Plan | Note |
|---|---|---|---|
| Current briefing, historical revision, revision history | 0.05 ms | Index scans: `members_pkey`, `uq_windows_tenant`, `uq_revisions_number` | 4 buffer hits each; no disk reads |
| Caregiver changes | 0.06 ms | Index on members; acknowledgments seq scan | The ack tables were empty at this profile, so their plan isn't meaningful yet. `uq_ack_member_revision` leads with `member_id`, which bounds the scan per member |
| Week view | 0.04 ms | `ix_briefing_windows_household_id` + sort | — |
| Week view, 730-night household | 0.13 ms | same, 15 buffer hits | History length barely matters; `uq_windows_child_date (child_id, local_date)` would serve it directly if it grew |
| Eligibility + assignment | 0.14 ms | `households_pkey` (LockRows: the household lock), `ix_claims_device`, `uq_devices_one_primary` | — |
| Outbox claim | 0.05 ms | **Seq scan + sort** on outbox | Empty table here. See the optimization below |
| Reconciliation scan | 0.01 ms | Seq scan on purchase_intents | Empty here. Same shape as the outbox; see below |
| Experiment health | 0.03 ms | Seq scans on assignments and receipts | An analytics query; acceptable as a batch job |

No sequential scan touched a table with more than 10,000 rows.

## Optimization proof: the outbox claim

Source: `artifacts/benchmarks/optimization-outbox-claim-*.json`, from `python -m bench.outbox_index --history 1000000 --pending 200`. The protocol is written at the top of `bench/outbox_index.py`.

**Bottleneck, from the plan rather than a guess.**
- `worker.claim()` filters on `state` and orders by `id`. Finished jobs stay as history.
- After the 2,000-purchase backlog, the captured claim used `outbox_pkey`, walking ids from the oldest. Its cost grows with every job ever written before the first waiting one.
- With 1,000,000 finished jobs ahead of 200 waiting ones:

| | Median of 7 | Plan | Buffers (hit / read) | Job claimed |
|---|---|---|---|---|
| Before | **356 ms** | Limit > LockRows > Index Scan on `outbox_pkey` | 2,024 / 19,098 | 1002044 |
| After `CREATE INDEX ix_outbox_claimable ON outbox (id) WHERE state IN ('pending','in_progress')` | **0.026 ms** | same shape, on `ix_outbox_claimable` | 4 / 0 | 1002044 (identical) |

**Trade-offs.**
- Inserting and finishing 10,000 jobs took 230 → 290 ms (+26%).
- The index is 16 KB, because it holds only waiting and leased jobs.
- Dead index entries from finished jobs are cleaned by vacuum.

**Decision.** Kept as migration `0004_outbox_claim_index`, applied to every database. The synthetic history rows were deleted after the measurement.

**Not changed, with reasons:**
- **Reconciliation scan** (`state IN ('SUBMITTED','UNKNOWN')`): the planner already serves it from the partial unique index `uq_intents_one_live_upgrade` (Bitmap Index Scan, 0.14 ms after the backlog). A new index would add nothing.
- **Acknowledgment lookups** by window and member: `uq_ack_member_revision` leads with `member_id`, and a member has few acknowledgments. No measured cost.
- **Week view on a 730-night household:** 0.15 ms. Nothing to fix.

## Data lineage

| Displayed fact | Produced by | From | Stored as | Shown with |
|---|---|---|---|---|
| Recorded sleep, onset, final wake | `briefing.compute_facts` | evidence_events in the window (current, non-superseded), clipped to the window in UTC | `briefing_revisions.facts` + per-fact `fingerprints` + `evidence_ids` + `cause` | source chips (Camera / Parent reported), X-Ray evidence table |
| Observation coverage | same | `camera_availability` events with `usable=true` only | same | "unknown" when no reports, never 100% |
| Corrected value | `engine.apply_correction` | a new evidence row that supersedes the target; the original is retained | new revision; `cause.kind = correction` | "was 10 h 40 min", edited mark |
| 7-night average | `engine.week_view` | current revision of each night, computed on read | not stored | denominator "counted of 7", edited nights marked |
| Camera plan, team limit | `entitlements.effective_plan` | all entitlement_claims of that camera | not stored (derived) | plan preview, X-Ray claims table |
| Offer and price | `entitlements.evaluate` + catalog | `CATALOGS` entry with `source` URL and `price_basis` | `capability_decisions.offer` (snapshot with catalog/policy versions) | "Price basis" row on the offer |
| Purchase outcome | `purchases.record_outcome` | provider ledger record for `op_<intent>` | `purchase_intents.outcome` + `intent_transitions` + a `simulated_purchase` claim | X-Ray transitions and provider record |
| Experiment exposure | `growth.record_render` | client render receipt (≥50% visible) | `render_receipts` | X-Ray, experiment health |
| Public research facts | research agents | public pages and papers | `research/`, `data/public_research/manifest.json` | Data & proof → Sources / Availability |

Every row written by a request carries its `trace_id`. Synthetic payloads carry `synthetic: true`.

## Storage at scale (estimate, not measured)

- **Measured:** 100,000 households × 3 nights (plus one 730-night household) took 1.47 GB, about 4.9 KB per household-night.
  - evidence_events: about 2.4 KB of that.
  - briefing_revisions: about 1.7 KB per revision (facts are JSONB).
- **Projected:** if every household kept a nightly record, 1,000,000 households × 365 nights ≈ 365M household-nights ≈ **1.8 TB per year**, before indexes grow.
- **At that size:**
  - Partition `evidence_events` and `briefing_revisions` by month, so old nights stay cold and vacuum stays bounded.
  - Keep the household-leading indexes inside each partition.
  - Consider storing facts per revision once, then de-duplicating identical facts.
- **This projection is an extrapolation of one measured profile.** It is not a capacity test, and it says nothing about Nanit's actual storage.
