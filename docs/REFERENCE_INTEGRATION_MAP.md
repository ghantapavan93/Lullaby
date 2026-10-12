# Reference integration map

**What the reference is.** "From Purchase to Access" is a standalone reference concept: a five-screen parent flow, a deterministic reconciliation table, a SQLite event store, and 22 tests.

**What we actually had.** The package itself (`nanit-from-purchase-to-access-prototype.zip`) was **not on this machine**. Only its two preview images were (`Downloads/desktop-preview.png`, `mobile-preview.png`). This map therefore uses:
- the described decision table;
- the screen flow;
- the previews.

No reference code was read or ported.

## Component by component

| Reference component | Existing equivalent | Stronger? | Decision | Test in our suite |
|---|---|---|---|---|
| SQLite event store | PostgreSQL with append-only triggers, tenant FKs, household lock | Existing | **Reject.** New tables go into PostgreSQL (migration 0006) | `test_access.py` (append-only, tenancy) |
| Deterministic reconciliation engine | `effective_plan` (claims), `evaluate` (policy), `evaluate_now` (pending) | Existing covers plan and feature; **missing**: unbound purchase evidence, "bound elsewhere", purchase vs plan vs feature stages | **Extend:** `app/access.py` read model over existing services | status table tests, property test (order invariance) |
| "Paid on nanit.com, code not activated" | none (claims always have a camera) | — | **Build:** `purchase_evidence` + bind command → `claims.record` | `test_paid_not_active_connects_without_a_new_purchase` |
| "Verified App Store receipt, no camera" | none | — | same | same family of tests |
| "Paid plan, incompatible Flex Stand" | `evaluate` hardware blockers | Existing | **Reuse;** surfaced as `ACTIVE_HARDWARE_BLOCKED` | `test_active_plan_on_wrong_mount_is_not_a_billing_problem` |
| "Google Play outcome pending" | pending claims ("checking") and UNKNOWN intents | Existing | **Reuse + extend** to unbound pending evidence | `test_pending_evidence_waits_and_never_offers_a_purchase` |
| "Code already assigned to another camera" | `CODE_ALREADY_REDEEMED` | Existing | **Reuse;** surfaced as `ALREADY_ASSIGNED`, no automatic transfer | `test_purchase_bound_to_another_camera_is_not_moved` |
| "Purchase refunded" | revoked claims → plan removed | Existing for claims | **Extend:** a refund event on bound evidence writes the revoked claim | `test_refund_after_activation_revokes_access_and_keeps_history` |
| Commands with expected revision + idempotency key | `purchases.prepare` pattern, `household.state_revision` | Existing pattern | **Reuse** the pattern in `access.bind` | concurrency + replay tests |
| Five parent screens | Feature availability page | Reference flow is clearer for the problem | **Adopt the flow,** built on our API and tokens | browser tests |
| "Behind the decision" engineering panel | X-Ray (docked), Data & Proof Explorer | Existing | **Extend** X-Ray with the access trace | `test_proof.py` flow entries |
| Event store "decision trail" | `access_checks` + `intent_transitions` + claims | — | **Build** `access_checks` (append-only) | verification tests |

## Not ported, on purpose

- **The reference's authentication.** Ours resolves the household from the database on every request.
- **Its provider simulation.** Ours has a separate provider database and crash recovery.
- **Its persistence.** There is one system of record.

## What was transferred from ShelfTrace and the ISL Performance Compiler

Both are the author's earlier projects (github.com/ghantapavan93). Their code was read for patterns only. No code, domain or UI was copied.

| Idea | Where it came from | Where it lives here |
|---|---|---|
| "Operation done" and "verified" are different states | ShelfTrace: a delivery can be acknowledged while its receipt mismatches | `access.bind` returns `capability: not_yet_verified`; `access.verify` is a separate command |
| A pure decision function with a safe default | ShelfTrace `decide_action`, `derive_eligibility` | `access.assess` (fixed precedence, read-only, unknown never becomes yes) |
| A stage rail where the first failure dims what follows | ShelfTrace evidence rail | Five stages; later stages read "not yet known" |
| Certification from real artifacts; a failed check blocks | ShelfTrace certification runs | `proof.certification()` reads the JUnit report; "not run" blocks |
| A verification window with per-observation verdicts | ISL Verification Window | `access.window` (still verified / changed / access lost) |
| Outcome review with a mandatory caveat | ISL Outcome Review | `access.outcome_review` (three tiers, synthetic caveat on screen) |
| Scenario builder that runs through the real pipeline | ShelfTrace scenario builder | `app/scenarios.py`, Scenario Studio |

**Not transferred:**
- pricing, scrapers, insurance or coaching domains;
- multi-tenant row-level security, GraphQL;
- Wilson intervals (access checks are binary and few);
- confidence-routed auto-accept;
- any automatic rule change.

## Tests that now exist for the rows above

All are in `proof-layer/tests/test_access.py`:
- `test_paid_not_active_connects_without_a_new_purchase`
- `test_active_plan_on_wrong_mount_is_not_a_billing_problem`
- `test_pending_evidence_waits_and_never_offers_a_purchase`
- `test_purchase_bound_to_another_camera_is_not_moved`
- `test_refund_after_activation_revokes_access_and_keeps_history`

The full list is in `docs/ACCESS_EDGE_CASES.md`.
