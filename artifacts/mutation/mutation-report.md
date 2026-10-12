# Mutation results

Generated 2026-10-11T16:00:52+00:00 by `python scripts/mutate.py `.

Killed 66 of 68 mutants.

| Mutant | Guarantee | Verdict | Caught by (first failing test) | Seconds |
|---|---|---|---|---|
| BASELINE | unmutated copy | **GREEN** | `—` | 324.6 |
| M01 | A purchase decision must belong to the requesting household | **KILLED** | `tests/redteam/test_rt_commerce.py::test_rt_decision_belongs_to_its_household` | 26.5 |
| M02 | A correction must name the revision the parent saw (expected_revision) | **KILLED** | `tests/test_corrections_and_acks.py::test_stale_screen_correction_gets_revision_conflict` | 163.7 |
| M03 | Idempotency: a retry with the same key replays; a different payload is rejected (app check) | **KILLED** | `tests/redteam/test_rt_commerce.py::test_rt_same_key_from_concurrent_separate_clients_creates_one_intent` | 29.7 |
| M04 | Idempotency: one intent per (household, key) (database constraint) | **KILLED** | `tests/test_commerce.py::test_unique_constraint_is_the_backstop_without_the_lock` | 158.7 |
| M05 | A camera on the wrong mount is blocked; a plan offer can't fix hardware | **KILLED** | `tests/redteam/test_rt_commerce.py::test_rt_hardware_blocked_decision_never_produces_a_provider_submission` | 23.1 |
| M06 | An unknown provider outcome stays UNKNOWN; it is never turned into CONFIRMED | **KILLED** | `tests/redteam/test_rt_commerce.py::test_rt_provider_outcomes_are_recorded_distinctly[pending-UNKNOWN-PENDING]` | 29.3 |
| M07 | No camera evidence means coverage is unknown or partial, never full | **KILLED** | `tests/test_briefing_rules.py::test_confirmed_zero_is_distinct_from_unknown` | 141.6 |
| M08 | A household-owned row is loaded only within the caller's household | **KILLED** | `tests/redteam/test_rt_commerce.py::test_rt_decision_belongs_to_its_household` | 24.2 |
| M09 | A revocation committed while a request waits for the lock still stops it | **KILLED** | `tests/critic/test_held.py::test_revocation_committed_while_waiting_for_the_lock_stops_the_write` | 18.2 |
| M10 | An older acknowledgment can never move a caregiver's head backwards | **KILLED** | `tests/redteam/test_rt_acks.py::test_rt_delayed_older_ack_cannot_override_newer` | 21.1 |
| M11 | No offers on safety surfaces (live view, safety alert) | **KILLED** | `tests/test_growth.py::test_no_offer_is_ever_attached_on_a_safety_surface` | 172.5 |
| M12 | While a purchase is being confirmed, don't sell it again | **KILLED** | `tests/redteam/test_rt_commerce.py::test_rt_purchase_being_confirmed_is_not_offered_again` | 40.7 |
| M13 | Purchase re-runs the rules against the camera as it is now | **KILLED** | `tests/test_camera_plans.py::test_a_plan_that_starts_after_the_check_is_not_sold_again` | 148.8 |
| M14 | Client prices can't override server terms | **KILLED** | `tests/redteam/test_rt_commerce.py::test_rt_mismatched_expected_terms_are_rejected[expected0]` | 26.6 |
| M15 | A refund (revoked claim) removes the plan | **KILLED** | `tests/test_access.py::test_refund_after_activation_revokes_access_and_keeps_history` | 87.5 |
| M16 | A camera's plan doesn't depend on the order its claims arrived | **KILLED** | `tests/test_camera_plans.py::test_order_and_duplicates_never_change_the_plan` | 143.5 |
| M17 | A reported sleep claim inside watched time where the camera saw no sleep is contested | **KILLED** | `tests/critic/test_evidence_and_acks.py::test_correction_cannot_silently_overrule_camera_observation` | 14.3 |
| M18 | One live upgrade per camera (database backstop only) | **KILLED** | `tests/test_commerce.py::test_database_allows_one_live_upgrade_per_camera` | 155.6 |
| M19 | A purchase or code names one camera only (database backstop only) | **KILLED** | `tests/test_access.py::test_database_keeps_one_purchase_on_one_camera_in_every_channel[web]` | 115.8 |
| M20 | The app's own unconfirmed purchase counts as being confirmed (no offer again) | **KILLED** | `tests/redteam/test_rt_commerce.py::test_rt_purchase_being_confirmed_is_not_offered_again` | 35.9 |
| M21 | A newly pending higher plan moves the household version (earlier offers go stale) | **KILLED** | `tests/test_camera_plans.py::test_a_second_pending_purchase_makes_earlier_offers_stale` | 142.9 |
| M22 | A re-delivered record doesn't change the recorded-sleep fingerprint | **KILLED** | `tests/redteam/test_rt_evidence.py::test_rt_duplicate_record_does_not_require_renewed_review` | 37.3 |
| M23 | Two households redeeming one code at once get a clear refusal, not a server error | **KILLED** | `tests/test_camera_plans.py::test_one_code_redeemed_by_two_households_at_once_lands_on_one_camera` | 146.3 |
| M24 | A killed experiment admits no one and counts no exposure | **KILLED** | `tests/redteam/test_rt_experiments.py::test_rt_kill_switch` | 44.6 |
| M25 | A family that already paid is never offered the same capability again | **KILLED** | `tests/test_access.py::test_pending_evidence_waits_and_never_offers_a_purchase` | 89.5 |
| M26 | A purchase on another camera is refused by the connect command itself | **KILLED** | `tests/test_access.py::test_purchase_bound_to_another_camera_is_not_moved` | 80.1 |
| M27 | The provider's highest version wins, whatever order events arrive in | **KILLED** | `tests/test_access.py::test_late_older_provider_event_changes_nothing` | 83.5 |
| M28 | A refund of a connected purchase revokes it on that camera | **KILLED** | `tests/test_access.py::test_refund_after_activation_revokes_access_and_keeps_history` | 82.3 |
| M29 | A connect from an outdated screen is refused | **KILLED** | `tests/test_access.py::test_concurrent_connects_of_one_purchase_bind_exactly_once` | 85.3 |
| M30 | A camera that already has a paid plan is not given a duplicate | **KILLED** | `tests/test_access.py::test_duplicate_purchase_is_not_connected_to_the_same_camera` | 84.8 |
| M31 | Evidence for another family's purchase is refused with a clear answer | **KILLED** | `tests/test_access.py::test_another_familys_purchase_cannot_be_recorded_or_seen` | 89.4 |
| M32 | Capability verified is never inferred from operation completed | **KILLED** | `tests/test_access.py::test_three_outcomes_are_never_inferred_from_one_another` | 112.6 |
| M33 | A completed connect is not a verified capability until a check runs | **KILLED** | `tests/test_access.py::test_paid_not_active_connects_without_a_new_purchase` | 82.5 |
| M38 | Another household's purchase can't be put on this family's camera | **KILLED** | `tests/test_access.py::test_another_household_cannot_put_this_familys_purchase_on_its_camera` | 122.2 |
| M39 | A purchase grants the plan of the period covering now (a cheaper renewal applies) | **KILLED** | `tests/test_access.py::test_a_cheaper_plan_at_renewal_takes_effect_when_its_period_starts` | 120.8 |
| M40 | A period that hasn't started is scheduled, not ended | **KILLED** | `tests/test_access.py::test_a_period_that_has_not_started_is_scheduled_not_ended` | 118.1 |
| M41 | A paid, connected period that hasn't started reads as scheduled, not as no plan | **KILLED** | `tests/test_subscription_model.py::test_late_arriving_prior_period` | 241.1 |
| M42 | A check sorts after the connect it saw (commit time, not transaction start) | **KILLED** | `tests/test_subscription_model.py::test_concurrent_connect_and_verification_ordering` | 249.9 |
| M43 | An upgrade with unmet prerequisites stops with a diagnostic before changing anything | **KILLED** | `tests/test_migration_upgrade.py::test_a_purchase_split_across_cameras_blocks_the_upgrade_and_changes_nothing` | 203.3 |
| M44 | Entitlement time is read through one controllable clock | **KILLED** | `tests/test_billing_lifecycle.py::test_apple_grace_keeps_access_until_the_grace_end` | 122.8 |
| M45 | A demo reset takes every lock up front, so an open page is never a deadlock victim | **SURVIVED** | `—` | 347.9 |
| M46 | A store contradiction is never hidden behind 'connected, checking' | **KILLED** | `tests/test_subscription_model.py::test_a_store_contradiction_after_connect_is_not_hidden_behind_checking` | 274.2 |
| M47 | Billing retry outside a grace period grants no access | **KILLED** | `tests/test_billing_lifecycle.py::test_billing_retry_without_grace_removes_access_but_keeps_the_camera` | 128.5 |
| M48 | A store grace period keeps access on the camera until the grace end | **KILLED** | `tests/test_billing_lifecycle.py::test_apple_grace_keeps_access_until_the_grace_end` | 129.0 |
| M49 | A late grace event can't undo a recovery | **KILLED** | `tests/test_billing_lifecycle.py::test_a_late_grace_event_does_not_undo_a_recovery` | 128.4 |
| M50 | A grace period whose end has passed is billing retry, not grace | **KILLED** | `tests/test_billing_lifecycle.py::test_apple_grace_keeps_access_until_the_grace_end` | 118.2 |
| M51 | A paused plan says it is paused, not that it ended | **KILLED** | `tests/test_access.py::test_every_scenario_reaches_its_expected_status_and_never_sells[plan_paused]` | 101.5 |
| M52 | A store-reported expiry is not called a missing renewal | **KILLED** | `tests/test_billing_lifecycle.py::test_store_reported_expiry_is_not_called_a_missing_renewal` | 127.5 |
| M53 | A purchase in billing retry stays on its camera; connecting it again is refused | **KILLED** | `tests/test_billing_lifecycle.py::test_billing_retry_without_grace_removes_access_but_keeps_the_camera` | 125.7 |
| M54 | When the store ends access early but a claim still grants it, the camera says so | **KILLED** | `tests/test_billing_lifecycle.py::test_a_later_store_word_inside_a_written_grace_window_is_not_hidden[later0]` | 132.3 |
| M55 | A plan whose payment is retrying or paused is never sold again | **KILLED** | `tests/test_billing_lifecycle.py::test_a_plan_whose_payment_is_retrying_or_paused_is_never_sold_again[apple-DID_RENEW-DID_FAIL_TO_RENEW-PAYMENT_RETRYING]` | 129.6 |
| M56 | Grace names the last day with access, not the end instant | **KILLED** | `tests/test_billing_lifecycle.py::test_apple_grace_keeps_access_until_the_grace_end` | 129.4 |
| M57 | A lower plan's grace doesn't hide a higher plan that works | **KILLED** | `tests/test_billing_lifecycle.py::test_a_higher_plan_on_the_camera_is_not_hidden_by_a_lower_plans_billing` | 129.8 |
| M58 | A late-delivered failure is not timed as a negative recovery | **KILLED** | `tests/test_access_metrics.py::test_durations_come_from_record_times` | 123.5 |
| M59 | Reading recorded evidence checks the camera's plan | **KILLED** | `tests/test_video_evidence.py::test_the_evidence_needs_a_plan_with_video_history` | 290.4 |
| M60 | A signed media link expires | **KILLED** | `tests/test_video_evidence.py::test_media_bytes_need_a_valid_unexpired_link` | 302.6 |
| M61 | A signed media link opens only the clip it names | **KILLED** | `tests/test_video_evidence.py::test_media_bytes_need_a_valid_unexpired_link` | 324.1 |
| M62 | A stalled feed is missing evidence, not stillness | **KILLED** | `tests/test_video_evidence.py::test_a_scripted_clip_is_measured_as_scripted` | 285.8 |
| M67 | A stall right after a pan is still a stall: change carried over the lag doesn't count | **KILLED** | `tests/test_video_evidence.py::test_the_shipped_demo_clip_is_measured_as_scripted_in_every_window` | 309.6 |
| M68 | Passing tests on other code never certify this code | **KILLED** | `tests/test_proof.py::test_a_report_from_other_code_is_never_certified` | 230.2 |
| M63 | Missing frames are missing evidence, not stillness | **SURVIVED** | `—` | 312.5 |
| M64 | A report from an outdated evidence screen is refused | **KILLED** | `tests/test_video_evidence.py::test_a_contradicting_annotation_keeps_the_measurement` | 271.0 |
| M66 | A comparison's refusal probes leave nothing behind | **KILLED** | `tests/test_compare.py::test_probes_are_rolled_back` | 159.4 |
| M65 | Camera motion isn't reported as movement in the scene | **KILLED** | `tests/test_camera_plans.py::test_one_code_redeemed_by_two_households_at_once_lands_on_one_camera` | 147.1 |
| M34 | Two statuses at the same provider version are a conflict, not a guess | **KILLED** | `tests/test_access.py::test_same_version_contradiction_is_conflicting_and_cannot_be_connected` | 84.8 |
| M36 | A confirmed renewal of a connected purchase reaches its camera | **KILLED** | `tests/test_access.py::test_every_scenario_reaches_its_expected_status_and_never_sells[renewal_reflected]` | 102.5 |
| M37 | An ended purchase can't be connected as if it were current | **KILLED** | `tests/test_access.py::test_an_ended_purchase_cannot_be_connected` | 118.9 |
| M35 | A repeated connect that changed nothing does not reopen verification | **KILLED** | `tests/test_access.py::test_repeated_activation_is_recorded_but_does_not_reopen_verification` | 105.4 |
