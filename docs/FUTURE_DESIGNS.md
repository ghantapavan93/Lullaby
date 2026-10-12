# Future designs (documented, not built)

These are designs only. Nothing here exists in the code. Each needs its own fail-first tests before anyone builds it.

## 1. Real store notifications

**Today:** synthetic events enter through a development-only endpoint. `app/provider_lifecycle.py` maps them.

**Design.** A real receiver would:

1. Verify Apple's JWS signature (the `x5c` chain to the Apple root). Google notifications arrive over Pub/Sub, authenticated.
2. Deduplicate on Apple `notificationUUID` and Google `messageId`.
3. Treat the notification as a signal. Re-read current state: Apple Get All Subscription Statuses, Google `subscriptionsv2.get`.
4. Record what the store says now, with its signed date. The synthetic `provider_version` would be replaced by the store's own ordering.
5. Answer 200 only after the record commits. Apple retries at 1, 12, 24, 48 and 72 hours.

**Open question:** neither store documents delivery order. Re-reading state is what makes order irrelevant.

## 2. Serving with more than one process

**Today:** the four-process soak fails. The diagnosis (`audits/SOAK_DIAGNOSIS_2026-10-11.md`) found a worker freezing in `accept()` on the listening socket all four share, on this Windows machine. Locks, the pool and foreign keys were ruled out.

**Design:** one listening socket per process behind a round-robin proxy, then the same 10-minute soak. If the diagnosis is right, it shows 0 timeouts. Also: the repro on Linux.

**Not pursued:** `FOR NO KEY UPDATE` on the household lock. The comparison showed 0 lock waits either way, so it would fix nothing measured.

## 3. Linking purchases across stores

**Today:** a website order and an App Store purchase are separate. Both on one camera is `DUPLICATE_PURCHASE`, sent to support.

**Design:** an app-set `appAccountToken` (Apple) or obfuscated account id (Google) could link a store purchase to the family account at checkout. It is a hint, not proof: Apple doesn't document validating it.

## 4. Camera replacement

**Today:** moving a plan to a replaced camera is support-only.

**Design:** a "replace camera" command. It would revoke the old camera's claim and write the new one in one transaction, keeping one camera per purchase. It needs a support-side confirmation step.

## 5. Early end of grace

**Today:** a grace window, once written, is an append-only claim that runs to the end date the store sent. If the store's later word ends grace sooner, the camera shows `CONFLICTING_EVIDENCE` (`STORE_ENDED_CAMERA_ACTIVE`) and points to support. Nothing is hidden, but the claim still grants its features until its end.

**Design:** a later event dated inside the window would close it with a dated "ended" claim. `effective_plan` would cap the matching period at that date. That needs a new claim status and changes the core derivation. It is deliberately left out of this round: a high-risk change to the core derivation needs its own review.
