# API

Base: `http://127.0.0.1:8710` (any port). JSON in and out. Every response carries `X-Trace-Id` (send your own to correlate) and `X-Prototype-Disclosure`.

**Auth (development only):** `Authorization: Bearer pl1.<member_id>.<hmac>`, issued by `POST /v1/dev/sessions`. The server resolves household and role from the database on every request. Errors: `401 UNAUTHENTICATED`, `403 ACCESS_REVOKED`, `403 FORBIDDEN`. Resources outside your household answer `404 NOT_FOUND`, the same as missing ones.

Error body: `{"code": "...", "message": "...", "detail": {...}, "trace_id": "..."}`.

## Briefing

| Method | Path | Body / query | Success | Notable failures |
|---|---|---|---|---|
| GET | `/v1/me` | — | member, household, windows, notifications | |
| GET | `/v1/windows/{id}/brief` | `?revision=N` (optional) | immutable revision: facts, fingerprints, evidence ids, cause | 404 |
| GET | `/v1/windows/{id}/revisions` | — | every revision (X-Ray) | 404 |
| GET | `/v1/windows/{id}/evidence` | — | every event incl. superseded, with `superseded_by` | 404 |
| POST | `/v1/windows/{id}/corrections` | `target_event_id`, `corrected_end` and/or `corrected_start` (ISO 8601 **with offset**), `expected_revision`, `client_event_id`, `reason` | previous and new revision, per-fact before/after, `replayed` | `409 REVISION_CONFLICT`, `409 EVENT_KEY_REUSED`, `409 ALREADY_CORRECTED`, `422 OUTSIDE_WINDOW`, `422 TARGET_SPANS_WINDOWS`, `422 AMBIGUOUS_TIMESTAMP`, `422 INVALID_INTERVAL`, `403` for caregivers |
| POST | `/v1/windows/{id}/acknowledgments` | `revision_id` (the displayed one), `displayed_facts` | `acknowledged_revision`, `current_revision`, `newer_available`, `changed_since`, `message` | `422 INVALID_FACTS`, `409 ACK_FACTS_DIFFER` (same revision already acknowledged for other facts), 404 |
| GET | `/v1/windows/{id}/week` | — | the last 7 nights from each night's current revision: minutes or exclusion reason, edited flag, average with `counted` of `of` | 404 |
| GET | `/v1/windows/{id}/changes` | `?since_revision=N` | diff vs previous (or N), per-member per-fact status | 404 |

## Eligibility and purchases

| Method | Path | Body | Success | Notable failures |
|---|---|---|---|---|
| GET | `/v1/features` | — | features with sources and assumptions | |
| GET | `/v1/household/commercial` | — | each camera with its derived plan (`plan_code`, `state`: active / checking / none, `pending_plans`, `member_cap`, transactions) | `403` for caregivers |
| GET | `/v1/devices/{id}/plan-preview` | — | for every plan: which features it would unlock on this camera, and which are blocked by hardware | `403` for caregivers, 404 |
| POST | `/v1/capability-decisions` | `feature`, optional `device_id` (default: primary camera), optional `surface` (`live_view` / `safety_alert` never carry an offer) | decision: status, blockers, requirements, offer (or null), versions, expiry, experiment assignment | `403` for caregivers |
| GET | `/v1/capability-decisions/{id}` | — | decision + `current` + `stale_reasons` | 404 |
| POST | `/v1/purchase-intents` | header `Idempotency-Key`; `decision_id`, `offer_id`, optional `expected_amount_minor`, `expected_currency` | `201` new intent, `200` with `replayed: true` for a matching retry | `409 STALE_DECISION` (reasons), `409 NO_ELIGIBLE_OFFER`, `409 OFFER_MISMATCH`, `409 TERMS_MISMATCH`, `409 IDEMPOTENCY_KEY_REUSED`, `409 UPGRADE_IN_PROGRESS` (another upgrade is still in flight), `403` without billing permission, 404 |
| GET | `/v1/purchase-intents/{id}` | — | intent, transitions, outbox job, provider ledger record | 404 |
| GET | `/v1/purchase-intents` | — | household's intents | |

Prices always come from the server's decision snapshot. A client-supplied amount or currency is only a check against what the person saw.

## Purchase to access

Contract and status precedence: [ACCESS_RECONCILIATION_CONTRACT.md](ACCESS_RECONCILIATION_CONTRACT.md).

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/v1/access` | — | every camera's status, stages and allowed actions; purchases only for owners and admins. Writes nothing |
| GET | `/v1/access/cameras/{device_id}` | `?feature=` optional | one camera, with `trace` (12 steps), `fingerprint`, `connect_preview`; `offer` is always `null` |
| POST | `/v1/access/check` | `device_id`, `channel`, `external_ref` | one purchase or code against one camera. A POST only to keep references out of URLs; writes nothing. Owner/admin |
| POST | `/v1/access/bind` | `device_id`, `channel`, `external_ref`, `expected_state_revision`; header `Idempotency-Key` | `201` completed, not yet verified; `200` replay; `409 STALE_ACCESS_VIEW`, `ALREADY_ASSIGNED`, `CODE_ALREADY_REDEEMED`, `CAMERA_ALREADY_HAS_PLAN`, `PURCHASE_NOT_CONFIRMED`, `PURCHASE_REFUNDED`, `PURCHASE_UNVERIFIED`, `PURCHASE_EVIDENCE_CONFLICT`, `IDEMPOTENCY_KEY_REUSED`, `PURCHASE_EXPIRED`; `404 PURCHASE_NOT_FOUND`. Owner/admin |
| POST | `/v1/access/verify` | `device_id` | appends an access check (`activation` after a connect, else `recheck`) |
| GET | `/v1/access/cameras/{device_id}/window` | — | verification window: activation check, later checks with verdicts, live verdict, decision |
| GET | `/v1/access/cameras/{device_id}/outcome` | — | operation completed / capability verified / customer outcome observed, each from its own evidence. Owner/admin |

## Growth

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/v1/render-receipts` | `render_id`, `surface`, `subject_id`, optional `experiment_key`, `variant` | idempotent per household `render_id`; `409 RENDER_ID_REUSED` for a different render; `422 VARIANT_MISMATCH`, `422 VARIANT_WITHOUT_EXPERIMENT`; `409 EXPERIMENT_KILLED` when its experiment is switched off |
| POST | `/v1/analytics` | `event_id`, `name`, `properties` | idempotent per household `event_id`; client events only; values must be counts, flags, enums or identifiers (`422 PROPERTY_NOT_ALLOWED`, `PROPERTY_INVALID`, `SERVER_ONLY_EVENT`) |

## Engineering

`GET /v1/xray` — the household's authorization decisions, outbox, analytics, assignments, receipts and access checks. Intent transitions, the provider ledger, purchase events, purchase evidence and connect commands appear only for owners and admins (`commerce_visible`).

`GET /v1/customer-evidence` — public customer reports (paraphrased, dated) mapped to synthetic scenarios and the tests behind each answer, with test states from the latest JUnit report. No household data.

`GET /v1/proof` — the Data & Proof Explorer, including `certification` (invariants, test results from the JUnit report, release gates).

## Development-only (`PROOF_LAYER_DEV=1`, set by `scripts/demo.py`; off by default, 404 otherwise)

All except `accounts` and `sessions` need an owner or admin session. `reset` also works without one when the database has no members (bootstrap).

| Path | Purpose |
|---|---|
| `GET /v1/dev/accounts`, `POST /v1/dev/sessions` | synthetic account switching |
| `POST /v1/dev/reset` | reload the synthetic scenario |
| `POST /v1/dev/mount` | move one camera (`device_id`, default primary): a commercial-state change |
| `POST /v1/dev/experiments/{key}` | kill switch: `killed`, `reason`. While killed, decisions carry `experiment.killed: true` and the control variant, no assignment is written, and exposure is refused |
| `POST /v1/dev/claims` | a synthetic channel event: `channel` (trial, apple, google, web, activation_code), `external_ref`, `event_status` (active, pending, revoked), `plan_code`, optional `starts_at`, `days`. Replays are no-ops; same event with different content → `409 CLAIM_CONFLICT`; a code on a second camera → `409 CODE_ALREADY_REDEEMED` |
| `POST /v1/dev/purchase-evidence` | a synthetic provider event about a purchase not yet on a camera: `channel` (web, apple, google, activation_code), `external_ref`, `plan_code`, `status` (verified, pending, refunded, unverified), `provider_version`, optional `starts_at`, `days`. Replays are no-ops; same event with different content → `409 EVIDENCE_CONFLICT`; another family's purchase → `409 PURCHASE_OWNED_ELSEWHERE`. A refund of a connected purchase writes a revoked claim |
| `GET /v1/dev/access-scenarios`, `POST /v1/dev/access-scenario` | Scenario Studio: list, or reset and build one of seven scenarios through the app's own code paths |
| `POST /v1/dev/provider/next` | schedule the provider's next reply for the caller's household: `succeed`, `decline`, `pending`, `lose_response`, `unavailable` |
| `POST /v1/dev/provider/resolve` | settle a PENDING provider operation of the caller's household |
| `POST /v1/dev/worker` | run `python -m app.worker --once` as a separate process; `crash_after_provider: true` makes it exit between the provider's commit and the app's |

## Operations and site

| Path | Purpose |
|---|---|
| `GET /metrics` | Prometheus text format: request count and latency by route template; outbox jobs by kind and state; oldest due job; intents by state; age of the oldest undecided purchase. Aggregates only. |
| `GET /privacy`, `GET /robots.txt`, `GET /favicon.ico` | Site basics. Unknown pages get a 404 page; unknown `/v1/` paths get JSON. |
