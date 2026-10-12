# Growth reliability certification

Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.

The Growth Lab (`#/growth`) shows whether the purchase-to-access invariants hold, from real test results, with the code that enforces each one. Code: `proof-layer/app/proof.py` (`INVARIANTS`, `certification()`), served in `GET /v1/proof` as `certification`.

## How the verdict is computed

1. Each invariant is a hand-written statement with:
   - the code that enforces it (`module:name`, resolved to file and line at run time);
   - the tests that prove it.
2. Test outcomes come from the committed JUnit report `artifacts/test-results/final-backend.xml`. Nothing is computed in the browser.
3. An invariant passes only if every one of its tests has run and passed. Parametrized cases count individually.
4. **"Not run" is never "passed".** This was observed: before the report was regenerated, the lab showed BLOCKED by all 12 invariants.
5. **CERTIFIED** means every blocking invariant passed. Otherwise the verdict is **BLOCKED**, with the list of invariants.
6. Click an invariant to see:
   - its code locations;
   - each test, with its state and number of cases.

## Invariants (all blocking)

| Id | Statement |
|---|---|
| no-resale | A family that already paid is never offered the same capability again |
| hardware-not-billing | A mount problem is never answered with a plan |
| no-transfer | A purchase on one camera is never moved to another automatically |
| bind-once | Concurrent connects of one purchase bind it exactly once |
| no-duplicate | A camera with a paid plan is not given a duplicate of it |
| stale-and-idempotent | A stale connect is refused; a retry returns the first outcome |
| provider-order | The provider's latest version wins; a contradiction is never guessed |
| refund-revokes | A refund removes access through the claim derivation and keeps history |
| tenancy | Another family's purchase or camera reads exactly like a missing one |
| pure-reads | Reading access writes nothing |
| three-outcomes | Operation, capability and customer outcome are never inferred from each other |
| crash-recovery | A worker crash after the provider commits recovers to one provider operation per idempotency key and usable access (at-least-once delivery plus an idempotent provider; not "exactly once") |
| renewal-continuity | Every confirmed period reaches the camera (renewal, extension, late year); an ended plan is said plainly, never as a paywall |

**Current result:** see [RELEASE_PROOF_2026-10-11.md](RELEASE_PROOF_2026-10-11.md). There are 15 blocking invariants; the latest is `video-evidence` (recorded evidence opens only on a plan with video history, re-checked on every media request; missing, dark or stalled video is never stillness; a report never changes a measurement). The certification card also shows the running commit, and warns when the test report is older than the last code change.

## Release gates (shown, not hidden)

**Four-process soak:** FAILED. Read from `artifacts/benchmarks/soak-large-100k-20261010T131314Z.json`:

| Measure | Value |
|---|---|
| Requests | 30,400 |
| Client timeouts | 17 |
| p95 | 496.6 ms |

Narrowed, not fixed (audits/SOAK_DIAGNOSIS_2026-10-11.md): with four processes on this Windows machine, a worker sometimes freezes inside accept() on the shared listening socket, for up to 68.7 s, and the requests on its open connections wait with it. One process never did; lock waits, pool exhaustion and foreign-key locks were measured and ruled out. Why Windows blocks there is unknown, and no run on another OS or behind a proxy has been done. It blocks any claim that the application is production-ready. It does not block the access invariants.

## What certification is not

- **It is not production readiness.** The page says so in the verdict box.
- **It is not a dashboard of outcomes.** No synthetic count is shown as a rate or a prevalence.
- **It does not change rules on its own.** No commercial or security rule is changed by any result. Proposals would need a human reviewer; none are automated here.

## Mutation check

Mutants M25–M35 each remove one invariant's enforcement (`scripts/mutate.py`). A surviving mutant means a test gap or a redundant guard. See `artifacts/mutation/mutation-report.md`.
