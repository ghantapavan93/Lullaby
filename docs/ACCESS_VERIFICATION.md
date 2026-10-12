# Access verification, the verification window and outcome review

Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.

## Three outcomes, three kinds of evidence

| Outcome | Evidence | Written by |
|---|---|---|
| Operation completed | An `access_actions` row whose outcome changed the camera (`already_connected: false`) | `POST /v1/access/bind` |
| Capability verified | An `access_checks` row after that connect with status `ACTIVE_FEATURE_USABLE` | `POST /v1/access/verify` |
| Customer outcome observed | An `access_feature_opened` client event for that camera after the verification | the client (`POST /v1/analytics`) |

`GET /v1/access/cameras/{id}/outcome` reports each one separately. A tier that lacks its own evidence is "not observed", whatever the others say. A click before verification doesn't count. Test: `test_three_outcomes_are_never_inferred_from_one_another`. Mutant M32 makes "verified" fall back to "connected", and the suite catches it.

**Caveat, shown on screen:** in this prototype the parent's click is synthetic. It validates instrumentation, not a real outcome.

## Lifecycle events

| Event | Kind | When |
|---|---|---|
| `access_check_rendered` | client | Once per camera and status actually shown on the access page |
| `activation_action_submitted` | server (kept even if the request fails) | A connect was attempted |
| `camera_entitlement_resolved` | server (only on commit) | The connect wrote the claim |
| `feature_access_verified` | server | A check found a usable feature |
| `access_resolution_unconfirmed` | server | A check found no usable feature |
| `access_feature_opened` | client | The parent opened the feature |

Server events can't be posted by clients (`SERVER_ONLY_EVENT`). Properties are allow-listed identifiers only.

## Verification window

`GET /v1/access/cameras/{id}/window` compares later checks, and the live state, with the activation check.

| Per observation | Meaning |
|---|---|
| `still_verified` | Same fingerprint: plan, plan state, usable and mount-blocked features, mount, connected purchases |
| `changed` | Something changed and nothing usable was lost |
| `access_lost` | A feature usable at activation no longer is |

| Window decision | When |
|---|---|
| `NOT_STARTED` | No check yet |
| `HOLDING` | The live state still matches |
| `CHANGED` | The plan or features changed, nothing lost |
| `ACCESS_LOST` | Lists the features lost |

Window events. The Growth Lab has buttons for device change, refund, reassignment attempt and recheck; each goes through the real API and is followed by a recheck. The other events run in tests only.

| Event | How it is applied | Tested by |
|---|---|---|
| Device change | `POST /v1/dev/mount` | `test_window_holds_until_the_mount_changes` |
| Refund | `POST /v1/dev/purchase-evidence` (refunded, next version) | `test_refund_after_activation_revokes_access_and_keeps_history` |
| Reassignment attempt | `POST /v1/access/bind` to the other camera (refused) | `test_reassignment_attempt_leaves_verified_access_unchanged` |
| Repeated activation | same purchase, same camera | `test_repeated_activation_is_recorded_but_does_not_reopen_verification` |
| Reconnect / recheck | `POST /v1/access/verify` | `test_window_holds_until_the_mount_changes` (two checks, still holding) |
| Delayed provider | a pending purchase arrives later | `test_pending_evidence_waits_and_never_offers_a_purchase` |
| Revoked caregiver | membership revoked | `test_revoked_caregiver_cannot_read_or_verify` |
| Plan update | a store event on the camera's purchase | No dedicated test. It changes the fingerprint like a mount change does. |

The window counts binary checks. It does not compute confidence intervals; there are no repeated outcomes to estimate from.
