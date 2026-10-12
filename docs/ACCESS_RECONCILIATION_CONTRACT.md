# Access reconciliation contract

Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.

This is the contract for one question: **for this camera, what did the family pay for, and what can the camera do?** Code: `proof-layer/app/access.py`, `proof-layer/app/purchase_evidence.py`. Tests: `proof-layer/tests/test_access.py`.

## What it reads (no new engine)

| Fact | Source | Already existed? |
|---|---|---|
| What the store says about a purchase | `purchase_evidence` (append-only, versioned per provider) | New (migration 0006) |
| Which family owns a purchase | `purchase_refs` (one owner per channel + ref) | New |
| Where a purchase is connected | `entitlement_claims` with the same channel + ref | Yes |
| The camera's plan | `claims.camera_plan` → `entitlements.effective_plan` | Yes |
| Purchases in flight for this camera | `decisions.unconfirmed_plans`, purchase intents | Yes |
| What each feature needs | `entitlements.evaluate` (plan × the camera's own mount) | Yes |
| What was verified | `access_checks` (append-only) | New |
| Connect commands | `access_actions` (append-only, idempotent per household + key) | New |

Connecting a purchase writes an ordinary claim through `claims.record`. The camera's plan is still derived in one place.

## Purchase state (pure)

`purchase_evidence.purchase_state(events)`:

1. The highest `provider_version` is the provider's latest word.
2. Older events change nothing, whatever order they arrive in.
3. Two different statuses at the top version → `conflicting`. Nothing guesses between them.
4. A re-delivered event is the same fact (set semantics). The property test found this: a duplicate used to read as a conflict.

## The twelve steps

`access.assess` is deterministic and writes nothing. Each response carries these steps as `trace`.

1. The caller and household are resolved from the members table.
2. The camera is loaded within that household. A foreign id is a 404.
3. Load the household's purchase evidence; group it by purchase.
4. Per purchase, the highest provider version wins. Disagreement at that version is conflicting.
5. Find where each purchase is connected (claims with its channel and ref).
6. Derive the camera's plan from its claims.
7. Collect unconfirmed purchases for this camera (pending claims, in-flight intents).
8. Evaluate every feature against the plan and the camera's mount.
9. Compare the last connect that changed the camera with the last verification check.
10. Choose the status by fixed precedence, or for the purchase or feature asked about.
11. Derive the stages and allowed actions from the status and the member's permissions.
12. Return. Nothing is written except the authorization record every request leaves.

## Status precedence (camera headline)

First match wins. "Relevant" purchases: unconnected ones that would raise this camera's plan, and ones connected to this camera.

| # | Status | When |
|---|---|---|
| 1 | `CONFLICTING_EVIDENCE` | A relevant purchase is conflicting; the store says verified while the camera's claim is revoked; or the store's latest word ends access while a claim from that purchase still grants it (`STORE_ENDED_CAMERA_ACTIVE`). Outranks "connected, checking" (fixed 2026-10-11 after an external review) |
| 2 | `ASSIGNED_PENDING_CONFIRMATION` | A connect changed this camera, the connected purchase is still active, and no check has run since |
| 3 | `PROVIDER_UNKNOWN` | An in-app purchase for this camera has an unknown provider outcome |
| 4 | `PURCHASE_PENDING` | A relevant purchase is pending, or a higher plan is in flight |
| 5 | `BILLING_RETRY` | A higher plan on this camera failed to renew and the store is retrying (Apple billing retry, Google account hold), or its grace end passed with no newer word. No access; the purchase stays on its camera |
| 6 | `PLAN_PAUSED` | A higher plan on this camera is paused in the store. No access; says when it resumes, or that the store hasn't confirmed a resume that was due |
| 7 | `VERIFIED_UNBOUND` | A relevant purchase is confirmed (or in a store grace period) and not on any camera |
| 8 | `REFUNDED_OR_REVOKED` | A higher plan on this camera was revoked, or a relevant purchase was refunded |
| 9 | `BILLING_GRACE` | The plan the camera runs on failed to renew and the store keeps access through its grace end. Features work; the payment needs attention |
| 10 | `PLAN_SCHEDULED` | A higher plan on this camera is paid for and connected, and its period hasn't started |
| 11 | `PLAN_EXPIRED` | A higher plan's period on this camera ended in the last 30 days. Says when; never a paywall. If the store reported the expiry, it says so and offers support only; a trial says "trial" and offers nothing |
| 12 | `PURCHASE_UNVERIFIED` | The store couldn't verify a relevant purchase |
| 13 | `NO_PURCHASE` | No plan on the camera |
| 14 | `ACTIVE_FEATURE_USABLE` | A feature in the plan works with this mount |
| 15 | `ACTIVE_HARDWARE_BLOCKED` | Every feature in the plan is blocked by the mount |

Reached only when a specific purchase or feature is asked about (`POST /v1/access/check`, `?feature=`):

| Status | When |
|---|---|
| `ALREADY_ASSIGNED` | The purchase is on another camera (or another family's camera; never named) |
| `DUPLICATE_PURCHASE` | This camera already has that plan or higher from another payment (a trial doesn't count) |
| `PLAN_EXCLUDES_FEATURE` | The plan doesn't include the feature |
| `PURCHASE_NOT_FOUND` | Not on this account. A missing purchase and another family's purchase read identically. |

## Stages and actions

Stages: Purchase → Account → Camera → Plan → Feature. Each one is `verified`, `action`, `waiting`, `blocked`, `conflicting`, `unknown` ("not yet known") or `not_needed`. A stage after the first unresolved one is "not yet known". We don't claim what we haven't reached.

| Status | Allowed actions |
|---|---|
| `VERIFIED_UNBOUND` | `connect` (owner/admin) or `ask_owner` (caregiver) |
| `ASSIGNED_PENDING_CONFIRMATION`, `ACTIVE_FEATURE_USABLE` | `verify` |
| `ACTIVE_HARDWARE_BLOCKED` | `mount_guide`, `verify` |
| `PURCHASE_PENDING`, `PROVIDER_UNKNOWN`, `PLAN_SCHEDULED`, `BILLING_GRACE`, `PLAN_PAUSED` | `check_again` |
| `PURCHASE_UNVERIFIED`, `PURCHASE_NOT_FOUND`, `PLAN_EXPIRED`, `BILLING_RETRY` | `check_again`, `contact_support` (a store-reported expiry: `contact_support` only; an ended trial: none) |
| `ALREADY_ASSIGNED`, `DUPLICATE_PURCHASE`, `CONFLICTING_EVIDENCE`, `REFUNDED_OR_REVOKED` | `contact_support` |
| `NO_PURCHASE`, `PLAN_EXCLUDES_FEATURE` | none |

No status carries an offer or a buy action. Separately, `decisions.evaluate_now` withholds a capability offer while an unconnected purchase (verified, pending or conflicting) would unlock the feature on that camera. Its blocker is `PURCHASE_NOT_CONNECTED`, resolution `CONNECT`.

## Commands

### `POST /v1/access/bind`

Requires an `Idempotency-Key` header. Body: `device_id`, `channel`, `external_ref`, `expected_state_revision`. Role: owner or admin.

Order: authenticate → household lock → membership recheck → authorize → scoped camera load → idempotency lookup → revision check → purchase checks → `claims.record` → `access_actions` row.

| Refusal | Code |
|---|---|
| Same key, different request | 409 `IDEMPOTENCY_KEY_REUSED` |
| Screen out of date | 409 `STALE_ACCESS_VIEW` (with `current_state_revision`) |
| Not on this account | 404 `PURCHASE_NOT_FOUND` |
| Store not confirmed / refunded / unverified / conflicting | 409 `PURCHASE_NOT_CONFIRMED` / `PURCHASE_REFUNDED` / `PURCHASE_UNVERIFIED` / `PURCHASE_EVIDENCE_CONFLICT` |
| On another camera | 409 `ALREADY_ASSIGNED` (`CODE_ALREADY_REDEEMED` for codes) |
| Camera already has the plan from a payment | 409 `CAMERA_ALREADY_HAS_PLAN` |
| The purchase's period has ended, or the store reports it expired | 409 `PURCHASE_EXPIRED` |
| The store is retrying the renewal payment / the plan is paused | 409 `PURCHASE_PAYMENT_RETRYING` / `PURCHASE_PAUSED` |

Success is `201` with `operation: completed`, `capability: not_yet_verified`. A replay with the same key returns the stored outcome with `200`. Reconnecting a purchase that is already on the camera is recorded with `already_connected: true` and does not reopen verification.

### `POST /v1/access/verify`

Re-reads committed state under the household lock and appends an `access_checks` row. The server picks the kind: the first check after a connect is `activation`; others are `recheck`.

### Read endpoints

`GET /v1/access`, `GET /v1/access/cameras/{id}[?feature=]`, `POST /v1/access/check` (a POST only so purchase references stay out of URLs; it writes nothing), `GET /v1/access/cameras/{id}/window`, `GET /v1/access/cameras/{id}/outcome`.

Caregivers see statuses and stages. Purchase references and the trace need `commercial.read` (owner, admin).

## Renewals (migration 0007)

**Why:** a customer-reported symptom ("renewed, but the app says subscribe"), which is not a confirmed diagnosis. It led us to check our own model.

**The gap.** Our model couldn't represent a renewal. Claims were unique per (channel, ref, status), so a second active period of one purchase had nowhere to go: the camera lapsed while the store said active.

**Now:**
- Claims are unique per (channel, ref, status, `starts_at`, `ends_at`), with migrations 0007 and 0008. An extension (same start, later end) is a new period.
- Every confirmed period of a purchase reaches its camera through `purchase_evidence.apply_periods`, in the same transaction as the provider event or the connect. That covers a renewal, an extension, and an older year that arrives late. No new connect is needed.
- A purchase grants the plan of the period covering now, so a cheaper renewal takes effect. A period that hasn't started is `scheduled`, not ended. Endings older than 30 days are history, not the headline.
- A channel event can't put another household's purchase on this household's camera (`PURCHASE_OWNED_ELSEWHERE`).
- A refunded or revoked purchase is not renewed; it reads as conflicting.
- An unconnected purchase whose period ended is history, not "found". Connecting it is refused (`PURCHASE_EXPIRED`).
- A camera whose higher plan ended without a renewal reads `PLAN_EXPIRED`, with the end date.
- The one-camera rule is now an exclusion constraint, `ex_claim_one_camera`: `EXCLUDE USING gist (channel WITH =, external_ref WITH =, device_id WITH <>)`. Every claim for one purchase names the same camera, in every channel and period. It replaces an index that covered active activation codes only.

## Store billing lifecycle (migration 0009)

Purchase evidence also records `grace`, `billing_retry`, `paused` and `expired`. Store events are translated by `app/provider_lifecycle.py`, a bounded map of documented Apple and Google notifications. Facts: `docs/PROVIDER_LIFECYCLE_FACTS.md`. Gaps: `docs/PROVIDER_LIFECYCLE_GAP_MATRIX.md`.

| Store state | Camera | Why |
|---|---|---|
| `grace` | While grace is the store's latest word, its window is written to the camera as a claim period. Access until the grace end | Apple and Google both say to keep access during grace |
| `billing_retry` | Nothing written. The paid period ends; the purchase stays on its camera | Apple: you can stop service. Google: block access during hold |
| `paused` | Nothing written | Google: no access while paused |
| `expired` | Nothing written | Both: no access |
| `verified` after any of these | The recovered period is written to the same camera. No new connect | Recovery restores access |

A grace window that has ended, with no newer word from the store, reads as billing retry. Only the grace window at the store's latest version is written, and only while grace is current, so a late or older grace event grants nothing. If the store's later word ends access while a written window still runs, the status is `CONFLICTING_EVIDENCE` (`STORE_ENDED_CAMERA_ACTIVE`). The feature page never offers a plan whose payment is retrying or paused.

## Refunds

A refund event for a connected purchase writes a `revoked` claim on that camera through `claims.record`. The plan falls through the normal derivation. The active claim and the refund both stay. A later "verified" event from the store can't reactivate it: the purchase reads as conflicting, and connect is refused.

## What this does not claim

- Nothing here describes Nanit's internal systems. Channels, per-camera plans and code redemption follow public pages (`research/PURCHASE_TO_ACCESS_EVIDENCE.md`). Everything else is our model.
- Store payloads are synthetic. No App Store, Google Play or web store is contacted.
