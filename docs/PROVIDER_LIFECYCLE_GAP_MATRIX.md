# Provider lifecycle gap matrix

What the official Apple and Google documentation says, next to what the prototype does.
Facts and citations: `docs/PROVIDER_LIFECYCLE_FACTS.md` (checked 2026-10-11).
Code: `proof-layer/app/provider_lifecycle.py`, `purchase_evidence.py`, `access.py`.
Tests: `proof-layer/tests/test_billing_lifecycle.py` (frozen clock, PostgreSQL).

The prototype receives no store notifications. Every store event below is synthetic and enters through a development-only endpoint or a Scenario Studio step.

**Verdict key:**
- **Modelled:** behaves as documented, with a test.
- **Simplified:** the outcome is right, but the mechanism is ours.
- **Not modelled:** absent; stated here, not hidden.

## Access states

| Store fact | Prototype | Verdict | Test or mutant |
|---|---|---|---|
| Apple `DID_FAIL_TO_RENEW` + `GRACE_PERIOD`: keep access until `gracePeriodExpiresDate` | Evidence `grace`. While grace is the store's latest word, its window is written to the camera as a claim period. Status `BILLING_GRACE`: features work, the payment needs attention | Modelled | `test_apple_grace_keeps_access_until_the_grace_end`, M48 |
| Grace is opt-in (Apple) or configurable (Google) | No grace is ever assumed. A grace event must carry its end date or it is refused (`GRACE_NEEDS_END`). Only the grace window at the store's latest version is written | Modelled | `test_a_grace_event_needs_its_end_date`, `test_only_the_latest_grace_window_is_written` |
| Apple `DID_FAIL_TO_RENEW` with no subtype: you may stop service | Evidence `billing_retry`: no access; the purchase stays on its camera. Status `BILLING_RETRY` | Modelled | `test_billing_retry_without_grace_removes_access_but_keeps_the_camera`, M47 |
| Apple `GRACE_PERIOD_EXPIRED`: you may turn off access | `billing_retry`. A grace end that passes with no newer word also reads as billing retry | Modelled | M50 |
| Apple retries billing for up to 60 days | The retry window is whatever the event carries. The 60-day limit isn't enforced; the store's `EXPIRED/BILLING_RETRY` ends it | Simplified | — |
| Apple `DID_RENEW` + `BILLING_RECOVERY`: a new transaction | A verified period from the recovery date, written to the same camera. No new connect | Modelled | `test_billing_recovery_restores_access_on_the_same_camera` |
| Google silent grace (at least 1 day, still `ACTIVE`) | No event, so the purchase stays verified | Modelled (nothing to do) | — |
| Google `IN_GRACE_PERIOD`: access continues | `grace`, as for Apple | Modelled | `test_google_hold_recovery_resets_the_renewal_date` |
| Google recovery inside grace keeps the renewal date | The renewal event carries the original date; the period is written as sent | Modelled | `test_google_grace_recovery_keeps_the_renewal_date` |
| Google `ON_HOLD`: block access; recovery resets the billing date | `billing_retry`; `RECOVERED` writes a period from the recovery date | Modelled | `test_google_hold_recovery_resets_the_renewal_date` |
| Google `PAUSED`: no access; `RECOVERED` resumes | Evidence `paused`. Status `PLAN_PAUSED` says when it resumes and never mentions payment | Modelled | `test_google_pause_has_no_access_and_says_when_it_resumes`, M51 |
| Google `PAUSE_SCHEDULE_CHANGED`: access until the next renewal | Refused as `NOT_A_STATE_CHANGE`, with that reason | Modelled | `test_events_that_change_nothing_are_refused_with_the_reason` |
| Google `DEFERRED`: access continues, renewal date moves | `verified`, with the new period | Modelled | `test_deferred_and_restarted_keep_access` |
| Google `CANCELED`: access until `expiryTime` | `NOT_A_STATE_CHANGE`. Access ends when the period ends | Modelled | Same test |
| Apple `AUTO_RENEW_DISABLED` | `NOT_A_STATE_CHANGE`. Apple's access-until-expiry wording was **not verified** | Simplified | — |
| Google `RESTARTED` | `verified` | Modelled | `test_deferred_and_restarted_keep_access` |
| Apple `REFUND`/`REVOKE`, Google `REVOKED`: access ends at once | `refunded` writes a revoked claim. This holds during grace too | Modelled | `test_refund_during_grace_ends_access_now` |
| Apple `EXPIRED` (any subtype), Google `EXPIRED` | Evidence `expired`. `PLAN_EXPIRED` says the store reported it; the only action is support | Modelled | `test_store_reported_expiry_is_not_called_a_missing_renewal`, M52 |
| Billing retry, pause or expiry: do not ask the parent to buy | The feature page withholds the offer (`PAYMENT_RETRYING`, `PLAN_PAUSED`; an unconnected purchase in grace: `PURCHASE_NOT_CONNECTED`). Connecting again is refused (`PURCHASE_PAYMENT_RETRYING`, `PURCHASE_PAUSED`, `PURCHASE_EXPIRED`) | Modelled | `test_a_plan_whose_payment_is_retrying_or_paused_is_never_sold_again`, `test_an_unconnected_purchase_in_grace_is_never_sold_again`, `test_a_paused_purchase_cant_be_connected_again`; M53, M55 |

## Delivery, identity and ordering

| Store fact | Prototype | Verdict |
|---|---|---|
| Apple signs notifications (JWS, `x5c` chain to the Apple root) | No signature is verified, and none exists: events are synthetic | **Not modelled** (no real integration is claimed) |
| Google: a notification only signals a change; `subscriptionsv2.get` is the source of truth | No store API is called. Each synthetic event *is* the store's state | **Not modelled** |
| Apple retries delivery (1, 12, 24, 48, 72 h); Google advises `messageId` dedupe | A replay of the same event is a no-op. The same version and status with different content is a conflict | Simplified (dedupe by content, not message id) |
| Ordering is not documented by either store | `provider_version` decides; a late older event changes nothing | Simplified. **Real stores send no such counter.** A real integration would re-read current state (or order by the signed event time) and record that |
| `originalTransactionId` links Apple renewals; each renewal is a new transaction | `external_ref` names the subscription; renewals are new periods of the same ref | Modelled |
| `appAccountToken` is an optional, app-set hint | Ownership is the first household to record the ref (`purchase_refs`) | Simplified |
| Website and App Store purchases are separate purchases | Kept separate. Two on one camera is `DUPLICATE_PURCHASE`, sent to support. Merging is our policy | Modelled (as policy) |
| Google resubscribe after full expiry: new token, no `linkedPurchaseToken` | A resubscribe would be a new ref. No linking | **Not modelled** |
| Upgrades and downgrades via `linkedPurchaseToken` | No linking across tokens | **Not modelled** |

## Known gap in the model

A grace window, once written to the camera, is an append-only claim. The store's later word can end grace sooner: a shorter grace at a newer version, or a billing-retry notice dated inside the window.

**What the prototype does:** it doesn't hide the conflict and doesn't guess. Camera and purchase both show `CONFLICTING_EVIDENCE` (`STORE_ENDED_CAMERA_ACTIVE`) with support as the action. The claim still grants its features until its end date.

**Why not more:** closing a claim early needs a new claim state in the entitlement derivation. That design is in `docs/FUTURE_DESIGNS.md` §5 and is not built.

Tests: `test_a_later_store_word_inside_a_written_grace_window_is_not_hidden`; mutant M54.

## PostgreSQL

| Fact | Prototype |
|---|---|
| A foreign-key check takes `FOR KEY SHARE` on the parent row; `FOR UPDATE` on the parent blocks it, `FOR NO KEY UPDATE` does not | The household lock is `SELECT … FOR UPDATE`. The soak writes no table that references `households`, and `FOR UPDATE` vs `FOR NO KEY UPDATE` (isolated worktree only) both showed 0 lock waits. Not changed. See `audits/SOAK_DIAGNOSIS_2026-10-11.md` |
