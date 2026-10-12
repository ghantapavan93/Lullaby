# RT-8: A purchase the app is still confirming is offered again

**Severity:** P2. A paywall is shown for a plan the parent is already paying for. The second charge itself is
blocked by `UPGRADE_IN_PROGRESS`; no double charge was observed.
**Test:** `tests/redteam/test_rt_commerce.py::test_rt_purchase_being_confirmed_is_not_offered_again`

## Steps
1. Seed the demo. `POST /v1/dev/provider/next {"behavior":"pending"}`.
2. `POST /v1/capability-decisions {"feature":"nextnap"}` and buy its offer (`POST /v1/purchase-intents`).
3. Run the worker once. The intent is now `UNKNOWN` (the provider says PENDING).
4. `POST /v1/capability-decisions {"feature":"nextnap"}` again.

## Observed vs expected
- Observed: `status: BLOCKED` with `offer: offer-memories-year-cat-2026-10-public` (the same plan, $120).
- Expected (I23: "A purchase being confirmed is never sold again", enforced as "UNKNOWN +
  PURCHASE_BEING_CONFIRMED, no offer"): `status: UNKNOWN`, `offer: null`, blocker `PURCHASE_BEING_CONFIRMED`.
  That is what happens for a pending *store* claim, but not for the app's own in-flight purchase.
- The same holds while the intent is PREPARED or SUBMITTED. This is inferred from the same code path and was not run.

## Root cause
`app/decisions.py:53`: `evaluate_now` counts only `snapshot["pending_plans"]` (pending entitlement claims) as
"being confirmed". An intent in `PREPARED`, `SUBMITTED` or `UNKNOWN` creates no pending claim
(`purchases.record_outcome` records a claim only on SUCCEEDED), so eligibility cannot see it.

## Smallest fix
In `evaluate_now`, also read the device's live intents (`state in ('PREPARED','SUBMITTED','UNKNOWN')`) and add
their `terms.plan_code` to the pending plans before the `with_pending` re-evaluation.
