# Domain

## Bounded contexts

| Context | Language | Owns |
|---|---|---|
| **Tenancy** | household, member, role, revocation | who may see or change what |
| **Evidence** | evidence event, source (CAMERA / PARENT / CAREGIVER), camera availability, sleep interval, correction (supersedes) | what was recorded, by whom, never rewritten |
| **Briefing** | window (19:00–07:00 in the household's named timezone), revision, fact, fingerprint, status | what the night looks like given the evidence |
| **Acknowledgment** | acknowledgment, displayed revision, standing acknowledgment, needs review | which version a person viewed |
| **Camera plans** | camera, channel claim (trial, Apple, Google, web, activation code, simulated purchase), transaction, derived plan, checking, team-member limit | what each camera is entitled to, derived from every channel's events |
| **Eligibility** | feature, requirement, blocker (hardware / subscription), decision (per camera), offer, catalog version, policy version, state revision, surface | whether a feature works on a camera, and whether paying would help |
| **Purchasing** | intent, idempotency key, operation key, submission authorization, outcome, reconciliation | one simulated charge, recorded truthfully |
| **Growth** | experiment, assignment, render receipt (exposure), analytics event | measuring, without inventing results |

## Facts on a briefing

| Fact key | Meaning | Unknown when |
|---|---|---|
| `recorded_sleep` | Union of effective sleep intervals in the window, with sources. Not a clinical measure. Contested time is never folded into an exact total. | no sleep records |
| `sleep_onset` / `final_wake` | Earliest start / latest end of recorded sleep, with sources | no sleep records |
| `observation_coverage` | Usable camera observation ÷ window elapsed time. States: unknown, none, partial, full | no camera availability report at all |
| `unobserved` | Window minus usable observation, each gap labeled camera-reported-unavailable or no-report | coverage unknown |

Status: `unknown_observation` → `conflicting_evidence` → `no_observation` → `incomplete_observation` → `complete` (first match wins).

A **fingerprint** is sha256 over a fact's value, provenance and uncertainty. A number that stays the same while its source changes gets a new fingerprint.

## Invariants and where they are enforced

| # | Invariant | Database | Application | Test |
|---|---|---|---|---|
| I1 | A household's rows never reference another household's rows | composite FKs `(household_id, id)` | `load_owned` scopes every lookup | `test_isolation.py` |
| I2 | Evidence, revisions, acknowledgments and intent history are never rewritten | `reject_mutation()` triggers | no update paths | `test_original_evidence_is_retained…`, `test_intent_history_is_append_only` |
| I3 | At most one correction per evidence event | unique `supersedes_event_id` | `ALREADY_CORRECTED` check | `test_simultaneous_corrections_one_wins…` |
| I4 | One revision number per window | unique `(window_id, revision)` | household + window locks | same |
| I5 | Coverage counts only usable camera observation; unknown ≠ zero | — | `briefing.compute_facts` | `test_briefing_rules.py` incl. property tests |
| I6 | Duplicates and overlaps never add minutes; real elapsed time across DST | — | `intervals.py` (UTC-normalized) | grid property test, DST test |
| I7 | A correction stays inside its window, never targets a record spanning windows, and yields one new revision of that window only | — | `apply_correction` (`OUTSIDE_WINDOW`, `TARGET_SPANS_WINDOWS`) | `test_correction_cannot_leave_its_window`, `…does_not_touch_other_windows` |
| I8 | A replayed request returns the original result; a reused key with new content is rejected | unique `(household_id, client_event_id)` | request hash comparison before freshness | `test_replayed_correction…`, `test_reused_correction_key…` |
| I9 | An acknowledgment covers exactly the displayed revision and facts | unique `(member_id, revision_id)` | revision id required; fact keys validated | `test_stale_screen_acknowledgment_binds…` |
| I10 | A late older acknowledgment cannot replace a newer one | conditional upsert on `ack_heads` | per-fact status by highest revision | `test_late_old_acknowledgment…` |
| I11 | An offer exists only when every blocker is a plan and the offered plan unlocks the feature | `CHECK offer is null or status='BLOCKED'` | `entitlements.evaluate` re-runs the rules | parametrized product over features × plans × mounts |
| I12 | Nothing is prepared from a stale, foreign, offer-less, mismatched or re-priced decision | composite FK intent→decision | `purchases.prepare` | `test_commerce.py` |
| I13 | One intent per household and idempotency key under concurrency | unique `(household_id, idempotency_key)` | household lock; replay before freshness | 8 simultaneous requests → 1 intent |
| I14 | The provider is called only after SUBMITTED commits, always with the same op key | unique `provider_operation_key` | `authorize_submission` | `test_recovery.py` |
| I15 | No outcome without provider evidence; UNKNOWN never becomes CONFIRMED without SUCCEEDED | — | `record_outcome`, `reconcile_intents` | pending/settle tests, real crash test |
| I16 | Exposure only from a render; one per member, surface and subject | unique `(household_id, render_id)` | client sends receipts only from IntersectionObserver | `test_growth.py`, e2e |
| I17 | Analytics accept only typed, allow-listed properties; clients can't post server events | PK `(household_id, event_id)` | `growth._validate` (counts, flags, enums, identifiers) | `test_analytics_are_idempotent_and_allow_listed`, critic F-05/F-10 |
| I18 | At most one upgrade in flight per camera | partial unique index `uq_intents_one_live_upgrade` | `UPGRADE_IN_PROGRESS` | `test_one_upgrade_is_never_charged_twice`, `test_database_allows_one_live_upgrade_per_household` |
| I19 | A confirmed purchase never lowers the plan | — | `_grant_entitlement` compares plan rank | `test_a_confirmed_purchase_never_lowers_the_plan` |
| I20 | A correction is a selection only inside the camera record it replaces | — | `camera_basis` on the correction; extensions into watched awake time are contested | `test_stretching_a_correction_into_watched_awake_time_is_contested` |

## State machines

Intent: `PREPARED → CANCELLED_BEFORE_SUBMISSION` · `PREPARED → SUBMITTED → CONFIRMED | FAILED | UNKNOWN` · `UNKNOWN → CONFIRMED | FAILED | UNKNOWN`. Every transition is a row in `intent_transitions`.

Outbox job: `pending → in_progress (leased) → done`, or back to `pending` with backoff, `dead` after 8 attempts. An expired lease makes a job claimable again.

## Events (outbox kinds)

- `notify_revision` — written with a correction's revision; delivers in-app notifications to the other active members, once (unique per member, window, revision).
- `submit_purchase` — written with a PREPARED intent; drives submission and recovery.

## Invariants added after the research gate (2026-10-10)

| # | Invariant | Database | Application | Test |
|---|---|---|---|---|
| I21 | A camera's plan is a function of the *set* of its channel claims: order and duplicates never change it, and a revoked transaction never grants | append-only claims; unique `(channel, external_ref, event_status)` | `entitlements.effective_plan` (pure) | property `test_order_and_duplicates_never_change_the_plan` (400 examples), `test_revoked_transaction_never_grants_in_any_order` |
| I22 | Plan and team-member limit come from one computation | — | `effective_plan` returns both | property `test_team_limit_always_matches_the_plan` |
| I23 | A purchase being confirmed is never sold again | — | UNKNOWN + `PURCHASE_BEING_CONFIRMED`, no offer | `test_a_purchase_being_confirmed_is_never_sold_again` |
| I24 | An activation code is active on one camera only | partial unique index `uq_activation_code_one_camera` | `CODE_ALREADY_REDEEMED` | `test_activation_code_works_on_one_camera_only` |
| I25 | Eligibility, offers and purchases are per camera; buying for one camera never changes another | composite FKs decision/intent → device | `camera_for`, `evaluate_now(device)` | `test_each_camera_is_judged_by_its_own_mount_and_plan`, `test_a_purchase_for_one_camera_never_changes_another` |
| I26 | Multi-night values reflect every past-night correction on the next read, and say how many nights they count | — | `engine.week_view` reads current revisions | `test_week.py` |
| I27 | No offer on safety surfaces (live view, alerts) | — | `decisions.SAFETY_SURFACES` | `test_no_offer_is_ever_attached_on_a_safety_surface` |
| I28 | No experiment outcome is read when assignment or exposure fails a sample-ratio check | — | `growth.sample_ratio` (p < 0.001) | `test_sample_ratio_check_flags_broken_exposure…`, `bench/experiment_check.py` |
| I29 | A request holds at most one application database connection | pool 10 + 10 | `db.keep()` | `test_every_request_kind_uses_one_connection` (fails on the old code) |
| I30 | Earlier offers go stale whenever what may be sold changes, including a newly pending plan | — | `claims._commercial_view` moves the household version | `test_a_second_pending_purchase_makes_earlier_offers_stale`; model-based test |
| I31 | A killed experiment admits no one new, serves its control variant and counts no exposure | `experiment_controls` (migration 0005) | `growth.serve`, `growth.record_render` | `test_rt_kill_switch` |
| I32 | The worker's claim cost doesn't grow with finished jobs | partial index `ix_outbox_claimable` (migration 0004) | `worker.claim` | `bench/outbox_index.py` (356 ms → 0.026 ms at 1M finished jobs) |
| I33 | A purchase belongs to one family; evidence for it can't be recorded under another | `purchase_refs` PK + `fk_evidence_owner` (migration 0006) | `purchase_evidence._claim_owner` | `test_another_familys_purchase_cannot_be_recorded_or_seen`, `test_database_refuses_evidence_under_a_household_that_does_not_own_the_purchase` |
| I34 | A purchase's state is a function of its set of provider events; highest version wins; a same-version contradiction is conflicting | `uq_evidence_event` | `purchase_evidence.purchase_state` | `test_purchase_state_depends_only_on_the_set_of_events` (property) |
| I35 | A paid purchase is never sold again: no access status has an offer, and decisions withhold an offer an unconnected purchase would satisfy | — | `access.assess`, `decisions._withhold_if_already_bought` | `test_every_scenario_reaches_its_expected_status_and_never_sells`, `test_unconnected_purchase_withholds_the_offer_for_what_it_unlocks_only` |
| I36 | A purchase is connected to one camera and never moved automatically; a camera with a paid plan gets no duplicate | `uq_claim_event`, `ex_claim_one_camera` (migration 0007) | `access.bind`, `claims._already_recorded` | `test_purchase_bound_to_another_camera_is_not_moved`, `test_concurrent_connects_of_one_purchase_bind_exactly_once`, `test_duplicate_purchase_is_not_connected_to_the_same_camera` |
| I37 | Connected is not verified, and verified is not used: three outcomes from three kinds of evidence | append-only `access_actions`, `access_checks` | `access.outcome_review` | `test_three_outcomes_are_never_inferred_from_one_another` |
| I38 | Reading access writes nothing | — | `access.assess` | `test_reading_access_writes_nothing_but_the_authorization_record` |
| I39 | A confirmed renewal of a connected purchase reaches the same camera in the same transaction; an ended plan is reported as ended, never as a paywall | `uq_claim_event` per period incl. end (migrations 0007, 0008), `ex_claim_one_camera` | `purchase_evidence.apply_periods`, `entitlements.effective_plan`, `access` (`PLAN_EXPIRED`) | `test_renewal_of_a_connected_purchase_keeps_the_camera_active`, `test_an_ended_plan_without_renewal_says_so_and_never_sells`, `test_database_keeps_one_purchase_on_one_camera_in_every_channel` |
