# Proof Layer: purchase to access

**Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.**

Try it: **https://lullaby-rho.vercel.app** (free hosting: the backend can take about a minute to wake, and it serves one private workspace at a time).

## The problem

A parent pays for a baby-monitor plan. Then a feature doesn't work on their camera, and the app asks them to subscribe. "I paid for this. Why can't I use it?"

The same complaint has different causes. The plan was bought but never connected to a camera. A code went to the wrong camera. The camera's mount can't support the feature, so no plan would help. The store's renewal hasn't arrived. The renewal payment failed and the store is retrying. Buying again fixes almost none of these.

Proof Layer is a working backend and app that finds the actual cause for one camera, says what the parent can do, refuses what wouldn't help, and proves each answer.

## What to try

1. Open the link. On Home, press **Same problem. Different cause.** Two situations are built for real on the backend. Each side shows what the parent sees, what the backend found, what it allows, and what happens if the parent tries anyway (run for real, then rolled back).
2. Press **Watch it work, in about a minute**. It drives the real app: an ended plan renewed by the store, a paid plan connected without paying again, an offer shown only where a plan is the one blocker, and an old offer refused after the camera moves.
3. Open **Growth Lab**: the rules that must pass before a release, and five separate steps from purchase to use, each counted from its own records.

## How the backend works

- **FastAPI and PostgreSQL.** Purchase evidence from each store, entitlement claims per camera and access checks are append-only rows. A camera's plan is computed from its claims, not stored in a mutable column.
- **One purchase, one camera** is enforced by a database exclusion constraint, not only by application code.
- **Purchases go through an outbox.** The worker submits to a simulated payment provider with a fixed operation key, so a retry or a second worker never charges twice. If the worker dies after the provider commits, a restart asks the provider's own ledger and finishes the purchase once.
- **Every decision is explained** by the same function the API uses, with a step-by-step trace in the engineering X-Ray.
- **Each visitor gets a private workspace** behind a small gateway (`deploy/gateway`): separate databases, process, signing key and worker, wiped when the workspace ends.

Details: `docs/ARCHITECTURE.md`, `docs/ACCESS_RECONCILIATION_CONTRACT.md`, `docs/DOMAIN.md`.

## What happens when something fails

| Failure | What the system does | Test |
|---|---|---|
| The worker crashes after the provider charged | The purchase stays pending, then completes once from the provider's record | `test_crash_of_a_real_worker_process_is_recovered_by_a_restart` |
| Two workers take the same job | One provider operation | `test_concurrent_workers_submit_once` |
| The provider's answer is unknown | The purchase stays UNKNOWN until the provider decides; nothing is assumed | `test_provider_unknown_stays_unknown_until_the_provider_decides` |
| A store event arrives late or twice | The highest store version wins; a replay changes nothing | `test_late_older_provider_event_changes_nothing` |
| The camera moves to a mount the feature can't use | The old offer is refused before any payment | `test_present_mode_drives_the_real_app_to_a_recovered_purchase` |
| A refund after access was verified | Access is withdrawn on that camera; the history is kept | `test_refund_after_activation_revokes_access_and_keeps_history` |

## How it is tested

PostgreSQL integration tests (domain rules, real concurrent transactions, a controlled clock, migrations, red-team suites, property-based tests), Playwright browser tests in Chromium, mutation testing (66 of 68 planted defects caught), axe-core accessibility checks, and a scripted sweep of the deployed site (`deploy/live_sweep.py`). Results and the commands that produced them are in `artifacts/`. Every defect found along the way is listed with its evidence in `docs/FAILURE_LEDGER.md`.

## Run it locally

Python 3.12 or later, and PostgreSQL 16 with `btree_gist`.

```
cd proof-layer
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # .venv/bin/pip on macOS and Linux
python scripts/devdb.py up        # creates, migrates and seeds local databases (needs initdb and pg_ctl on PATH, or PG_BIN)
python scripts/demo.py            # prints the local URL
```

Tests: `python -m pytest tests --ignore=tests/e2e` for the backend; `pip install playwright && playwright install chromium`, then `python -m pytest tests/e2e` for the browser.

## What it does not do

- **No real stores or payments.** Store events and the payment provider are simulated. Real App Store and Google Play notifications are not handled.
- **Development sign-in.** Sessions are issued for synthetic members; there is no real authentication.
- **Not load-proven.** A 10-minute soak with four server processes on Windows had 17 timeouts in 30,400 requests. The cause is narrowed, not fixed, so it stays a failed gate.
- **Plan rules may be out of date.** Plan names, prices and mount rules are public rules restated in our own words. Customer symptoms are hypotheses, never a diagnosis of anyone's real system.
- **Free hosting.** One workspace at a time, a cold start of about a minute, and a free database that expires.
