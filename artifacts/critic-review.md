# Red-team review: Nanit Proof Layer (`proof-layer/`)

Reviewer: independent critic. Scope: spec §7–10 against `app/`, `static/app.js`, the migration and the existing tests.
Nothing under `app/`, `static/`, `migrations/` or the existing tests was changed.

## How to reproduce

```bash
cd proof-layer
DATABASE_URL=postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_critic \
PROVIDER_DATABASE_URL=postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_critic \
.venv/Scripts/python -m pytest -q -p no:cacheprovider tests/critic
```

Result on the reviewed code: **24 failed, 9 passed**.

- The 24 failures are the defect reproductions. 17 findings, some parametrized.
- The 9 passes are in `test_held.py`. They guard attacks that held.

Each failing test passes once its defect is fixed. Where a different repair is also acceptable, such as refusing the request instead of handling it, the test accepts that repair too.

## Findings

| ID | Sev | Violated invariant | Test (`tests/critic/…`) | Root cause | Smallest correct repair |
|---|---|---|---|---|---|
| F-01 | P1 | One upgrade is charged at most once per household. | `test_commerce_and_worker.py::test_one_upgrade_is_never_charged_twice` | `app/purchases.py:77-145` (`prepare` ignores in-flight intents); `app/purchases.py:161` (the pre-submission recheck sees only committed plan state) | Refuse a new intent, or cancel it before submission, while another intent for the same household and plan or feature is PREPARED, SUBMITTED or UNKNOWN. A partial unique index on `(household_id, terms->>'plan_code') WHERE state IN ('PREPARED','SUBMITTED','UNKNOWN')` adds a database backstop. Return 409 `PURCHASE_IN_FLIGHT`. |
| F-02 | P1 | A confirmed purchase never lowers the household's plan. | `test_commerce_and_worker.py::test_a_confirmed_purchase_never_lowers_the_plan` | `app/purchases.py:209-220` (`_grant_entitlement` overwrites `plan_code` unconditionally) | Grant only when the purchased plan's rank is higher than the current plan's. Otherwise record the confirmation and flag it for refund or review. F-01's in-flight guard also prevents the ordering that triggers this. |
| F-03 | P1 | A parent correction cannot turn a disagreement with camera observation into an exact total (§8: unrelated disagreements stay unresolved; supersession does not establish truth). | `test_evidence_and_acks.py::test_correction_cannot_silently_overrule_camera_observation` | `app/briefing.py:109` (`supersedes_event_id is None` exempts every correction from the contest rule) | Exempt only the part of a correction that lies inside its target's original interval. Run the existing contest rule on any extension into usable, camera-observed time with no camera sleep. The alternative is to reject such extensions with 422. |
| F-04 | P2 | UI copy matches behavior: "Time the camera didn't observe is never counted as sleep." | `test_evidence_and_acks.py::test_unobserved_time_copy_matches_correction_behaviour` | `static/app.js:382`; `app/briefing.py:115-129` (corrected sleep inside `unobserved` gaps is counted exactly) | Report correction minutes that fall in unobserved gaps as a separate `parent_reported_unobserved_minutes`, or exclude them from the exact total. The alternative is to change the copy to say parent corrections can count that time. |
| F-05 | P1 | Analytics never store free text or care notes (`growth.py` docstring; spec §11). | `test_access_growth_and_ui.py::test_analytics_rejects_free_text_in_any_shape[nested-object, list-of-strings, short-free-text]` | `app/growth.py:105-113` (`_validate` checks only top-level `str` longer than 64 characters) | Give each property a type: int, bool, or an enum of reason or state codes. Reject `dict` and `list` values, and any string outside the property's enum. |
| F-06 | P1 | An acknowledgment response describes what was actually recorded. A second acknowledgment of the same revision for more facts is not silently dropped. | `test_evidence_and_acks.py::test_second_acknowledgment_of_same_revision_records_its_facts` | `app/acks.py:47-55` (`ON CONFLICT (member_id, revision_id) DO NOTHING` keeps the first fact subset, yet the response says "You acknowledged the current version.") | Key acknowledgments per fact, as `(member_id, revision_id, fact)` rows, so a later request adds facts. The alternative is to return 409 when the requested fact set differs from the stored one. `has_acknowledged_current` should require all facts. |
| F-07 | P2 | Correcting one window never changes another window's evidence (§7: limit corrections to their window; §12: the other window stays unchanged). | `test_evidence_and_acks.py::test_correction_never_changes_another_windows_evidence` | `app/engine.py:257-261` (checks only that the target overlaps this window and that the new interval is inside it; the whole target is superseded) | Reject with 422 when the target extends outside the window, or recompute and revise every window the target overlaps. |
| F-08 | P2 | An unchanged number with changed meaning requires review (§7). | `test_evidence_and_acks.py::test_moving_recorded_sleep_is_a_reviewable_change` | `app/briefing.py:175-180` (the `recorded_sleep` fingerprint excludes `intervals`) | Include the canonical `(start, end, source)` interval list in the `recorded_sleep` semantic fingerprint, or add an acknowledgeable `timeline` fact. |
| F-09 | P2 | An UNKNOWN outcome keeps being reconciled until the provider settles it. | `test_commerce_and_worker.py::test_unknown_outcome_is_still_reconciled_by_a_running_worker` | `app/worker.py:36,69` (submit job goes to dead-letter after 8 attempts, about 40 s at 5 s each); `app/worker.py:178` (reconcile runs only at process start) | Do not dead-letter submit jobs whose intent is SUBMITTED or UNKNOWN; back off to a ceiling instead. The alternative is to call `reconcile_intents` periodically inside the `main()` loop. |
| F-10 | P2 | Server-owned analytics cannot be forged, and analytics attribution is per household. | `test_access_growth_and_ui.py::test_clients_cannot_forge_server_analytics_events`, `::test_analytics_event_ids_do_not_collide_across_households` | `app/growth.py:50-53` (server events listed in the client allow-list); `app/models.py:391` (`event_id` is a global primary key) | Split the schema into client events and server-only events; `/v1/analytics` accepts only client events. Make the key `(household_id, event_id)`. |
| F-11 | P2 | An exposure receipt records only the assigned variant. A reused render id with different content is a conflict, not a silent no-op. | `test_access_growth_and_ui.py::test_variant_is_never_recorded_without_its_experiment`, `::test_render_id_reuse_for_a_different_subject_conflicts` | `app/growth.py:73-95` (the variant is checked only when `experiment_key` is set; `on_conflict_do_nothing` ignores a different subject) | Reject a `variant` without an `experiment_key` with 422. On conflict, compare surface, subject and variant with the stored receipt and return 409 on mismatch. |
| F-12 | P2 | Comparing against a revision that doesn't exist is an error, not "everything changed". | `test_evidence_and_acks.py::test_changes_against_a_missing_revision_is_not_found` | `app/main.py:240-250` (`base=None` when the lookup misses, then `diff(None, latest)`) | Return 404 `NOT_FOUND` when `since_revision` does not match a revision of this window. |
| F-13 | P2 | Roles denied `purchase.read` cannot read purchase data through another route. | `test_access_growth_and_ui.py::test_caregiver_cannot_read_purchase_data_through_xray` | `app/security.py:69` (`xray.read` includes caregiver); `app/main.py:355-385` (X-Ray returns intent transitions, provider ledger and purchase analytics) | Return commerce sections of `/v1/xray` only when `purchase.read` is also authorized, or restrict `xray.read` to owner and admin. Apply the same rule to `/v1/household/commercial`, which has no `authorize` call. |
| F-14 | P2 | Scenario controls are disabled outside the local demo configuration (§10), and the development-only token secret is not a published default. | `test_access_growth_and_ui.py::test_dev_routes_and_session_secret_are_not_on_by_default`, `::test_dev_worker_requires_a_session` | `app/main.py:37` (`PROOF_LAYER_DEV` defaults to `"1"`); `app/security.py:31` (default HMAC secret); `app/main.py:470` (`/v1/dev/worker` takes no credentials and drives every household's outbox, including the `crash_after_provider` kill switch) | Default `DEV` to off, and set it to 1 in `scripts/devdb.py` and the README run command. Refuse to start with DEV off and no `PROOF_LAYER_SECRET`. Require a session on `/v1/dev/worker` and `/v1/dev/reset`, and scope the worker to the caller's household. |
| F-15 | P2 | Input shapes are validated before side effects; bad input gets 4xx, never 500 (§10). | `test_access_growth_and_ui.py::test_oversized_or_unknown_inputs_are_rejected_not_500[feature_41_chars, idempotency_key_200_chars, mount_41_chars, unknown_provider_behaviour]` | `app/main.py` Pydantic models and header lack `max_length`. Limits are `app/models.py:258` (`feature`, 40), `:289` (key, 120) and `:82` (`mount`, 40). `app/main.py:445` lets `ValueError` escape. | Add `max_length` (or `Literal` or enum) to `DecisionIn.feature`, `MountIn.mount`, `BehaviorIn.behavior` and the `Idempotency-Key` header. Map `ValueError` to 422. |
| F-16 | P2 | An acknowledgment covers only facts that were displayed (§9: "verify the acknowledged fields were visible"). | `test_access_growth_and_ui.py::test_acknowledged_facts_are_all_displayed_on_the_briefing` | `static/app.js:15` (`FACTS` includes `sleep_onset`); `static/app.js:369-375` (four tiles, no "Fell asleep"); `static/app.js:345` ("the five facts above") | Render a `sleep_onset` tile, or send only the keys of tiles that were actually rendered as `displayed_facts`. |
| F-17 | P2 | The caregiver's screen uses the caregiver's own authority. | none (JavaScript-only; precise reference) | `static/app.js:152-162` (`refreshLatest` and `loadCommercial` always use `VIEWERS[0]`, the parent's token) | Fetch with `state.viewer`. Today a revoked or restricted caregiver's screen keeps receiving the latest revision and the members' review status through the parent's session. |

### Notes on the important ones

**F-01 and F-02 (commerce).** The defense against stale purchases is the recheck before submission in `authorize_submission`. It compares only committed household state. If the first purchase's provider reply is PENDING, a second intent prepared from the same decision, or for a higher plan, passes the recheck and gets submitted.

Reproduced end to end:

- **Same offer, two keys:** two SUCCEEDED provider charges and two CONFIRMED intents for one Memories upgrade.
- **Memories and Milestones prepared together:** Milestones confirms first. When Memories settles later, the plan drops back to Memories.

In-process tabs or retries that generate fresh keys reach this. The spec's single-key check (§12, "Concurrent matching purchase requests") does not cover it, but a second charge for an upgrade already in flight resolves no blocker (§9, step 8).

**F-03 (evidence).** `briefing.py` already contests a reported sleep interval in camera-observed time with no camera sleep. The same claim submitted as a correction becomes exact: 670 min, status `incomplete_observation`, no conflict.

The existing test `test_explicit_correction_is_a_selection_not_a_conflict` only shortens a record, so it never exercises this. Selecting a replacement for one record is not the same as overruling a different source.

**F-06 (acknowledgment).** The caregiver is told "You acknowledged the current version." Four of the five facts remain `not_acknowledged`, and `has_acknowledged_current` is `true`. The response contradicts what was stored.

## Attacks that held

| Attack | How it was checked |
|---|---|
| Token forgery: flipped signature, another member's signature, wrong version prefix, unknown member, garbage | `test_held.py::test_forged_or_mismatched_tokens_are_rejected`. Every case returns 401. |
| Revocation committed while a write waits for the household lock | `test_held.py::test_revocation_committed_while_waiting_for_the_lock_stops_the_write`. The test holds the lock in SQL, revokes, and commits. The request gets 403 `ACCESS_REVOKED`. |
| Cross-household dev controls: mount, scheduled provider behavior, resolving a foreign operation | `test_held.py::test_dev_routes_stay_inside_the_callers_household`. Resolve returns 404. Mount and schedule affect only the caller's household. The exception is `/v1/dev/worker`, which needs no session and is reported as F-14. |
| Mixed ids: own window with a foreign event, revision, decision or intent; foreign render subject | Existing `test_isolation.py` plus `test_held.py::test_render_receipt_for_foreign_subject_is_not_found`. Code review: `load_owned` and every subquery filter on `principal.household_id`, and composite foreign keys enforce it in the database. |
| Correction edges: zero length, camera_availability target, another window's record, `corrected_start` only, end exactly at the window edge, replay after a newer revision, re-correcting a superseded record, correcting a correction | `test_held.py::test_correction_edge_cases`. Each returns the documented code. A replay returns the original revision without creating a new one. |
| Acknowledgment with a revision from another window | `test_held.py::test_ack_with_another_windows_revision_is_rejected` returns 404. A late older acknowledgment cannot lower the head; existing tests cover this, and `acks.py` uses a conditional upsert under the household lock. |
| Purchase from a decision made before an upgrade | `test_held.py::test_purchase_against_a_decision_made_before_an_upgrade_is_stale` returns 409 `STALE_DECISION`. |
| Daylight-saving windows through real ingestion | `test_held.py::test_daylight_saving_windows_through_ingestion`. Fall-back night measures 780 min and spring-forward night 660 min; coverage is 100% and sleep equals elapsed time. There is no ingestion API, so the test uses `engine.ingest`. |
| Interval edges: touching, zero-length, edge clipping, duplicates | `test_held.py::test_interval_edges` |
| Price or currency tampering, offer mismatch, same key with different content, members without billing permission, caregiver purchase, caregiver correction, expired, catalog or policy change before PREPARED | Existing `test_commerce.py`, re-read against `purchases.prepare`. Terms come only from the decision snapshot. |
| Provider and worker: duplicate delivery, concurrent workers, crash between commits (in-process and real `os._exit`), lost response, unavailable provider, cancellation before submission, UNKNOWN only confirms on SUCCEEDED, the entitlement granted once per intent | Existing `test_recovery.py`, re-read against `worker.py` and `purchases.record_outcome`. The TERMINAL guard stops a double grant for the same intent. A reconcile racing an expired lease reuses the same key, and the provider row lock or primary key deduplicates. |
| XSS from server strings | Manual review of `static/app.js`. Every server-supplied free-form string (mount, labels, messages, trace ids, analytics properties, render ids) passes through `esc()`. The unescaped `table()` cells hold only server enums, numbers or counts. |
| UI shows success before CONFIRMED, silently swaps content, or hides staleness | Manual review. `purchasePanel` shows "Pending" until the server state changes. `poll()` refreshes `state.latest` but never replaces `screen.brief`; the stale banner and "earlier version" badge come from server revision numbers. Simulated money is labeled on the offer and in announcements. |

## Observations without a separate finding

- `catalog/policy change between PREPARED and SUBMITTED`: `authorize_submission` re-evaluates under the active versions but does not compare them with `intent.terms`. Price stays locked at prepare. That is defensible, but it is not stated. An unknown `catalog_version` in `commercial_config` raises `KeyError` (500) on every decision and keeps a PREPARED job retrying until it is dead-lettered.
- `record_outcome` trusts whatever ledger record exists for the key and does not check amount, currency or merchant against `intent.terms`. This is unreachable today because keys are `op_<intent id>`, but a cheap guard is missing.
- `static/app.js:634-637` hard-codes the correction date and offset (`2026-10-10…-04:00`). It is correct only for the demo night.
