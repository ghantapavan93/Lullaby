# Failure recovery

**Question:** can failures be recovered without inventing outcomes?

**Answer:** yes, for the 12 faults injected here. In each, the stored rows matched the rule, not just the HTTP status. Referral faults weren't run, because referrals aren't built.

Run it yourself:
- `python scripts/failure_lab.py` writes `artifacts/failure-lab/failure-lab.json` and `.md`. It runs on its own databases with a freshly seeded synthetic household per fault.
- The results are shown in the app: X-Ray → Data & proof → **Reliability**.

## The lab (2026-10-10)

| Fault injected | How | Expected (the invariant) | Observed | Result |
|---|---|---|---|---|
| Duplicate sleep event | Camera re-sends a record under a new key (engine ingest) | Minutes and recorded-sleep fingerprint unchanged; no re-review | rev 4→5, 640→640 min, fingerprint same | held |
| Out-of-order observation | A 03:40–04:00 camera report arrives after later ones | Observed minutes rise by exactly 20; ≤ 100% | 680→700 min, 94.4%→97.2% | held |
| Missing camera coverage | Sleep recorded on a night with no availability report | Coverage unknown: no %, not 100, not 0 | state=unknown, pct=None | held |
| Simultaneous corrections | 8 threads, same expected revision, barrier start | One 200, seven 409, one new revision | [200, 409×7]; rev 4→5 | held |
| Stale caregiver screen | Caregiver acknowledges rev 4 after rev 5 exists | Ack binds to rev 4, says newer exists, names changes | ack rev 4, current 5, changed final_wake + recorded_sleep | held |
| Expired upgrade | Decision expiry forced into the past, then buy | 409 STALE_DECISION; no intent; nothing charged | 409 STALE_DECISION; 0 intents; 0 provider ops | held |
| Hardware change | Camera moved to Flex Stand after the offer was shown | Old offer refused; new check is a hardware block with no offer | 409; new decision BLOCKED [PLAN_REQUIRED, MOUNT_NOT_SUPPORTED], offer none | held |
| Duplicate purchase | 10 threads, one idempotency key | One 201, nine replays, one intent, one job | [201, 200×9]; 1 intent; 1 job | held |
| Worker crash after charge | Real worker process exits (`os._exit(17)`) after the provider commits | Intent stays SUBMITTED; reconcile confirms from the ledger; 1 op, 1 attempt | exit 17; SUBMITTED (provider SUCCEEDED) → CONFIRMED; 1 op, 1 attempt | held |
| Provider timeout | Provider leaves the charge PENDING | UNKNOWN until the provider decides; no offer meanwhile; then CONFIRMED | UNKNOWN ×3 reconciles; offer none; CONFIRMED; 1 op | held |
| Lost response | Provider commits, response lost | Worker asks the ledger, not a blind retry | CONFIRMED; 1 op, 1 attempt | held |
| Analytics duplication | Event and render receipt sent 3 times | Each stored once | 1 event, 1 receipt | held |
| Invalid referral qualification | — | — | not run: referrals not built | not run |

**Two of these would have failed this morning:**
- The duplicate record changed the fingerprint (RT-7).
- The pending charge got a new offer while UNKNOWN (RT-8).

Both were found by the independent red team and fixed today. The lab now confirms the fixes on fresh data.

## Recovery rules and where they live

| Situation | Rule | Code | Test |
|---|---|---|---|
| Worker dies mid-job | Lease expires; another worker reclaims the job; same operation key | `worker.claim`, `worker.reconcile_intents` | `test_crash_of_a_real_worker_process_is_recovered_by_a_restart` |
| Provider charged, app didn't record it | Reconcile reads the provider ledger by key | `worker.reconcile_intents` | lab + `test_crash_after_provider_success_is_recovered_in_process` |
| Provider never saw the request | Authoritative absence: re-queue the **same** key | `worker.reconcile_intents` | `test_unreachable_provider_retries_with_the_same_key` |
| Provider says PENDING | UNKNOWN; re-ask every 5 s; never upgraded by the app | `purchases.record_outcome` | `test_provider_unknown_stays_unknown_until_the_provider_decides` |
| Eligibility changed before submission | Cancel before the provider is called | `purchases.authorize_submission` | `test_eligibility_change_before_submission_cancels_without_calling_the_provider` |
| Eligibility changed after submission | Can't recall it; the outcome is recorded honestly | same | `test_changes_after_the_authorization_boundary_cannot_recall_the_submission` |
| Two workers race | `SKIP LOCKED` + provider key: one operation | `worker.claim`, `provider.submit` | `test_concurrent_workers_submit_once` |

The model-based test also runs random interleavings of these, comparing against an independent model after every step (`tests/test_model_commerce.py`):
- purchases, store events and camera moves;
- provider behaviors and worker runs;
- crashes and late provider decisions.

## Uncertainty that remains

- **Between a crash and reconciliation**, the app says "submitted", not "confirmed". That is true, and the parent may wait up to one reconcile interval (30 s in the worker loop).
- **While UNKNOWN**, nobody knows if the charge will settle. The app neither grants nor re-sells; only the provider can end it. There is no timeout that turns UNKNOWN into FAILED. That's deliberate, and an operator alert on `proof_layer_undecided_purchase_oldest_seconds` is the backstop.
- **The provider is a simulator.** Real Apple, Google and Stripe failure modes aren't modelled: webhook delays, refunds initiated outside the app, chargebacks.
- **Crash points tested:** after the provider commits, and before submission. A crash inside a database transaction is PostgreSQL's job and isn't injected here.
