# Failure ledger

Every defect found, the rule it broke, the repair, and the evidence. "Test" names a check that fails before the repair and passes after.

## Round 1: Kimi v2 baseline (reproduced before any change)

Run: `artifacts/failure-reproductions/test_baseline_v2.py` against unmodified v2. Result: 12 failed, 1 passed, 1 harness error (`artifacts/test-results/baseline-v2-reproductions.*`).

| # | Rule broken | v2 behavior | Repair | Now proven by |
|---|---|---|---|---|
| B1 | No camera evidence ≠ full coverage | 100% from a parent report | Coverage only from `camera_availability usable=true` (`briefing.py`) | `test_parent_report_is_not_camera_observation_even_without_camera_data` |
| B2 | Missing evidence is unknown | 0.0 | `pct=None`, state `unknown` | `test_no_observation_evidence_is_unknown_not_full_and_not_zero` |
| B3 | No double counting | 120 min over 90 | interval union (`intervals.py`), property-tested against a minute grid | `test_interval_algebra_matches_a_minute_grid` |
| B4 | Reused event key + new content conflicts | silently accepted | request hash, `EVENT_KEY_REUSED`; unique `(household_id, client_event_id)` | `test_reused_correction_key_with_different_content_conflicts` |
| B5 | Acknowledgment needs the displayed revision | `version=None` → latest | `revision_id` required | `test_acknowledgment_requires_the_displayed_revision` |
| B6 | Foreign household can't read | no household identity on reads | server-resolved principal, household-scoped loading, composite FKs | `test_isolation.py` |
| B7 | Foreign decision can't be bought | accepted | decision loaded within the caller's household; composite FK | `test_foreign_writes_are_rejected_and_change_nothing` |
| B8 | Hardware-blocked decision can't be bought | CONFIRMED | `NO_ELIGIBLE_OFFER`; offer only if every blocker is a plan | `test_hardware_blocked_decision_cannot_create_a_submission` |
| B9 | Policy change invalidates decisions | still valid | decisions snapshot catalog and policy versions | `test_catalog_or_policy_change_invalidates_open_decisions` |
| B10 | Client price rejected | accepted | server terms only; `TERMS_MISMATCH` | `test_client_price_manipulation_is_rejected` |
| B11 | Reconciliation needs provider evidence | UNKNOWN → CONFIRMED always | separate provider ledger; outcome only from its record | `test_provider_unknown_stays_unknown_until_the_provider_decides` |
| B12 | Concurrent same key → one intent | 2 intents | household lock + unique constraint | `test_simultaneous_same_key_requests_create_one_intent` (8 threads) |
| — | Offer unlocks its feature | **passed** in v2 | kept; now proven for every feature × plan × mount | parametrized, 125 cases |

## Round 2: found while building

| # | Rule broken | How found | Repair | Test |
|---|---|---|---|---|
| D1 | Durations are real elapsed time | DST test measured 720 instead of 780 | Python subtracts same-tzinfo datetimes by wall clock; all interval endpoints normalized to UTC, and DB sessions run in UTC | `test_dst_window_measures_real_elapsed_time` |
| D2 | A decision without an offer stores SQL NULL | DB `CHECK offer is null or status='BLOCKED'` rejected an UNKNOWN decision | JSONB `None` was stored as JSON `null`; `none_as_null=True` | `test_unknown_inputs_never_produce_an_offer` |
| D3 | The timeline is accessible | axe-core: list role without items; 19px non-functional buttons | one labelled `role="img"` plus a table equivalent; the table sorted 03:40 before 19:00 | `artifacts/a11y/axe-report.json` (0 violations) |
| D4 | A refresh keeps a carried operation | refreshing the features screen lost an in-flight purchase | restore the latest intent at boot | e2e refresh test, `08-pending-purchase.png` |

## Round 3: independent critic (`artifacts/critic-review.md`)

A separate reviewer wrote 33 checks in `proof-layer/tests/critic/`: 24 failing reproductions and 9 attacks that held. All 33 now pass. No cross-household leak was found.

| ID | Sev | Rule broken | Repair (shared layer) | Test |
|---|---|---|---|---|
| F-01 | P1 | One upgrade is charged at most once | `UPGRADE_IN_PROGRESS` while any intent is PREPARED/SUBMITTED/UNKNOWN; partial unique index `uq_intents_one_live_upgrade` (migration 0002) | `test_one_upgrade_is_never_charged_twice`, `test_database_allows_one_live_upgrade_per_household` |
| F-02 | P1 | A confirmed purchase never lowers the plan | grant only to a higher-ranked plan; otherwise CONFIRMED with `not_granted_plan_not_higher` recorded | `test_a_confirmed_purchase_never_lowers_the_plan` |
| F-03 | P1 | A correction can't overrule camera observation with an exact total | corrections carry `camera_basis`; only that part is a selection, and any extension into watched awake time is contested | `test_correction_cannot_silently_overrule_camera_observation`, `test_stretching_a_correction_into_watched_awake_time_is_contested` |
| F-04 | P2 | UI copy matches behavior | reported sleep in unobserved time is counted, labelled, and reported as `reported_in_unobserved_minutes`; copy corrected | `test_unobserved_time_copy_matches_correction_behaviour`, `test_reported_sleep_in_unobserved_time_is_labelled_not_hidden` |
| F-05 | P1 | Analytics never store free text | every property typed: count, flag, enum or identifier; objects and prose rejected | `test_analytics_rejects_free_text_in_any_shape` (×3) |
| F-06 | P2 | The acknowledgment response matches what was stored | same revision, different fact set → `409 ACK_FACTS_DIFFER` | `test_second_acknowledgment_of_same_revision_records_its_facts` |
| F-07 | P2 | A correction never changes another window | target records spanning windows → `422 TARGET_SPANS_WINDOWS` | `test_correction_never_changes_another_windows_evidence` |
| F-08 | P2 | A moved sleep block needs review | interval list is part of the `recorded_sleep` fingerprint | `test_moving_recorded_sleep_is_a_reviewable_change`, `test_moving_a_sleep_block_changes_its_fingerprint` |
| F-09 | P2 | An UNKNOWN outcome keeps being reconciled | submit jobs for undecided purchases are never dead-lettered (backoff capped at 60s); the loop reconciles every 30s | `test_unknown_outcome_is_still_reconciled_by_a_running_worker` |
| F-10 | P2 | Clients can't forge server events; ids are per household | client and server event lists split; PK `(household_id, event_id)` (migration 0002) | `test_clients_cannot_forge_server_analytics_events`, `test_analytics_event_ids_do_not_collide_across_households` |
| F-11 | P2 | Receipts carry only assigned variants; render ids aren't reused | variant without experiment → 422; reuse for a different render → `409 RENDER_ID_REUSED` | `test_variant_is_never_recorded_without_its_experiment`, `test_render_id_reuse_for_a_different_subject_conflicts` |
| F-12 | P2 | A missing comparison revision is 404 | 404 | `test_changes_against_a_missing_revision_is_not_found` |
| F-13 | P2 | Caregivers can't read purchase data | X-Ray hides commerce sections without `commercial.read`; `/v1/household/commercial` requires it | `test_caregiver_cannot_read_purchase_data_through_xray` |
| F-14 | P2 | Dev controls off by default; no published secret | `PROOF_LAYER_DEV` defaults to 0 (`scripts/demo.py` enables it); random per-process secret unless set; dev worker and reset need an owner/admin session | `test_dev_routes_and_session_secret_are_not_on_by_default`, `test_dev_worker_requires_a_session` |
| F-15 | P2 | Bad input gets 4xx, not 500 | length limits and enums on request models; `ValueError` → 422 | `test_oversized_or_unknown_inputs_are_rejected_not_500` (×4) |
| F-16 | P2 | Only displayed facts are acknowledged | "Fell asleep" tile added; five facts are shown and acknowledged | `test_acknowledged_facts_are_all_displayed_on_the_briefing` |
| F-17 | P2 | Each screen uses its own viewer's access | polling uses the current viewer's token | code change in `static/app.js` (`refreshLatest`); no automated test |

## Round 4: research gate, benchmarks and Present Mode (2026-10-10, later)

| # | Rule broken | How found | Repair | Test |
|---|---|---|---|---|
| R1 | A plan's scope is the camera, not the household | Research: Nanit plans attach per camera; critic: the mount check passed if *any* camera was overhead | Migration 0003: plans derived per camera from append-only channel claims; decisions, offers and intents bound to a camera | `test_camera_plans.py` |
| R2 | The offer is the cheapest plan that unlocks the feature | Research: Sleep is sold ($100/yr web); the catalog treated it as not purchasable, so no-plan households were offered Memories | Sleep purchasable in the simulated catalog, labelled with its channel price | `test_no_plan_household_is_offered_the_sleep_plan_for_sleep_analysis` |
| R3 | Every Nanit mount is modelled | Research: Nanit Essential (Anywhere Stand) and Multi-Stand were missing | Labels and blocking for both | `test_essential_and_multi_stand_mounts_block_overhead_features` |
| P1 | A request holds at most one connection | Benchmark: 32 × 500 and a 30.6 s p99 at concurrency 32 (pool starvation) | `db.keep()`: audit and analytics ride on the request's connection | `test_every_request_kind_uses_one_connection` (fails on old code); re-measured: 0 errors |
| U1 | Reset reloads the night | Present Mode's first run: in-app reset kept the old window id, so every later call returned 404 | `loadNight()` resolves the window from the server after a reset | e2e `test_in_app_reset_reloads_the_night` |
| U2 | A caption never states old data as new | Present Mode: while waiting, step 3 read "Revision 4: recorded sleep is now 10 h 40 min" | Each step has a neutral pending line until the backend reaches its state | e2e `test_present_mode_drives_the_real_app_to_a_recovered_purchase` |
| C1 | A re-delivered store event is a no-op | New test: the dev feed stamped `starts_at` with the current time, so replays never hashed the same | Store events carry their own date (default: start of the UTC day) | `test_duplicate_store_event_is_a_no_op_and_changed_content_conflicts` |
| C2 | The parent is told the specific reason | New test: a code already used on another camera returned the generic `CLAIM_CONFLICT` | Camera ownership checked before the content hash | `test_activation_code_works_on_one_camera_only` |
| A1 | An edit is never marked by color alone | Visual review at 390 px: the "edited" tag was hidden, leaving only the bar color | Fixed tag slot with an edit icon at every width | `artifacts/a11y/axe-report.json`, screenshots |

**Contracts changed (old assertions updated, not weakened).**
- The critic helpers `plan_code()` and `plan()` read the household subscription. They now read the primary camera's derived plan, because the old table no longer exists.
- `test_unknown_inputs_never_produce_an_offer` deleted every camera, which the claims' foreign keys now correctly forbid. It became `test_a_purchase_being_confirmed_is_never_sold_again`, the real unknown case.
- The live-upgrade backstop is per camera (`test_database_allows_one_live_upgrade_per_camera`).

## Round 5: independent proof gate (2026-10-10, evening)

Found by four independent sources:
- a model-based test (`tests/test_model_commerce.py`);
- mutation testing (`scripts/mutate.py`): a surviving mutant led to a new race test;
- a red-team agent (`tests/redteam/`, reproductions in `artifacts/redteam/RT-*.md`);
- a frontend critic agent (`audits/FULL_APP_INTERACTION_AUDIT.md`).

The failure lab (`scripts/failure_lab.py`) found nothing new. It confirmed the fixes on fresh data.

| # | Rule broken | How found | Repair | Test (fails before, passes after) |
|---|---|---|---|---|
| G1 | Earlier offers go stale when what may be sold changes | Model-based test: a second pending store purchase (Milestones after Memories) left plan, state and team limit unchanged, so the household version didn't move | `claims._commercial_view` includes pending plans | `test_a_second_pending_purchase_makes_earlier_offers_stale` |
| G2 | Bad input or races are 4xx, never 500 | Mutation testing: M19 (drop the activation-code index) survived, so a cross-household race test was written. It found that the guarantee held (one camera) but the losing request got an unhandled IntegrityError (500) | `claims.record` inserts in a savepoint and, on a unique violation, answers from the committed row (`CODE_ALREADY_REDEEMED`) | `test_one_code_redeemed_by_two_households_at_once_lands_on_one_camera` (10 racing rounds; failed 3 of 3 runs before) |
| G3 | A disabled dev route reveals nothing | The final mutation baseline went red: the new kill-switch route answered 422 (body validation) instead of 404 with dev mode off. The red team had noted this as ambiguous | `DEV_ONLY` dependency on all 9 dev routes: dev mode is checked before the body is read | `test_rt_dev_controls_are_unreachable_when_dev_mode_is_off` |
| RT-8 | Never sell what is being confirmed (I23) | Red team: while the app's **own** purchase was UNKNOWN, a new check offered the same plan again (the second charge was still blocked) | `decisions.unconfirmed_plans` counts in-flight intents; the submission check ignores its own intent | `test_rt_purchase_being_confirmed_is_not_offered_again`; model test |
| RT-7 | A duplicate record changes no meaning | Red team: a re-delivered camera record changed the recorded-sleep fingerprint and forced re-review | Fingerprint de-duplicates intervals | `test_rt_duplicate_record_does_not_require_renewed_review`; failure lab |
| RT-1 | Bad input is 4xx, never 500 | Path id over ~103 chars overflowed the audit column → 500 | Audit resource bounded to 120 chars | `test_rt_malformed_input_is_rejected_not_500[long *]` |
| RT-2 | same | A NUL byte in any string → psycopg DataError → 500 | `DataError` → 422 `INVALID_INPUT`; NUL dropped from the audit resource | `…[NUL in *]` (8 cases) |
| RT-3 | same | `?revision=` above int4 → 500 | `Query(ge=1, le=2**31-1)` | `…[revision beyond int4]`, `…[since_revision beyond int4]` |
| RT-4 | same | Non-ASCII token signature → TypeError in `compare_digest` → 500 | Compare bytes | `test_rt_non_ascii_token_signature_is_401_not_500` |
| RT-5 | same | Dev claim with a naive or year-9999 `starts_at` → 500 | Validator: offset required, years 2000–2100 | `…[naive dev claim start]`, `…[far-future dev claim]` |
| RT-6 | A missing revision isn't silently the current one | `?revision=0` returned the current revision | `revision=0` is refused (422) | `test_rt_revision_zero_is_not_silently_the_current_revision` |
| D1 | A rejected save can be retried | Frontend audit: after `REVISION_CONFLICT` the sheet dropped its handler; the next Save closed it and sent nothing | Handler kept; new key on conflict; "Show the newer revision" | e2e `test_a_rejected_correction_can_be_retried_without_losing_the_sheet` |
| D2 | Background refresh keeps focus | The 4 s poll rebuilt the nav and viewer switch | `paint()` replaces markup only when it changed | e2e `test_background_refresh_keeps_keyboard_focus` |
| D3 | Same, in the X-Ray | The poll rebuilt the drawer every 4 s; open panels collapsed | Live trace refreshes only when its data changed; the explorer never auto-refreshes | manual check |
| D4 | The explorer never implies a result that doesn't exist | "caught 0 of 0" hid a failed mutation baseline | Baseline status shown first | manual check |
| D5 | Present Mode leaves the demo as it found it | Overview and product runs ended on a Flex Stand | Closing step restores the Floor Stand | e2e `test_recruiter_present_run_leaves_the_camera_on_its_stand` |
| D6 | Offline is said, not thrown | `fetch` errors were unhandled | `api()` returns `OFFLINE`; banner | manual check |
| D7 | A drawer returns focus and traps Tab while it covers the page | Focus stayed inside the hidden drawer | Opener refocused; Tab cycles below 1024 px | e2e `test_closing_xray_returns_focus_to_its_opener` |
| D8–D17 | Smaller honesty, accessibility and polish items: | Frontend audit | | `audits/FULL_APP_INTERACTION_AUDIT.md` |
| | D8: a lab scenario that wasn't run showed as broken | | shows "not run" | |
| | D9: estimated rows showed 0 after a reset | | exact counts for small unanalyzed tables | |
| | D10: a reset on another screen caused 404s every 4 s | | a notice explains it | |
| | D11: the toast came before the screen updated | | the screen updates first | |
| | D12: a raw JavaScript error was shown | | plain message | |
| | D13: receipts were sent from screens a reset replaced | | observer cleared on reset | |
| | D14: touch targets were under 44 px | | 44 px on coarse pointers | |
| | D15: Space didn't open link-buttons | | handled | |
| | D16: an e2e locator was flaky | | made atomic | |
| | D17: the privacy page didn't mention Google Fonts | | now it does | |

**Contracts changed.**
- The red team's RT-6 test expected 404 for `?revision=0`. The fix returns 422, an invalid parameter. The test now accepts either, as long as it's never the current revision.
- The model-based test had first encoded RT-8 as correct behavior (an offer while the app's own purchase was in flight). Its model was corrected along with the code.

## Round 6: purchase to access (2026-10-11)

| # | Defect | Found by | Repair | Evidence |
|---|---|---|---|---|
| A1 | A re-delivered provider event (same status, same version) read as a contradiction, so a purchase showed "conflicting" | Property test `test_purchase_state_depends_only_on_the_set_of_events` (failed on the first run) | `purchase_state` compares the *set* of statuses at the top version | Same test passes; mutant M34 |
| A2 | A family with a confirmed purchase not yet on a camera was still offered the same plan on the feature page: a double charge waiting to happen | Design review while building, before any test existed. Not reproduced as a failing test first | `decisions._withhold_if_already_bought`: no offer; blocker `PURCHASE_NOT_CONNECTED`, resolution `CONNECT` | `test_pending_evidence_waits_and_never_offers_a_purchase`, `test_unconnected_purchase_withholds_the_offer_for_what_it_unlocks_only`; mutant M25 |
| A3 | Reconnecting a purchase already on the camera would reopen verification ("Connected, checking") | Design review of the window events. The filter came before its test | Only connects that changed the camera count (`already_connected: false`) | `test_repeated_activation_is_recorded_but_does_not_reopen_verification`; mutant M35 |
| A4 | Stage facts contradicted their state ("Plan: not yet known" over "Sleep (active)") | Visual QA screenshot, 1440×900 | Facts follow the stage state; unknown stages say what they wait for | Re-captured `artifacts/visual-qa/access/` |
| A5 | Home camera cards were offset by the global `.card + .card` margin | Visual QA screenshot | Grid children reset the margin | Re-captured |
| A6 | Raw feature keys (`sleep_analysis`) in the verification window and access map | Visual QA screenshot | Labels from the policy, server and client side | Re-captured |
| A7 | A route change scrolled the page past the header (focus on `main`) | Visual QA screenshot | `focus({preventScroll: true})` and scroll to the top | Re-captured |

| A8 | A store renewal of a connected purchase could not reach the camera: the claim key allowed one active event per purchase, so the camera lapsed while the store said active | A customer-reported symptom led to a probe script, which reproduced it (camera back on Sleep while the store said Memories to 2027) | Migration 0007 (claims unique per period; exclusion constraint for one camera per purchase); `_apply_renewal`; `PLAN_EXPIRED` and `PURCHASE_EXPIRED` | 11 renewal tests; mutants M36, M37 |
| A9 | Migration 0007 failed on the mutation database, whose activation-code index had already been dropped by an interrupted earlier run | The mutation runner's baseline step | `DROP INDEX IF EXISTS` | Mutation rerun |
| A10 | The ended-plan stage facts read as a contradiction ("Plan: Blocked, Sleep (active)") | Visual QA screenshot | Facts say what ended and what still works | Re-captured |

The red-team route sweep also failed when the three new POST routes were added: it requires a request body for every route. That was the sweep doing its job, not a product defect. Bodies were added and all 113 tests pass.

## Round 7: final evidence and publication gate (2026-10-11)

| # | Defect | Found by | Repair | Evidence |
|---|---|---|---|---|
| P1 | A trial that ended read "the store hasn't reported a renewal … support can check the store's record", which is wrong for a free trial | Controlled-clock test of trial expiry | Trial endings are worded as trials, with no store or support actions | `test_free_trial_expiration` |
| P2 | A paid, connected period that hadn't started yet read as "No plan on this camera" | Controlled-clock test (future-effective and late-arriving periods) | New status `PLAN_SCHEDULED`: "starts on <date>; nothing more to do" | `test_future_effective_period`, `test_late_arriving_prior_period`; mutant M41 |
| P3 | The demo reset (TRUNCATE of every table) deadlocked with an open page's own requests; PostgreSQL failed one side with a 500 | Visual QA run (server log: DeadlockDetected) | The reset takes every lock first with a 500 ms lock timeout (under the 1 s deadlock check) and retries | `test_a_scenario_reset_never_fails_while_a_page_keeps_polling`: it failed 3 of 3 times on the previous code; mutant M45 |
| P4 | An upgrade with unmet prerequisites would fail with a raw PostgreSQL error (a purchase already on two cameras; no privilege to create btree_gist) | Migration upgrade tests on isolated databases | `0007` preflight: named diagnostics, raised before any change, in one transaction | `test_migration_upgrade.py` (3); mutant M43 |
| P5 | Parent-facing dates were ISO ("2026-10-06") | Desktop review of the renewal journey | "Oct 6, 2026" | `test_renewal`, `test_free_trial_expiration` |
| P6 | Engineering pages and controls sat in the parent's navigation | Task review | One collapsed "For engineers" disclosure, open only on engineering pages; the phone tab bar is parent-only, with engineering links in the X-Ray sheet | `test_engineering_tools_stay_behind_one_disclosure_on_parent_pages` |
| P7 | The Customer evidence view shipped dated sources and counts from private research notes | Publication audit | Public cards are hypotheses; sources moved to private notes that public copies don't contain | `test_every_card_is_a_publishable_hypothesis_and_executable`, `test_without_private_notes_the_view_says_so` |

## Round 8: external critique and the store billing lifecycle (2026-10-11)

| # | Defect | Found by | Repair | Evidence |
|---|---|---|---|---|
| K1 | A store contradiction that arrived after a connect was hidden behind the calm "Connected. Checking your camera" | External critique; reproduced by a new test that failed with `ASSIGNED_PENDING_CONFIRMATION` | Conflict checks come before the pending overlay; the contract table was reordered | `test_a_store_contradiction_after_connect_is_not_hidden_behind_checking`; mutant M46 |
| K2 | The store billing lifecycle couldn't be recorded at all. An Apple billing retry or a Google account hold would read "no renewal reported … it may be on a different account", which is the wrong guidance for a failed card. There was no grace access, no pause, and no store-reported expiry | External critique, checked against official Apple and Google docs (`docs/PROVIDER_LIFECYCLE_FACTS.md`); 11 new tests failed first | Migration 0009; `app/provider_lifecycle.py`; statuses `BILLING_GRACE`, `BILLING_RETRY`, `PLAN_PAUSED`; store-reported `PLAN_EXPIRED`; bind refusals | `tests/test_billing_lifecycle.py` (12, of which 11 failed before; the map test was written with the map); mutants M47–M53 |
| K3 | Docs claimed "one charge" after a worker crash. The code guarantees one provider operation per idempotency key, at least once, against an idempotent simulated provider | External critique | Wording corrected in five places | Doc review |
| K4 | The contract's precedence table had drifted from the code: `PLAN_SCHEDULED` was missing, and the trial and store-reported wording was absent | Doc review while adding the billing statuses | Table rewritten from `_camera_status`, in code order | Doc review |
| K5 | `docs/DOMAIN.md` named a constraint that migration 0007 replaced | External critique | `ex_claim_one_camera` | Doc review |
| K6 | The feature page still offered a plan whose renewal payment was retrying or paused, or whose purchase was unconnected and in grace. `assess()` hard-codes no offer, so the scenario test couldn't see it | Adversarial review of the billing change; 3 new tests failed first | The offer is withheld with `PAYMENT_RETRYING`, `PLAN_PAUSED` or `PURCHASE_NOT_CONNECTED` | `test_a_plan_whose_payment_is_retrying_or_paused_is_never_sold_again` (2 cases), `test_an_unconnected_purchase_in_grace_is_never_sold_again`; mutant M55 |
| K7 | An older or longer grace window kept granting access after the store's newer word shortened or ended it. The camera said "Ready to use" while the purchase said features are off | Adversarial review; 3 tests failed first | Only the top-version grace window is written. A store word that ends access while a written window still grants it is `CONFLICTING_EVIDENCE` (`STORE_ENDED_CAMERA_ACTIVE`) | `test_only_the_latest_grace_window_is_written`, `test_a_later_store_word_inside_a_written_grace_window_is_not_hidden` (2 cases); mutant M54 |
| K8 | A grace event without `days` got a 365-day grace, so the `GRACE_NEEDS_END` refusal was unreachable | Adversarial review; failed first | `days` is optional: a year for a purchase, no end for retry, pause or expiry, refused for grace | `test_a_grace_event_needs_its_end_date` |
| K9 | An unconnected purchase in billing retry promised it "comes back on this camera by itself", which a recovery never does | Adversarial review; failed first | Unconnected copy says it can be connected once the store confirms | `test_an_unconnected_purchase_in_billing_retry_is_not_promised_a_camera` |
| K10 | A lower plan's grace outranked a higher working plan, and a lower plan's retry read "features are off" while an equal or higher plan ran | Adversarial review; failed first | Grace applies only to the plan the camera runs on. Retry and pause copy says what the camera keeps | `test_a_higher_plan_on_the_camera_is_not_hidden_by_a_lower_plans_billing`; mutant M57 |
| K11 | "Connect your Sleep purchase" outranked a higher plan held in billing retry | Adversarial review; failed first | `BILLING_RETRY` and `PLAN_PAUSED` come before `VERIFIED_UNBOUND` | `test_a_lost_higher_plan_outranks_an_unconnected_lower_purchase` |
| K12 | "Stays on until Oct 22" for access that ends at midnight UTC on Oct 22 | Adversarial review; failed first | "through Oct 21" | `test_grace_names_the_last_day_with_access`; mutant M56 |
| K13 | A pause past its resume date still said "paused until <past date>". The home list said "grace period" after grace ended | Adversarial review; failed first | An overdue pause says the store hasn't confirmed the resume. Home uses the same time-aware state as the cameras | `test_a_pause_past_its_resume_date_says_the_store_hasnt_confirmed`, `test_the_home_list_agrees_with_the_camera_after_grace_ends` |
| K14 | The funnel called a purchase connected by a claim "not connected", kept refunds "unresolved" forever, counted a re-failed purchase as recovered, and timed a late delivery as a negative recovery | Adversarial review; failed first | Claims count as connection. Only verified or grace purchases can be unresolved. Recovery is per latest episode, by version; late deliveries are flagged, not timed | `test_durations_come_from_record_times`, `test_a_scenario_connected_by_claim_is_not_reported_unresolved`; mutant M58 |
| K15 | Migration 0009's downgrade dropped `provider_event` from rows whose replay hash includes it | Adversarial review | Downgrade refuses while any row was recorded from a store event | `test_downgrade_from_0009_refuses_while_a_row_was_recorded_from_a_store_event`. **Written after the fix; not seen failing** |
| K16 | The gap matrix rated map-only rows "Modelled", and backed "no offer" by a test of a hard-coded value | Adversarial review | Tests added for `DEFERRED`, `RESTARTED`, the paused-bind refusal and the offer path; rows cite them | `test_deferred_and_restarted_keep_access` and `test_a_paused_purchase_cant_be_connected_again` passed on first run (the paths already worked) |

## Round 9: video evidence, independent review (2026-10-11)

Twelve defects were found by a read-only reviewer in the new video branch, before it merged. They are listed in `docs/VIDEO_EVIDENCE.md` §8 as V1–V12, with four smaller items.

The reviewer reproduced seven on generated clips (V3, V5, V7, V8, V11, V12, and the frame-size bomb behind V3). The tests in `tests/test_video_evidence.py` were written together with the fixes; they weren't run against the pre-fix branch.

## Round 10: cold review, no builder context (2026-10-11)

A reviewer agent got only the repository and the `nanit-cold-review` skill (`.claude/skills/nanit-cold-review/SKILL.md`). It never saw the builder's summary. It ran the server, a browser, live API calls and 8 targeted tests.

| # | Defect | Rule broken | Repair | Evidence |
|---|---|---|---|---|
| C1 | On the shipped demo clip, a feed that stalled right after a pan read as "small change" | A stalled feed is missing evidence, never a measurement | The stall check counts only change inside the window (`local_change_within`); processor `motion-baseline-2` | `test_the_shipped_demo_clip_is_measured_as_scripted_in_every_window`, seen failing against the old `video_processing.py`, passing after |
| C2 | The staged clip's script mislabelled its own pixels: "still" after "move" teleported the shape, "small" swapped to a smaller shape and swayed too little to register, segments straddled windows | A test fixture must show what its script says | Continuous segments (eased path, same shape), a 16 px sway (14 is the least that registers, measured), whole-second segments; cache name `staged-v2` | Same test, all 17 windows; straddling windows: `test_a_window_that_straddles_two_conditions_is_never_read_as_still` |
| C3 | The real CC0 infant recording was the default demo clip when present, under a "Synthetic child records" banner, captioned only "Independent sample clip" | The disclosure must be true | Staged clip by default; the real clip only with `PROOF_LAYER_SAMPLE=cc0`, captioned "Real infant recording … Not synthetic" | `test_a_real_infant_recording_is_used_only_when_asked_for` (written with the fix; not run against the old code) |
| C4 | After a server restart, an open tab's actions failed silently with 401 | Never fail without telling the person | The API helper renews the synthetic sessions once and repeats the request (a 401 ran nothing); otherwise a "Your session ended" banner | Browser, by hand: restart under an open tab, then 401 → two session renewals → 200, page kept updating |
| C5 | The report form pre-selected "A caregiver was beside the crib" | A report says only what the person chose | Nothing pre-selected; submitting without a choice says "Choose one answer first. Nothing was saved." | Code; the browser test picks a label explicitly. The empty-choice message was not browser-tested |
| C6 | Home: the camera reads "Sleep plan" while its Plan stage reads "Not yet known" | Two parts of one card must not contradict | A purchase waiting for its connect shows Plan "Waiting"; the camera line says "Sleep plan now" | Browser, by hand, scenario "Paid, not connected" |
| C7 | One unconnected purchase was offered as "Connect it to …" on both cameras | The copy must not imply two connects | "It can go on one camera: connect it here if Nursery camera is the one." | Browser, by hand |
| C8 | Docs: the media route given as `?token=` (it's a cookie); "fits an active-sleep clip" (a sleep interpretation); "every scripted segment is recovered" (false, C1) | Docs say only what is true | Corrected in `VIDEO_EVIDENCE.md` and the final report, with the correction stated | Documentation defect |
| C9 | Cold start: neither README mentioned the video module; the two READMEs disagreed on the headline; `devdb.py` couldn't create a second checkout's databases | A stranger can run what the README describes | Both READMEs lead with purchase to access and describe recorded evidence; `devdb.py up <suffix>` | Run on a fresh suffix: four databases, migration `0010`, seeded; then dropped |
| C10 | The status documents described the state before the video work (checkpoint, BUILD_STATUS, briefing counts) and the final report handed its verdict to them | One dated verdict, with counts | Rewritten after the final gate (see the release proof) | Documentation defect |

Not a defect, a gap closed: the media route's member and plan re-checks had no test. `test_a_valid_media_link_stops_working_when_the_member_or_the_plan_goes` passed on its first run.

## Round 11: second cold review, on the merged code (2026-10-11)

The same skill, a new reviewer, no builder context, run on `main` after the merge. It ran 10 targeted tests (all passed) and checked the media gate by hand. It found that the evidence held, but the paperwork around it didn't.

| # | Defect | Rule broken | Repair | Evidence |
|---|---|---|---|---|
| E1 | The release proof was committed with placeholder rows while the final run was still going | Never present a verdict before its evidence | Marked DRAFT with `pending` rows until the run finishes | Documentation defect |
| E2 | Four documents carried mutation counts (40 of 40, 45 of 45) that the only artifact contradicted (57 of 58, M16 survived) | Counts come from artifacts | They link to the release proof, which quotes the artifact | Documentation defect |
| E3 | The "report older than this code" check compared file times, so it could never fire on a fresh clone; certification said CERTIFIED regardless | A passing report on other code proves nothing about this code | Each JUnit report records a digest of the code it ran on (`app`, `migrations`, `static`, `tests`, line endings normalised). Certification compares it: CERTIFIED only on a match, UNVERIFIED otherwise | `test_a_report_from_other_code_is_never_certified`, `test_the_code_digest_ignores_line_endings_and_caches` (written with the fix); mutant M68; seen in the browser: an old report shows "Not verified" |
| E4 | The axe report predated the last frontend commit and was called "same frontend" | Re-measure after a change | Rerun on the merged code in the final chain | Process defect |
| E5 | The certification count was stale (13 of 13, 14 of 14; the live count was 15) | Counts come from the live source | Linked instead | Documentation defect |
| E6 | `docs/TESTS.md` called a 409-test run "latest"; the briefing said 140 captures | Same | Linked instead | Documentation defect |
| E7 | The proof-layer README named `capture_screens.py` for visual QA | Same | Names `visual_qa.py` and `a11y_audit.py` | Documentation defect |

Not fixed, now stated as a limit: at 280 px the header controls overflow. Widths under 320 px are not supported.

## Round 12: a flaky reset test (2026-10-11)

| # | Defect | Rule broken | Repair | Evidence |
|---|---|---|---|---|
| F1 | Under steady polling, the demo reset could lose all 60 lock attempts and return 500 | A reset never fails while a page is open | Lock `members` and `households` first, the order every request reads them (`seed.LOCK_ORDER`) | `test_a_scenario_reset_never_fails_while_a_page_keeps_polling`: failed 1 of 5 runs after `test_camera_plans.py` before; 34 of 35 after. The remaining failure, a row-level deadlock between two app transactions, is **not diagnosed** |

## Round 13: final reality review (2026-10-11, evening)

Reproduced in a browser or against a running instance, then fixed. A third cold reviewer, with no builder summary, used an isolated instance of its own and answered ten questions.

| # | Defect | Rule broken | Repair | Evidence |
|---|---|---|---|---|
| G1 | "Play this part" stopped up to 0.19 s late, failed silently when the media endpoint was gone (`play()` errors were swallowed), never renewed the 5-minute media link, and lost the selected interval when the parent left the page | Never fail without telling the person | `playPart`: renew the link when it is about to lapse, seek, play, stop on the boundary every frame, and say each outcome in `#play-status`; media errors are caught; the selection survives a return to the same clip | `test_play_this_part_plays_exactly_the_selected_interval[desktop, mobile]`, `test_playback_failure_is_said_and_a_lapsed_link_is_renewed`: all three seen failing against the old `app.js`. Measured stop: 5.001 s for a 5.0 s boundary |
| G2 | A camera showed five "Verified" stages with no verification check on record, and a trial was called a purchase | Show as certain only what a record or a check backs | The feature stage says "Not checked yet" until a check matches the camera's current facts; a trial or seeded plan's purchase stage says "Not needed" | `test_seeded_cameras_have_honest_statuses` (rewritten: before and after a check) |
| G3 | Present's correction caption claimed "Revision 5" when another visitor's correction had landed first and Present's own was refused | A caption never states another's result as this one's | The step waits on, and captions, its own write; a refusal is said; a step that throws gets a failure caption | `test_present_never_claims_a_correction_another_visitor_beat_it_to`, seen failing against the old `app.js` |
| G4 | Customer evidence said "4 of 4 passed in the latest report" when that report ran on other code; Present's certification caption likewise | Same as E3 | The catalog carries `report.matches_code`; both say "ran on other code" | `test_the_catalog_says_whether_its_test_report_ran_on_this_code` (written with the fix) |
| G5 | The briefing's inventory counts were stale (commits, routes, lines, migrations, scenarios, invariants), it said a check is what makes the status usable, and the release proof's 60-second demo named a walkthrough without the connect step | Docs say only what is true | Counts dated to a commit; the check's role stated exactly; demos rewritten and run | Documentation defect |

Also found, not code defects:
- PostgreSQL went down uncleanly (about 17:16Z), because it had been started as a child of a background shell that was then cleaned up. It recovered on restart, with nothing lost.
- One backend run on `980497b` had 1 failure that wasn't captured; the next run passed 595 of 595. Probably the known rare reset deadlock (F1). Final runs use `-rf`, so a failure names itself.
- One commit subject (`e9d347c`) is 76 characters, over the 72 the author asked for. It was left as is: rewriting history needs his word.

## Round 14: end-to-end bug hunt, live and local (2026-10-12)

Two sources. A scripted sweep of the deployed copy (`deploy/live_sweep.py`: every page, all 15 scenarios, compare, X-Ray, theme, reload, Present, hand-off; 1440 and 390 px) found H1. An independent exploratory agent on a local 3-workspace gateway stack found H2–H18, each reproduced with steps and screenshots.

| # | Defect | Rule broken | Repair | Evidence |
|---|---|---|---|---|
| H1 | After a visitor ended a workspace, the next visitor could get 503 `WORKSPACE_NOT_READY`: the claim's reseed gave up because the janitor's reseed of the same slot was in flight. With several slots, the janitor could also wipe a slot already handed to a visitor | A freed workspace is handed to the next visitor; nobody's data is wiped under them | One reseed in flight per slot, shared by every waiter; the janitor re-reads the slot's state before wiping; the client waits through `WORKSPACE_NOT_READY` | `test_gateway_reseed.py` (3 tests), all three seen failing against the old gateway |
| H2 | The connect dialog crashed (`focus_purchase` null) when another tab connected the purchase while it was open | Never fail without telling the person | The dialog keeps the purchase it was opened for; the server's stale-revision refusal is shown | Reproduced by the agent; checked by hand, no automated test |
| H3 | A pending purchase was described with the camera's current plan ("your Sleep purchase" while Memories was being confirmed) | Copy names what is actually being bought | Pending copy and the purchase stage use the unconfirmed plan | `test_worker_crash_recovers_to_one_charge_and_usable_access` (extended; written with the fix) |
| H4 | "Ready to use" sat beside a feature stage reading "Not checked yet" | A card never contradicts itself | The stage reads "Expected by the plan; not checked", with the plan's rules as the reason | Copy defect |
| H5 | Studio kept "matches" after the store's renewal changed the status, and the worker buttons overwrote the build-time result | A result names when it was measured | "After the store's event" is measured and shown separately; the renewal button is spent once applied | Checked by hand |
| H6 | The worker-crash scenario sometimes couldn't crash: the always-on worker finished the job first | A scenario shows what it says | The scenario holds its job for 15 minutes; only the demo worker (`--include-held`) takes it | `test_worker_crash_recovers…` asserts the always-on worker takes nothing |
| H7 | A lookup of an unknown code showed the camera's own trial as its purchase; the support summary dropped the code | Same as H3 | Not-found facts describe the lookup; the summary includes what was typed | Checked by hand |
| H8 | Recorded evidence on a fresh workspace was a dead end ("not on this camera's plan", no way forward) | Every refusal says what to do next | A simulation bar builds the renewed-plan scenario from that page | Checked by hand |
| H9 | Horizontal overflow at 390 px: clip facts (451 px) and the night list (410 px) | No horizontal page scroll at phone width | `.facts` column capped; the list table scrolls inside its own region | Visual QA rerun (see release proof) |
| H10 | Week value labels overlapped at 390 px | Same | Labels wrap | Same |
| H11 | "Customer outcome observed" was recorded for a feature opened after access was lost | An outcome needs its own evidence | The server stamps `usable_when_opened` on arrival (clients can't send it); only usable opens count | `test_opening_a_feature_after_access_was_lost_is_not_a_customer_outcome` (written with the fix) |
| H12 | A corrected video report still appeared under "After" | A corrected report is history | Snapshots drop replaced reports | `test_a_correction_is_a_new_revision_and_the_original_stays` (extended; written with the fix) |
| H13 | Present: raw feature keys, a step titled "Invariants block release" over a passing report, and second-person narration of Caregiver B | Captions say what is true, in the narrator's voice | Feature labels; neutral title; "Caregiver B acknowledged…" | Copy defect |
| H14 | "Your current plan is No Insights plan" | Same wording everywhere | "This camera has no plan yet" | Copy defect |
| H15 | Comparing a scenario with itself showed a lowercase server error over the old comparison and reset both pickers | Same as H2 | Checked in the page first; choices kept; a failed comparison clears the old one | Checked by hand |
| H16 | Switching feature hid an in-flight purchase; after a reload it lost its camera and feature | Never replace content under the user | Only a settled purchase leaves the screen; the panel names feature, camera and plan | Checked by hand |
| H17 | A spaces-padded code passed the form, then failed with a generic message; a link to an unknown camera silently showed another | Same as H2 | Trimmed length checked first; the unknown camera is named in a notice | Checked by hand |
| H18 | Radio groups had no arrow-key support and every option was a tab stop | ARIA radio group behavior | One tab stop per group; arrow keys move and select | Checked by hand |

Not fixed, stated as a limit: the per-address limit on new workspaces trusts the first `X-Forwarded-For` entry, so a visitor calling the Render URL directly can bypass it ([LIMITATIONS.md](LIMITATIONS.md)). Not a defect: the 409 on `POST /v1/purchase-intents` during Present is the stale offer being refused, by design.

## Round 15: frontend audit, four independent reviewers (2026-10-12)

Four agents with no builder context audited the frontend in parallel on a local four-workspace gateway: visual craft (390 and 1440 px, light and dark), HTML, head and CSS code, product communication (recruiter, Head of Product and senior engineer), and mobile interaction (375, 390 and 768 px, touch). Each reported only what it saw or measured. Fixes were written test first where a browser can measure them (`tests/e2e/test_craft.py`, `tests/test_frontend_shell.py`).

| # | Defect | Rule broken | Repair | Evidence |
|---|---|---|---|---|
| I1 | The live Customer evidence page explained how its private sources were collected, which a parent-facing page doesn't need | Say what the page shows, plainly | "Each card paraphrases a pattern in parents' public posts… not distributed with this demo." The sources file was never in a public copy | `test_without_private_notes_the_view_says_so` (extended) |
| I2 | Links styled as buttons were underlined on almost every screen | Buttons read as buttons | `.btn { text-decoration: none }` | `test_links_styled_as_buttons_are_not_underlined`, seen failing on the old CSS |
| I3 | The video timeline's `.seg` rule overrode the night timeline's: awake time drew as a full-height block with a pointer cursor | The timeline matches its legend | Video rules scoped to `.timeline-track` | `test_awake_time_on_the_night_timeline_is_a_thin_rule`, seen failing |
| I4 | Cards touched (0 px) on Access, Studio and Recorded; side-by-side cards sat 16 px out of line | Consistent spacing | Grid children reset; the layouts get block margins | `test_stacked_cards_never_touch_and_side_by_side_cards_line_up` (3 routes), seen failing |
| I5 | Studio's comparison selects were 14 px (iOS zooms) and unstyled | Inputs at least 16 px; one field style | The field style, at 16 px or more | `test_form_controls_on_a_phone_are_large_enough_not_to_zoom`, seen failing |
| I6 | The tab title never changed, and focus went to an unnamed `<main>` | Each screen names itself | `document.title` per route; focus moves to the page's heading | `test_the_browser_tab_names_the_screen`, seen failing |
| I7 | Five minutes of cache on unversioned `app.js` could pair new HTML with old JS after a deploy | A page runs its own deploy's code | Assets linked with `?v=<content hash>`, cached as immutable | `test_after_a_deploy_pages_load_that_deploys_assets` |
| I8 | No Content-Security-Policy, framing protection or Permissions-Policy on either host; four inline `onclick` handlers | Defense in depth for a public link | One policy in `deploy/gateway/headers.py` for Vercel and the gateway; inline scripts allowed only by hash; handlers moved to `data-action` | `test_the_bundle_sends_a_strict_policy…`, `test_the_gateway_sends_the_same_policy` (4 paths), `test_no_served_markup_carries_an_inline_event_handler` |
| I9 | A forwarded link previewed as a bare URL: no Open Graph or Twitter card, no theme-color or touch icon | A link explains itself | Question-framed preview tags; `og.png` and `apple-touch-icon.png` rendered from our tokens (`design/tools/render_share_images.py`) | `test_a_forwarded_link_previews_with_a_title_a_summary_and_an_image` |
| I10 | The 60-second walkthrough skipped "I paid for this. Why can't I use it?" and narrated results that were off screen | The demo shows its point | The recruiter path leads with paid-not-connected and connect; results scroll into view; the closing line points to the parent reports | Watched end to end (round 15 sweep) |
| I11 | Opening `/app` showed nothing to solve and no place to start | First 10 seconds | A "Start here" card: the parent's question, the walkthrough, the parent reports, a scenario | Screenshots, both widths |
| I12 | Connect on a phone left the result off screen with focus on `<body>` | Every action shows its result | Toast, then the result heading scrolled into view and focused | `test_after_connecting_the_result_is_on_screen_with_focus_on_it`, seen failing |
| I13 | X-Ray's links changed the page behind it on phones while it stayed open; it wasn't a dialog | A modal behaves as one | Closes on navigation below 1280 px; `role=dialog`, `aria-modal`; the page behind doesn't scroll | `test_the_x_ray_closes_when_one_of_its_links_opens_a_page`, seen failing |
| I14 | A second visitor saw "couldn't start" and gave up at once, and a later route change said "the workspace has ended… data is gone" when it never started | Never fail without telling the person what happens next | The page waits up to 15 minutes, retrying every 15 s, and says so; "ended" only for a workspace that started | Checked by hand |
| I15 | "Queued for analysis" could sit forever with no message after "Run the worker" | Same | A toast on start; after 45 s without progress the page says so and what to do | Checked by hand |
| I16 | A deleted heartbeat folder crashed the always-on worker (seen on the local stack) | An always-on worker outlives any one pass | The heartbeat is inside the loop's guard and recreates its folder | Checked by hand |
| I17 | Hover styles stuck after a tap; no tap-highlight or `touch-action` rules; no scroll padding under the sticky chrome; sheets ignored the home indicator and let the page scroll behind them | Touch polish | `@media (hover: hover)` on every hover rule; `touch-action: manipulation`; `scroll-padding`; safe-area padding; `overscroll-behavior` and a scroll lock | Checked by hand |
| I18 | Smaller polish: `.small` text on body line height, state labels at 13 px, no chevron on expandable summaries, twelve competing primary buttons on Studio, an unstyled "Analysed" chip, the X-Ray covering header controls at 1440 px, the theme icon always a moon and the browser bar ignoring the toggle, a theme flash on load, plan cards wrapping, Present taking a third of a phone, no phone route to Studio, Growth or Customer evidence, the privacy page calling the hosted copy "local" and omitting its cookie, a 404 button to a page that moved | Craft | Each fixed in place | Screenshots; checked by hand |

Written with its fix and not seen failing: `test_plan_cards_fit_a_small_phone_when_the_camera_is_on_a_flex_stand`. It passes on the old CSS too, so it did not reproduce the overflow the mobile reviewer measured inside Present mode.

## Round 16: final review by a principal backend engineer, a Growth lead and a release engineer (2026-10-12)

Three read-only reviewers with no builder summary: backend correctness and recovery, Growth instrumentation, and the public repository and deployment. The backend reviewer found no P0 (no double charge, cross-tenant read or purchase on two cameras).

| # | Defect | Rule broken | Repair | Evidence |
|---|---|---|---|---|
| J1 | The public copy shipped `PUBLIC_MANIFEST.md`, which named every excluded file and why | Private context never ships | The manifest stays in `release/`; the scan also refuses application and research-collection wording | Build scan: 0 hits; the file is absent from the public tree |
| J2 | Growth Lab merged steps: confirmed, effective, eligible, shown and opened were not counted apart, and "shown" was collected but never read | An outcome needs its own evidence | A read-only ladder: each step from its own rows, over one denominator (confirmed purchases); a claim counts only once its period has started | `test_the_ladder_keeps_five_steps_apart_each_from_its_own_rows`; checked end to end in the browser |
| J3 | The availability experiment recorded exposure on the offer card, which a hardware-blocked household never sees, though the hypothesis is about those households; its primary metric was zero by construction | Measure the population the hypothesis is about | Exposure moved to the explanation card, where the variant renders; the metric restated as defined, not yet computed | `test_a_hardware_blocked_decision_is_exposed_to_the_experiment`, seen failing on the old frontend |
| J4 | Polling a purchase returned a bare 500 when the provider was down | A client's poll gets the stored state | The provider record is optional detail; `provider_reachable` says when it is missing | `test_polling_a_purchase_still_answers_when_the_provider_is_down`, seen failing |
| J5 | With one workspace, a visitor whose own lease had expired was told every workspace was busy until the janitor ran | Same as H1 | The visitor's own expired lease is freed at once | `test_a_returning_visitors_own_expired_lease_is_freed_for_them_at_once`, seen failing |
| J6 | The gateway tests imported the gateway without its folder on the path, so they passed only after another module set it | Tests don't depend on their order | The fixture adds the path | The module now passes alone |
| J8 | The live certification could never say CERTIFIED: the image held neither the tests nor the report, and once it did, the code digest still differed because file paths sorted case-insensitively on Windows and case-sensitively on Linux | A report certifies its code on any platform | The image ships the tests and the public copy's own report (`release/certify_public.py`); the digest orders files by their posix path string | `test_the_code_digest_is_the_same_on_every_platform`, seen failing; checked on the live site with `deploy/live_certification.py` |
| J9 | One public commit (`87cdb4c`) carried the machine's absolute path in two skip messages of the test report: the copy was synced after a test run without being scanned again | The public copy is scanned after anything writes to it | `release/certify_public.py` strips local paths, deletes the run's caches and rescans before anything is synced; the fix commit removed the path from the current tree | The scan over the whole clone: 0 hits. The path remains in that commit's history |
| J7 | The release proof said the advisory lock fixed "the flaky failure of round 12"; they are different deadlocks | Docs say only what is true | The new release proof separates them: the cross-household redemption deadlock is fixed; the reset test's row-level deadlock is still undiagnosed | Documentation defect |

Also found: running two test suites at once on the one shared test database produced 15 false failures (lock timeouts, a wrong state). They passed when rerun alone. The test database name is fixed (`proof-layer/tests/conftest.py`), so suites must run one at a time; nothing enforces it yet.

Not fixed, stated: a refund of one renewal revokes the whole purchase on that camera (a deliberate conservative rule that does not match per-transaction store refunds); the household-wide revision cancels a prepared purchase when an unrelated camera changes; the Worker-crash scenario once showed the fresh-seed status on the live free host at 390 px (not reproduced in 15 local runs with an always-on worker).

## Still open

None of the reproduced defects is open. In all, 183 defects are recorded: 12 from the Kimi baseline, 4 found while building, 17 from the critic, 9 in round 4, 28 in round 5 (1 model-based, 2 from mutation testing, 8 red team, 17 frontend) 10 in round 6 (1 property test, 2 design review, 5 visual QA, 1 customer research, 1 migration robustness) 7 in round 7 (the publication gate) 16 in round 8 (an external critique and an adversarial review of the billing change) 12 in round 9 (an independent review of the video branch; those tests were written with their fixes) 10 in round 10 (a cold review without the builder's context; C1 was seen failing, C3 was not) 7 in round 11 (a second cold review on the merged code; E3's tests were written with the fix) 1 in round 12 (a flaky reset test; one rare deadlock remains undiagnosed) 5 in round 13 (the final reality review; G1 and G3 seen failing against the old code) 18 in round 14 (an end-to-end bug hunt; H1 seen failing against the old code, one limit stated rather than fixed) 18 in round 15 (a frontend audit by four reviewers; nine browser tests seen failing on the old frontend) and 9 in round 16 (backend, Growth and release reviews, and the live certification; J3 to J5 and J8 seen failing on the old code). A2, A3 and K15 were fixed before their tests existed, so those tests were not seen failing; K3–K5 and K16 are documentation defects. Every backend defect, and frontend defects D1, D2, D5 and D7, have a test that fails before its repair and passes after (the four browser tests were run against the previous `app.js` to confirm). D3, D4, D6 and the low-severity frontend items were checked by hand in the browser. The scope limits that are not defects are in [LIMITATIONS.md](LIMITATIONS.md).
