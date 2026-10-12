# Concurrency and invariants

> **Update 2026-10-11:** this audit records the state at the time. The mutation suite is now 40 mutants, and all 40 were killed on the final code. M19 is no longer an equivalent survivor: the activation-code index was replaced by an exclusion constraint for every channel (migration 0007), and a database-level test catches its removal. See `artifacts/mutation/mutation-report.md`.

**Question:** which guarantees have been tested, how hard, and would the tests notice if a guarantee broke?

Five independent lines of evidence:
1. the existing suite;
2. a model-based state machine;
3. a red team that tried to disprove each guarantee;
4. 24 planted defects (mutation testing);
5. a failure lab that injects real faults.

Every important failure found today has a reproduction and a regression test (`docs/FAILURE_LEDGER.md`, round 5).

## Test inventory (run 2026-10-10)

| Layer | Count | Result | Command |
|---|---|---|---|
| Backend: domain, API, concurrency, recovery, camera plans, week, growth, critic, model-based, red team, explorer | 409 | **409 passed** | `python -m pytest tests --ignore=tests/e2e` |
| Browser (Chromium) | 14 | **14 passed** | `python -m pytest tests/e2e` |
| Mutation | 24 mutants | **23 of 24 killed**; the 1 survivor is an equivalent mutant (below) | `python scripts/mutate.py` |
| Failure lab | 12 faults (+1 not run) | **12 held** | `python scripts/failure_lab.py` |
| Model-based depth run | 60 sequences × up to 18 steps | **60 passing, 0 failing** (`artifacts/test-results/model-commerce-60.xml`, `.txt`) | `MODEL_EXAMPLES=60 python -m pytest tests/test_model_commerce.py` |

## Invariants and their strongest evidence

| Invariant | Example test | Property / model | Concurrency | Mutation that proves the test bites |
|---|---|---|---|---|
| No camera evidence → coverage unknown, never full | `test_no_observation_evidence_is_unknown_not_full_and_not_zero` | red-team Hypothesis: coverage in [0, 1] when known | — | M07 killed |
| Reported sleep never creates observation | `test_parent_reported_sleep_never_adds_coverage` | `test_reported_sleep_never_changes_coverage` (150 examples) | — | — |
| Overlaps and duplicates don't inflate | `test_overlapping_and_duplicated_records_are_not_double_counted` | interval algebra vs a minute grid (300 examples); red-team union property | — | M22 (fingerprint) killed |
| Corrections keep the original; one new revision | `test_original_evidence_is_retained_and_database_refuses_rewrites` | — | 8 threads in the lab; 50-way storm in benchmarks | M02 killed |
| Contested sleep stays uncertain | `test_stretching_a_correction_into_watched_awake_time_is_contested` | — | — | M17 killed |
| DST uses elapsed time | `test_dst_window_measures_real_elapsed_time` | red team: New York, London, Lord Howe + 8 zones | — | — |
| Ack binds to the displayed revision; heads are monotonic | `test_late_old_acknowledgment_cannot_replace_a_newer_one` | red team: ack of N never acknowledges N+1 | contention benchmark checks heads | M10 killed |
| Revocation stops writes, even mid-wait | `test_revocation_committed_while_waiting_for_the_lock_stops_the_write` | — | yes (lock wait) | M09 killed |
| Tenant isolation | `test_isolation.py`; red team on every id route | — | — | M01, M08 killed |
| Plan is a function of the claim set | `test_revoked_transaction_never_grants_in_any_order` | 400 shuffled/duplicated sequences; **model-based test** | cross-household race (10 rounds) | M15, M16 killed |
| Earlier offers go stale when what may be sold changes | `test_a_second_pending_purchase_makes_earlier_offers_stale` | model-based (found G1) | — | M21 killed |
| Never sell what is being confirmed | `test_a_purchase_being_confirmed_is_never_sold_again`, RT-8 test | model-based | — | M12, M20 killed |
| Purchase re-checks the camera as it is now | `test_a_plan_that_starts_after_the_check_is_not_sold_again` | — | — | M13 killed (it **survived** until this test was written) |
| Hardware can't be sold | `test_every_offer_unlocks_its_feature…` (125 cases) | model-based | — | M05 killed |
| No offers on safety surfaces | `test_no_offer_is_ever_attached_on_a_safety_surface` | model-based (`live_view`) | — | M11 killed |
| Server terms only | `test_client_price_manipulation_is_rejected` | — | — | M14 killed |
| Idempotency per household | `test_same_key_with_different_content_conflicts` | model-based key reuse | 8 threads; 16 separate clients (red team); 20-way benchmark | M03 (app check), M04 (DB constraint) killed |
| One live upgrade per camera | `test_database_allows_one_live_upgrade_per_camera` | model-based invariant after every step | — | M18 killed |
| One camera per activation code | `test_activation_code_works_on_one_camera_only` | model-based `redeem_code` | **two households racing** | M23 killed; M19 survives (see below) |
| UNKNOWN is never upgraded without the provider | `test_provider_unknown_stays_unknown_until_the_provider_decides` | model-based ledger check | — | M06 killed |
| Crash after charge reconciles with one operation | `test_crash_of_a_real_worker_process_is_recovered_by_a_restart` | model-based crash rule | `test_concurrent_workers_submit_once` | — |
| Exposure only from render receipts | `test_reading_data_never_creates_exposure` | — | — | — |
| A killed experiment admits no one and counts no exposure | `test_rt_kill_switch` | — | — | M24 killed |

"—" in the mutation column means no mutant targeted that guarantee. That is a limit of the 24 chosen, not a pass.

## Mutation testing in detail

`scripts/mutate.py` copies the app and tests to a temporary directory, applies **one** change, migrates its own database, and runs the backend suite with `-x`. A mutant is killed when a test fails.

The unmutated baseline must be green first. That check stopped a run three times today:
- once on a missing static folder in the copy;
- once on a test that assumed the repository's folder name;
- once on a real defect: a disabled dev route answered 422 instead of 404 (G3).

Final run on the final code, 2026-10-10T14:13:11+00:00 UTC. Baseline (unmutated copy): 409 passed, 1 warning in 143.92s (0:02:23).

| Mutant | Planted defect (the guarantee it breaks) | Verdict | First test to fail |
|---|---|---|---|
| M01 | A purchase decision must belong to the requesting household | **killed** | `test_rt_decision_belongs_to_its_household` |
| M02 | A correction must name the revision the parent saw (expected_revision) | **killed** | `test_stale_screen_correction_gets_revision_conflict` |
| M03 | Idempotency: a retry with the same key replays; a different payload is rejected (app check) | **killed** | `test_rt_same_key_from_concurrent_separate_clients_creates_one_intent` |
| M04 | Idempotency: one intent per (household, key) (database constraint) | **killed** | `test_unique_constraint_is_the_backstop_without_the_lock` |
| M05 | A camera on the wrong mount is blocked; a plan offer can't fix hardware | **killed** | `test_rt_hardware_blocked_decision_never_produces_a_provider_submission` |
| M06 | An unknown provider outcome stays UNKNOWN; it is never turned into CONFIRMED | **killed** | `test_rt_provider_outcomes_are_recorded_distinctly[pending-UNKNOWN-PENDING]` |
| M07 | No camera evidence means coverage is unknown or partial, never full | **killed** | `test_confirmed_zero_is_distinct_from_unknown` |
| M08 | A household-owned row is loaded only within the caller's household | **killed** | `test_rt_decision_belongs_to_its_household` |
| M09 | A revocation committed while a request waits for the lock still stops it | **killed** | `test_revocation_committed_while_waiting_for_the_lock_stops_the_write` |
| M10 | An older acknowledgment can never move a caregiver's head backwards | **killed** | `test_rt_delayed_older_ack_cannot_override_newer` |
| M11 | No offers on safety surfaces (live view, safety alert) | **killed** | `test_no_offer_is_ever_attached_on_a_safety_surface` |
| M12 | While a purchase is being confirmed, don't sell it again | **killed** | `test_rt_purchase_being_confirmed_is_not_offered_again` |
| M13 | Purchase re-runs the rules against the camera as it is now | **killed** | `test_a_plan_that_starts_after_the_check_is_not_sold_again` |
| M14 | Client prices can't override server terms | **killed** | `test_rt_mismatched_expected_terms_are_rejected[expected0]` |
| M15 | A refund (revoked claim) removes the plan | **killed** | `test_revoked_transaction_never_grants_in_any_order` |
| M16 | A camera's plan doesn't depend on the order its claims arrived | **killed** | `test_order_and_duplicates_never_change_the_plan` |
| M17 | A reported sleep claim inside watched time where the camera saw no sleep is contested | **killed** | `test_correction_cannot_silently_overrule_camera_observation` |
| M18 | One live upgrade per camera (database backstop only) | **killed** | `test_database_allows_one_live_upgrade_per_camera` |
| M19 | An activation code works on one camera only (database backstop only) | **survived** | — |
| M20 | The app's own unconfirmed purchase counts as being confirmed (no offer again) | **killed** | `test_rt_purchase_being_confirmed_is_not_offered_again` |
| M21 | A newly pending higher plan moves the household version (earlier offers go stale) | **killed** | `test_a_second_pending_purchase_makes_earlier_offers_stale` |
| M22 | A re-delivered record doesn't change the recorded-sleep fingerprint | **killed** | `test_rt_duplicate_record_does_not_require_renewed_review` |
| M23 | Two households redeeming one code at once get a clear refusal, not a server error | **killed** | `test_one_code_redeemed_by_two_households_at_once_lands_on_one_camera` |
| M24 | A killed experiment admits no one and counts no exposure | **killed** | `test_rt_kill_switch` |

Raw results: `artifacts/mutation/mutation-results.json` and `mutation-report.md`.

### Survivors, explained

- **M19: drop the partial index `uq_activation_code_one_camera`.** This is an equivalent mutant.
  - `uq_claim_event (channel, external_ref, event_status)` already makes an active activation code globally unique, so the partial index adds nothing.
  - The cross-household race test still passes without it, and its row count stays at one camera.
  - Recommendation: drop the redundant index in the next migration, or keep it as documentation. It is a choice, not a defect.
- **M13: the purchase-time live re-check.** This survived the first run because no test reached it: every eligibility change also moved the household version.
  - A camera's plan can change with time alone, when a store event starts later than it was recorded. The new test does exactly that and now kills M13.

### A flaky test was credited with two kills

In both full runs, a mutant was "killed" by `test_rt_tokens_are_bound_to_one_member_and_cannot_be_forged`, a security test unrelated to the change:
- M16 in the first run;
- M21 in the second.

The cause: the test forged a signature by setting its last character to "0". The signing secret is random per process, so 1 run in 16 the forged token **was** the real one.

The test now flips the character to a different one. In the final run, M16 and M21 are killed by `test_order_and_duplicates_never_change_the_plan` and `test_a_second_pending_purchase_makes_earlier_offers_stale`, the tests written for them.

## The model-based test

`tests/test_model_commerce.py` is a Hypothesis `RuleBasedStateMachine` over the real API and worker.

**Rules:**
- store events on two cameras (four channels, three statuses);
- activation codes;
- camera moves;
- eligibility on a normal and a safety surface;
- purchases with fresh and reused keys;
- five provider behaviors;
- worker runs;
- a crash after the provider commits;
- late provider decisions;
- no-news reconciliation.

**Reference model:** written without calling app code for any decision. It reads only the catalog's plan ranks and the policy's feature plans, which are data.

**Invariants after every step:**
- each camera's plan, plan state and team limit;
- the household version;
- every purchase's state;
- one live upgrade per camera;
- no purchase state ahead of the provider's ledger.

**What it found:**
- **G1:** a second pending purchase didn't move the household version.
- It also exposed that its own first model had encoded RT-8 as correct. The red team found RT-8 independently. Both were fixed together.

## Concurrency under load (100,000 households)

The scenarios are in `PERFORMANCE_AND_SCALE.md`. The invariants they checked:
- **same-household contention:** revisions gap-free; each winning correction adds exactly one revision; ack heads never pass the current revision;
- **purchase backlog drained by 4 worker processes:** one provider operation per intent, attempts = 1, empty queue;
- **reconnect storm:** no request lost;
- **soak:** connections bounded, no 5xx.

## What is not tested

- Isolation levels other than PostgreSQL's default READ COMMITTED. The design relies on row locks, not SERIALIZABLE.
- Clock skew between app servers. Decision expiry is set and checked with the app's own UTC clock (`decisions.utcnow`), so skewed hosts would disagree about expiry. Outbox leases use the database's `now()`.
- Network partitions between app and database.
- More than one app host. Tests and benchmarks use one machine, with up to 4 server processes and 4 worker processes.
