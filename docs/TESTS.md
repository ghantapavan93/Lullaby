# Test strategy

**Principle:** test the invariant, not the example. Each defect class gets the original case and its siblings, a control case that must not trigger, and a property test where the input space is wide. Tests run against PostgreSQL, because the guarantees are PostgreSQL behavior. SQLite is never used.

| Layer | Files | What it proves |
|---|---|---|
| Pure rules + properties | `tests/test_briefing_rules.py` | Coverage, union, contested sleep, DST, fingerprints. Hypothesis checks the interval algebra against a minute grid (300 examples) and that reported sleep never changes coverage (150 examples) |
| API + database | `tests/test_corrections_and_acks.py`, `test_isolation.py`, `test_commerce.py`, `test_growth.py` | Transactions, constraints, triggers, authorization, idempotency, concurrency with real threads and barriers |
| Failure and recovery | `tests/test_recovery.py` | Provider outcomes, an in-process crash, a **real OS-process crash** (`os._exit(17)`), duplicate deliveries, concurrent workers, the authorization boundary |
| Browser | `tests/e2e/test_browser.py` | What a person sees, in Chromium via Playwright, against a live server |
| Visual QA | `scripts/capture_screens.py` | 43 screenshots at 375, 390, 768, 1024, 1440 and 1920 px, light and dark, each checked for horizontal overflow |
| Independent critique | `tests/critic/` (33 checks written by a separate reviewer), `artifacts/critic-review.md` | 24 reproductions that failed before repair and 9 attacks that held; all 33 pass now (FAILURE_LEDGER.md) |
| Accessibility | `scripts/a11y_audit.py` | axe-core WCAG 2.0–2.2 A/AA: 0 violations on 48 screen, width and theme combinations (home, access, connect sheet, verified, ended, studio, growth, customers, briefing, features, changes, X-Ray × 390/1440 × light/dark) |
| Baseline | `artifacts/failure-reproductions/test_baseline_v2.py` | The same invariants run against unmodified Kimi v2: 12 of 14 fail there |
| Model-based | `tests/test_model_commerce.py` | Hypothesis state machine: random sequences of store events, camera moves, eligibility checks, purchases (fresh and reused keys), provider behaviors, worker runs, a crash after the provider commits, and late provider decisions. An independent reference model predicts every response, and after each step it's compared with the camera plans, team limits, purchase states, one-live-upgrade rule and provider ledger. `MODEL_EXAMPLES` sets the depth (default 25; run at 60 for the audit). Found G1 |
| Red team | `tests/redteam/` (113 tests), `artifacts/redteam/RT-*.md` | Written by a separate agent to disprove the guarantees. Found 8 defects (RT-1 to RT-8), all fixed and kept as regressions |
| Mutation | `scripts/mutate.py` → `artifacts/mutation/` | 45 planted defects (45 killed on 2026-10-11; M25–M45 cover purchase to access, renewals, ordering, upgrades and the demo reset), one at a time, in a temporary copy against its own database. Each is killed if any test fails. A survivor is either a missing test or a redundant guard; each one is explained in `audits/CONCURRENCY_AND_INVARIANTS.md` |
| Failure lab | `scripts/failure_lab.py` → `artifacts/failure-lab/` | 12 injected faults on fresh synthetic households, each judged by the stored rows |
| Purchase to access | `tests/test_access.py` (53 functions, 66 cases), `tests/test_customer_evidence.py` (8), access and evidence browser tests (10) | Status precedence, connect and verify, stale screens, idempotency, concurrency, refunds, provider ordering (Hypothesis, 300 examples), tenancy, append-only tables, read purity, three outcomes, crash recovery, and that no access screen sells. Map: `docs/ACCESS_EDGE_CASES.md` |
| Subscription model, controlled clock | `tests/test_subscription_model.py` (11) | `app/clock.py` freezes application time. Ten operations are driven through the API, each checked on stored claims, effective entitlement, parent status and allowed recovery: renewal, cheaper renewal, same-start extension, late older period, future period, refund before verification, cross-household ownership, same-instant replay, trial expiry, and concurrent connect with verification. A check that waited for the lock must sort after the connect. A demo reset must never fail while a page polls |
| Migrations as an upgrade | `tests/test_migration_upgrade.py` (3) | Isolated databases at 0006 with realistic data, upgraded to head. Valid data keeps every row. A purchase split across cameras stops with a named diagnostic and the database stays at 0006. A role without the extension privilege is told what a DBA must run; after the DBA runs it, the upgrade succeeds |
| Evidence explorer | `tests/test_proof.py` | Every flow step and cited test in the in-app explorer exists in the running code; the schema comes from the live catalog |

## The 38 required checks

| # | Check | Test |
|---|---|---|
| 1 | No camera observations → unknown coverage | `test_no_observation_evidence_is_unknown_not_full_and_not_zero`, `test_empty_night_is_unknown` |
| 2 | Parent-reported sleep never creates coverage | `test_parent_reported_sleep_never_adds_coverage`, property `test_reported_sleep_never_changes_coverage` |
| 3 | Overlaps not double-counted | `test_overlapping_and_duplicated_records_are_not_double_counted`, property `test_interval_algebra_matches_a_minute_grid` |
| 4 | Duplicates don't inflate sleep | same two tests |
| 5 | Conflicting evidence stays unresolved | `test_conflicting_evidence_has_no_exact_total`; control `test_explicit_correction_is_a_selection_not_a_conflict` |
| 6 | Corrections preserve originals | `test_original_evidence_is_retained_and_database_refuses_rewrites` |
| 7 | Replays create no revision | `test_replayed_correction_creates_no_new_revision` |
| 8 | Simultaneous corrections stay consistent | `test_simultaneous_corrections_one_wins_one_conflicts` (4 threads) |
| 9 | Unrelated windows untouched | `test_correction_does_not_touch_other_windows`, `test_correction_cannot_leave_its_window` |
| 10 | DST windows use real elapsed time | `test_dst_window_measures_real_elapsed_time` (780 and 660 min) |
| 11–12 | Rev 4 shown, rev 5 created, rev 4 acknowledged, never rev 5 | `test_stale_screen_acknowledgment_binds_to_displayed_revision`, e2e `test_stale_screen_keeps_its_revision_and_ack_binds_to_it` |
| 13 | A late old acknowledgment can't replace a newer one | `test_late_old_acknowledgment_cannot_replace_a_newer_one` |
| 14 | Unchanged facts keep their status | per-fact states asserted in the stale-screen test |
| 15 | Changed provenance triggers re-review | `test_same_value_with_changed_provenance_changes_the_fingerprint`, `test_unobserved_uncertainty_change_changes_its_fingerprint` |
| 16 | Revoked caregivers lose access | `test_revoked_caregiver_loses_access_with_the_same_token` |
| 17 | Cross-household requests fail | `test_isolation.py` (7 tests, including raw SQL against composite FKs) |
| 18 | Hardware-blocked decisions can't create submissions | `test_hardware_blocked_decision_cannot_create_a_submission` |
| 19 | The upgrade really unlocks the feature | parametrized `test_every_offer_unlocks_its_feature…` (125 cases); `test_happy_path_confirms_and_grants_the_plan` re-evaluates to AVAILABLE |
| 20 | Expired decisions rejected | `test_expired_decision_is_rejected` |
| 21 | Stale household revision rejected | `test_truthful_upgrade_story` |
| 22 | Catalog or policy change invalidates | `test_catalog_or_policy_change_invalidates_open_decisions` (2 cases) |
| 23 | Another household's decision rejected | `test_foreign_writes_are_rejected_and_change_nothing` |
| 24 | Client price manipulation rejected | `test_client_price_manipulation_is_rejected` |
| 25 | Simultaneous same-key requests → one intent | `test_simultaneous_same_key_requests_create_one_intent` (8 threads), `test_unique_constraint_is_the_backstop_without_the_lock` |
| 26 | Same key, different content → conflict | `test_same_key_with_different_content_conflicts` |
| 27 | Provider success then app crash reconciles | `test_crash_after_provider_success_is_recovered_in_process`, `test_crash_of_a_real_worker_process_is_recovered_by_a_restart` |
| 28 | Provider failure stays failure | `test_provider_failure_stays_failure`, settle-to-FAILED case |
| 29 | Provider unknown stays unknown | `test_provider_unknown_stays_unknown_until_the_provider_decides` |
| 30 | Duplicate deliveries → one external operation | `test_duplicate_outbox_delivery_never_creates_a_second_operation`, `test_concurrent_workers_submit_once` |
| 31 | Stale screens keep the displayed revision | e2e `test_stale_screen_keeps_its_revision_and_ack_binds_to_it` |
| 32 | Refresh preserves durable state | e2e `test_refresh_shows_durable_state` |
| 33 | A rendered surface produces its exposure receipt | e2e `test_rendered_offer_creates_one_exposure_and_refresh_does_not_duplicate` |
| 34 | Never rendered → no exposure | e2e `test_an_offer_never_rendered_creates_no_exposure`, `test_reading_data_never_creates_exposure`; below-the-fold assertion in the e2e test above |
| 35 | Refresh and retry don't duplicate receipts | e2e above, `test_render_receipt_is_recorded_once_per_render` |
| 36 | Mobile keeps the critical information | e2e `test_mobile_layout_keeps_the_critical_facts` (375px, no horizontal overflow) |
| 37 | Error, pending and unknown are distinguishable | e2e `test_failed_and_unknown_outcomes_are_distinct_and_never_celebrate` |
| 38 | Hardware-blocked upgrade can't be forced through the UI | e2e `test_hardware_blocked_purchase_cannot_be_forced_from_the_ui` |

## Tests replaced from v2

The 26 v2 tests asserted v2's API (projections, sleep score, optional `version`, SQLite). Each contract moved:

| v2 test | Corrected contract | Now covered by |
|---|---|---|
| `test_ack_binds_to_seen_version_idempotently` | the revision id is required, not optional | `test_acknowledgment_requires_the_displayed_revision`, `test_acknowledgment_is_idempotent` |
| `test_correction_flags_only_changed_fields` (expected 3 fields) | exactly the facts whose fingerprint changed: 2 | `test_canonical_correction`, `test_fingerprints_track_meaning_not_formatting` |
| `test_coverage_gap_makes_card_provisional_and_abstains` | no coach card or score; status `incomplete_observation` with unobserved intervals | `test_canonical_night_matches_the_specification` |
| `test_unknown_state_reconciles_never_fabricates` (asserted UNKNOWN→CONFIRMED) | **the old assertion was wrong**: reconciliation records the provider's ledger, and PENDING stays UNKNOWN | `test_provider_unknown_stays_unknown_until_the_provider_decides` |
| `test_matching_retry_returns_original_intent` (sequential) | the same under 8-way concurrency, with the DB constraint as backstop | `test_simultaneous_same_key_requests_create_one_intent` |
| `test_scoped_grant_then_revocation` | grants folded into roles; revocation is immediate | `test_revoked_caregiver_loses_access_with_the_same_token` |
| `test_exposure_requires_render_and_dedupes` | the client sends receipts only when visible; one per member, surface and subject | `test_growth.py` + e2e |
| the remaining 19 | same intent, rewritten for the new schema | see the files above |

## Running

```bash
cd proof-layer
.venv/Scripts/python -m pytest tests --ignore=tests/e2e
.venv/Scripts/python -m pytest tests/e2e
```

The latest dated counts are in [RELEASE_PROOF_2026-10-11.md](RELEASE_PROOF_2026-10-11.md), read from `artifacts/test-results/final-*.xml`. Each report records a digest of the code it ran on, and certification compares it with the running code. Line coverage of `app/` is 94% (`artifacts/coverage-reports/coverage.txt`); the worker's real-process crash path runs in a subprocess and isn't counted. Results are stored in `artifacts/test-results/`. Recovery traces (intent transitions with the provider record) are in `artifacts/recovery-traces/`.

## Added after the research gate

| File | Tests | What it proves |
|---|---|---|
| `test_camera_plans.py` | 13 | Plans from claims: order and duplicate invariance (property, 400 examples); team limit matches the plan (property); revoked never grants; checking vs none; one code per camera; per-camera eligibility and purchases; plan preview; claims append-only |
| `test_week.py` | 3 | 7-night denominator; past-night correction reflected on the next read; contested nights excluded and named |
| `test_connection_budget.py` | 2 | One connection per request (fails on the pre-fix code); denials still recorded |
| `test_growth.py` (+3) | — | Safety surfaces never carry offers; sample-ratio check; metrics without tenant data |
| `e2e/test_browser.py` (+2) | — | In-app reset; Present Mode runs end to end to a recovered purchase |
