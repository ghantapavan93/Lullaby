# Failure lab

Generated 2026-10-10T13:23:35+00:00 by `python scripts/failure_lab.py` on synthetic data. 12 of 12 injected faults held their invariant.

| Fault | Expected | Observed | Result |
|---|---|---|---|
| Duplicate sleep event (camera re-sends a record under a new key) | minutes and the recorded-sleep fingerprint unchanged; caregivers aren't asked to re-review | rev 4→5, minutes 640.0→640.0, fingerprint same | **held** |
| Out-of-order observation (an earlier camera report arrives last) | observed minutes rise by exactly the 20 newly covered minutes; coverage stays ≤ 100% | observed 680.0→700.0 min (+20.0), 94.4%→97.2% | **held** |
| Missing camera coverage (sleep recorded, no availability report) | coverage is unknown (no percentage), never 100% and never a measured 0% | state=unknown, pct=None | **held** |
| Simultaneous corrections (8 parents' screens submit at once) | exactly one wins (200); seven get 409; exactly one new revision | status codes [200, 409, 409, 409, 409, 409, 409, 409]; revisions 4→5 | **held** |
| Stale caregiver screen (acknowledges an older revision after a correction) | the ack binds to the revision shown, says a newer one exists, and names what changed | acknowledged rev 4, current 5, changed ['final_wake', 'recorded_sleep'] | **held** |
| Expired upgrade (offer bought after its expiry) | 409 STALE_DECISION; no intent; nothing charged | 409 STALE_DECISION; intents 0; provider ops 0 | **held** |
| Hardware change (camera moved to a Flex Stand after the offer was shown) | the shown offer is refused; a fresh check blocks on hardware with no offer | buy 409 STALE_DECISION; new decision BLOCKED ['PLAN_REQUIRED', 'MOUNT_NOT_SUPPORTED'], offer None | **held** |
| Duplicate purchase (10 taps with one idempotency key, at once) | one 201, nine replays (200); one intent; one submit job | codes [200, 200, 200, 200, 200, 200, 200, 200, 200, 201]; intents 1; submit jobs 1 | **held** |
| Worker crash after the provider charged (real process exit) | process exits 17; intent stays SUBMITTED; reconciliation confirms from the ledger; one operation, one attempt | exit 17; after crash SUBMITTED (provider SUCCEEDED); after reconcile CONFIRMED; operations 1, attempts [1] | **held** |
| Provider timeout (charge pending at the provider) | UNKNOWN until the provider decides, no offer meanwhile, then CONFIRMED; one operation | UNKNOWN → after 3 reconciles UNKNOWN; offer while unknown: None; after settle CONFIRMED; operations 1 | **held** |
| Lost response after a committed charge | the worker asks the ledger instead of retrying blind: CONFIRMED with one operation, one attempt | CONFIRMED; operations 1, attempts [1] | **held** |
| Analytics duplication (client resends an event and a render receipt) | each stored once | events 1, render receipts 1 | **held** |
| Invalid referral qualification |  | not run: referrals are not built | not run |

Uncertainty that remains after each fault is in `failure-lab.json` (`uncertainty`).
