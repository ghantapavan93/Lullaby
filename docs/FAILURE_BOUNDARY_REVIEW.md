# Provider and failure boundaries: what is tested, what isn't

Each combination is classified against the current code:

| Class | Meaning |
|---|---|
| **Tested** | A test exercises it and passes |
| **Defect (fixed)** | Reproduced, then repaired; the ledger has it |
| **Design gap** | Not implemented, on purpose or not yet |
| **Unverified risk** | Might fail; not tested |
| **Out of scope** | A deliberate choice not to model proprietary or production behaviour |

| Combination | Class | Evidence |
|---|---|---|
| Pending verification with contradictory evidence | Defect (fixed) → Tested | `test_a_store_contradiction_after_connect_is_not_hidden_behind_checking` (ledger K1) |
| Billing grace vs entitlement | Tested | `test_apple_grace_keeps_access_until_the_grace_end`; a later store word inside a written window → `CONFLICTING_EVIDENCE` (K7) |
| Billing retry vs true expiration | Tested | `test_billing_retry_without_grace_removes_access_but_keeps_the_camera`, `test_store_reported_expiry_is_not_called_a_missing_renewal` |
| Refund vs delayed renewal | Tested | `test_a_refunded_purchase_is_not_renewed`, `test_refund_during_grace_ends_access_now` |
| Cross-channel identity uncertainty | Design gap | Website and store purchases are separate; two on one camera is `DUPLICATE_PURCHASE`. No identity linking (`docs/FUTURE_DESIGNS.md` §3) |
| Duplicate provider notification | Tested | `test_provider_event_replay_is_a_noop_and_changed_replay_is_refused`, `test_a_renewal_delivered_twice_adds_one_period` |
| Event arrival out of order | Tested (by version) | `test_late_older_provider_event_changes_nothing`, `test_a_late_grace_event_does_not_undo_a_recovery`. Real stores send no version: a design gap |
| Worker crash before provider submission | Tested | Recovery suite: the intent stays SUBMITTED and the same key is re-queued (`reconcile_intents`) |
| Worker crash after provider submission | Tested | `test_worker_crash_recovers_to_one_charge_and_usable_access` (a real OS process exit) |
| Lost provider response | Tested | `test_rt_provider_outcomes_are_recorded_distinctly` (red team), reconciliation from the ledger |
| Recovery after restart | Tested | The worker reconciles on start; the demo reset retries under a lock timeout (`test_a_scenario_reset_never_fails_while_a_page_keeps_polling`) |
| Stale client cache | Tested | `test_stale_view_is_refused_with_the_current_revision`; for video, `STALE_EVIDENCE_VIEW` |
| Authentication revocation | Tested | `test_rt_revoked_member_is_refused_on_every_route` (every `/v1` route, video included) |
| Database transaction contention | Tested | `test_concurrent_connects_of_one_purchase_bind_exactly_once`, `test_repeated_and_concurrent_analysis_requests_make_one_run` |
| Timezone boundaries | Tested (partly) | `test_same_instant_timezone_replay`, `test_grace_names_the_last_day_with_access`. Parent dates are UTC dates: an unverified risk for late-evening US times |
| Provider evidence not yet trustworthy (unsigned) | Out of scope | No real notifications are received; signature verification is a proposed component |
| A four-process server under load | Defect (open) | The soak FAILED; narrowed to `accept()` on a shared Windows socket (`audits/SOAK_DIAGNOSIS_2026-10-11.md`) |
| Two public reviewers acting at once | Design gap (release-blocking for a public demo) | One shared demo household; a reset by one visitor replaces the other's state (see the readiness verdict) |
