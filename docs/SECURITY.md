# Security

Scope: a local research prototype. This file states what is enforced, what is a development shortcut, and what is out of scope.

## Enforced

| Control | How | Evidence |
|---|---|---|
| Household isolation in the application | Every lookup is scoped to the caller's household (`security.load_owned`); foreign ids return 404, the same as missing ones | `tests/test_isolation.py` |
| Household isolation in the database | Composite foreign keys `(household_id, id)` on every household-owned reference | `test_database_rejects_cross_household_references` |
| Server-side identity and roles | Signed token → member row; role, household and billing right come from the database on every request | `security.authenticate` |
| Immediate revocation | `revoked_at` checked at authentication and again under the household lock | `test_revoked_caregiver_loses_access_with_the_same_token` |
| Least privilege | Caregivers read and acknowledge; only owner/admin correct, check plans or buy; buying also needs `can_bill` | `test_caregiver_cannot_correct`, `test_member_without_billing_permission_cannot_buy` |
| Tamper-evident history | Append-only triggers on evidence, revisions, acknowledgments, intent transitions | append-only tests |
| No client-defined prices | Terms come from the server snapshot; a client amount is only compared | `test_client_price_manipulation_is_rejected` |
| Analytics privacy | Typed properties (counts, flags, enums, identifiers); nested objects and prose rejected; server events can't be posted by clients | `test_analytics_are_idempotent_and_allow_listed`, critic F-05/F-10 |
| Purchase data least privilege | Caregivers get no intents, transitions, provider ledger or plan details, in the API or the X-Ray | critic F-13 |
| Audit | Every authorization decision, allowed or denied, written in its own transaction | `authorization_log`, X-Ray |
| Output encoding | The UI escapes every server string before inserting HTML (`esc()`) | code review |

## Development-only shortcuts (not production)

- **Authentication.** Tokens are HMAC-signed with `PROOF_LAYER_SECRET`, or with a random per-process key when it is unset, so tokens can't be forged from the source and expire on restart. When dev mode is on, `/v1/dev/sessions` issues them to anyone. A real system would use its identity provider.
- **Scenario routes** (`/v1/dev/*`). Off by default; `scripts/demo.py` sets `PROOF_LAYER_DEV=1` for local demos. Mount, provider, reset and worker controls need an owner or admin session. `/v1/dev/reset` and `/v1/dev/worker` still act on the whole database.
- **Database.** Trust authentication, bound to 127.0.0.1 only.
- **No TLS, rate limiting or CSRF protection.** The app is meant to run on localhost. Bearer tokens are not cookies, so classic CSRF does not apply. The other two are absent.

## Out of scope

Real payment data, real child data, PII handling beyond synthetic display names, data retention and deletion workflows, and penetration testing beyond the adversarial test suite and the independent critic review (`artifacts/critic-review.md`).
