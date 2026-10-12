# Access edge cases

Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.

Every row names the test that proves it. Unless noted, tests are in `proof-layer/tests/test_access.py` (53 test functions, 66 cases with parametrization, as of the 2026-10-11T02:31Z run). Browser tests are in `tests/e2e/test_browser.py`. The JUnit report is `artifacts/test-results/final-backend.xml`.

## Purchase and provider

| Case | Expected | Test |
|---|---|---|
| Paid on the website, not on a camera | `VERIFIED_UNBOUND`; connect; verify; no purchase intent created | `test_paid_not_active_connects_without_a_new_purchase` |
| Store hasn't confirmed | `PURCHASE_PENDING`; offer withheld; connect refused | `test_pending_evidence_waits_and_never_offers_a_purchase` |
| Older event delivered late | Ignored; no revision bump | `test_late_older_provider_event_changes_nothing` |
| Two statuses at one version | `CONFLICTING_EVIDENCE`; connect refused | `test_same_version_contradiction_is_conflicting_and_cannot_be_connected` |
| Same event twice; same key, different content | No-op; `EVIDENCE_CONFLICT` | `test_provider_event_replay_is_a_noop_and_changed_replay_is_refused` |
| Any order, any duplicates | Same state (Hypothesis, 300 examples) | `test_purchase_state_depends_only_on_the_set_of_events` |
| Store can't verify | `PURCHASE_UNVERIFIED`; support; connect refused | `test_unverified_purchase_is_explained_and_cannot_be_connected` |
| Worker dies after the provider commits | `PURCHASE_PENDING`, then one provider operation for the idempotency key (the simulated provider honours the key) and usable access | `test_worker_crash_recovers_to_one_charge_and_usable_access` |

## Camera and entitlement

| Case | Expected | Test |
|---|---|---|
| Plan active, mount can't support it | `ACTIVE_HARDWARE_BLOCKED`; mount guide; no offer | `test_active_plan_on_wrong_mount_is_not_a_billing_problem` |
| Code on the wrong camera | `ALREADY_ASSIGNED`; not moved; second redemption refused | `test_purchase_bound_to_another_camera_is_not_moved` |
| Six concurrent connects to two cameras | Exactly one claim | `test_concurrent_connects_of_one_purchase_bind_exactly_once` |
| Another family's camera | 404 on read, connect and verify | `test_another_familys_camera_is_not_found` |

## Commercial protection

| Case | Expected | Test |
|---|---|---|
| Paid twice for one camera | `DUPLICATE_PURCHASE`; connect refused | `test_duplicate_purchase_is_not_connected_to_the_same_camera` |
| Paid plan on a camera with a trial | Allowed (a trial isn't a payment) | `test_a_trial_does_not_make_a_paid_purchase_a_duplicate` |
| Unconnected purchase vs. a feature it doesn't unlock | That offer stays honest | `test_unconnected_purchase_withholds_the_offer_for_what_it_unlocks_only` |
| Refund after activation | Revoked claim; trial remains; history kept; window `ACCESS_LOST` | `test_refund_after_activation_revokes_access_and_keeps_history` |
| Store says verified after a refund | `CONFLICTING_EVIDENCE`; no reactivation | `test_store_active_after_a_refund_is_a_conflict_not_a_reactivation` |
| Every scenario × every camera | No offer, no buy action | `test_every_scenario_reaches_its_expected_status_and_never_sells` (7 cases) |
| The same, in the browser | No upgrade text or offer card | `test_access_screens_never_offer_a_purchase` (5 cases) |

## Command safety and tenancy

| Case | Expected | Test |
|---|---|---|
| Screen out of date | `STALE_ACCESS_VIEW` with the current revision; nothing written | `test_stale_view_is_refused_with_the_current_revision`; browser: `test_a_stale_connect_is_refused_in_the_sheet_and_nothing_changes` |
| Retry; key reused for another request | Same outcome; `IDEMPOTENCY_KEY_REUSED` | `test_replay_returns_the_same_outcome_and_key_reuse_is_refused` |
| Caregiver | Status only; `ask_owner`; 403 on connect and lookup | `test_caregiver_sees_status_but_cannot_connect_or_see_purchases`; browser equivalent |
| Revoked caregiver | 403 | `test_revoked_caregiver_cannot_read_or_verify`; red-team route sweep |
| Another family's purchase | Identical to "not found"; recording refused | `test_another_familys_purchase_cannot_be_recorded_or_seen` |
| Evidence under the wrong household | Refused by a foreign key | `test_database_refuses_evidence_under_a_household_that_does_not_own_the_purchase` |
| UPDATE or DELETE on access tables | Refused by trigger | `test_access_records_are_append_only` (3 tables) |
| Reading | Writes nothing | `test_reading_access_writes_nothing_but_the_authorization_record` |

## Growth measurement

| Case | Expected | Test |
|---|---|---|
| Click before verification | Doesn't count as an outcome | `test_three_outcomes_are_never_inferred_from_one_another` |
| Client posts a server event | `SERVER_ONLY_EVENT` | `test_server_access_events_cannot_be_posted_by_clients` |
| Replayed connect | No second server event | `test_connect_and_verify_emit_server_events_once` |
| Repeated activation | Recorded; verification not reopened | `test_repeated_activation_is_recorded_but_does_not_reopen_verification` |

## Renewals and ended plans (migration 0007; a customer-reported symptom)

| Case | Expected | Test |
|---|---|---|
| Renewal of a connected purchase | Second period on the same camera; still usable; no new connect | `test_renewal_of_a_connected_purchase_keeps_the_camera_active` |
| Same renewal delivered twice | One period added | `test_a_renewal_delivered_twice_adds_one_period` |
| Plan ended, no renewal | `PLAN_EXPIRED` with the date; no offer; trial unaffected | `test_an_ended_plan_without_renewal_says_so_and_never_sells` |
| Renewal arrives after the end | Access back without a connect | `test_a_late_renewal_brings_an_ended_plan_back_without_a_connect` |
| Ended purchase, never connected | Not "found"; connect refused `PURCHASE_EXPIRED` | `test_an_ended_purchase_cannot_be_connected` |
| Renewal of a refunded purchase | Not applied; `CONFLICTING_EVIDENCE` | `test_a_refunded_purchase_is_not_renewed` |
| One purchase on two cameras, any channel, any period | Refused by the database alone | `test_database_keeps_one_purchase_on_one_camera_in_every_channel` (3 channels) |
| Higher plan from another channel (a customer-reported symptom) | Connects; plan and team limit move together | `test_a_higher_plan_from_another_channel_is_connected_not_refused` |

| Renewal of a cheaper plan | The cheaper plan applies once its period covers now | `test_a_cheaper_plan_at_renewal_takes_effect_when_its_period_starts` |
| Extension (same start, later end) | Reaches the camera (migration 0008) | `test_an_extension_with_the_same_start_reaches_the_camera` |
| Older year arriving after the next one | Written; the current year is usable | `test_an_older_period_that_arrives_late_is_still_written` |
| Period not started yet | `scheduled`, not ended | `test_a_period_that_has_not_started_is_scheduled_not_ended` |
| Ending older than 30 days | History, not the headline | `test_an_old_ending_is_history_not_the_headline` |
| Plan ended with no store record | Names its own plan and end date | `test_an_ended_plan_without_a_store_record_names_its_own_plan_and_date` |
| Refund after a connect, before the check | `REFUNDED_OR_REVOKED`, not "checking" | `test_a_refund_after_connect_is_not_hidden_behind_checking` |
| Trial plus a paid purchase of the same plan | Prompt to connect the paid one | `test_a_trial_does_not_hide_a_paid_plan_waiting_to_be_connected` |
| Another household's channel event for this family's purchase | Refused `PURCHASE_OWNED_ELSEWHERE` | `test_another_household_cannot_put_this_familys_purchase_on_its_camera` |
| Same instant, other offset | A replay, not a conflict | `test_the_same_instant_in_another_offset_is_a_replay` |

The ten rows above came from the independent critique (`audits/RESEARCH_AND_ENGINEERING_CRITIQUE_2026-10-11.md`). All ten of these tests fail on the pre-fix code; that was checked in a worktree of the previous commit. An eleventh critique test, `test_checking_names_the_plan_that_was_connected`, passes on both versions; its refund case is the refund row above.

Customer evidence cards are dated, paraphrased and executable: `tests/test_customer_evidence.py` (8 cases).

## Mutation

M25–M40 in `scripts/mutate.py` remove one access guarantee each. M36: renewal not applied. M37: ended purchase connectable. M38: ownership check. M39: plan from all periods. M40: scheduled read as ended. M19 now drops the exclusion constraint. The latest run and any survivor: `artifacts/mutation/mutation-report.md`, summarised in [RELEASE_PROOF_2026-10-11.md](RELEASE_PROOF_2026-10-11.md).
