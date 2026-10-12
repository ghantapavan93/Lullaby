# Growth hypothesis: verify access before asking for money

**Status: one hypothesis, with synthetic counts only.** No customer data exists here. The counts show what the instrumentation can measure, not how parents behave.

## The hypothesis

> When a parent asks "why can't I use this?", answering from verified purchase evidence **before** any checkout reduces purchases of something the household already owns, without reducing upgrades it genuinely needs.

It is falsifiable. Each part maps to an event the prototype already stores:

| Claim | Measured by | Falsified if |
|---|---|---|
| Fewer purchases of an owned plan | `purchase_rejected` with `NO_ELIGIBLE_OFFER` or `STALE_DECISION`; `DUPLICATE_PURCHASE` statuses | Duplicate purchases per household don't fall vs. control |
| Real upgrades are not blocked | `purchase_confirmed` for a plan the household didn't own | Upgrade conversion falls by more than the guardrail (below) |
| The parent gets to a working feature | First `USABLE` check after the purchase is connected (`time_to_usable`) | Median time to usable rises |

## Synthetic run (2026-10-11)

`proof-layer/scripts/growth_run.py` drives six journeys through the real API on a test database. It then reads `GET /v1/access/metrics` after each one. The output is in `artifacts/growth/synthetic-funnel.json`.

| Journey | Usable / journeys | Median to usable | Unresolved | Refused before payment | Video runs |
|---|---|---|---|---|---|
| Paid, not connected → connect → verify | 1 / 1 | 0.11 s | 0 | none | 0 |
| Plan ended → offer shown → store renewal → old offer paid | 1 / 1 | 0.10 s | 0 | `STALE_DECISION` 1 | 0 |
| Payment on hold → store recovery → verify | 1 / 1 (recovered 1 / 1) | 0.08 s | 0 | none | 0 |
| Purchase pending | 0 / 0 | — | 0 | none | 0 |
| Paid twice → checkout for a feature the plan has | 1 / 2 | 0.06 s | 1 | `NO_ELIGIBLE_OFFER` 1 | 0 |
| Renewal → recorded evidence → report | 0 / 1 | — | 1 | none | 1 (0.93 s) |

The times are machine speed on synthetic data, not parent time. They only show that the clock starts and stops on the right events.

### What the run itself showed

1. **The old offer was refused after the store renewed.** The parent saw an upgrade offer, then the store renewed the plan, then the parent paid the old offer. Checkout refused it (`STALE_DECISION`) and nothing was charged.
2. **An upgrade offer during "paid twice" is correct, not a defect.** This was checked during the run. The household owns Sleep, and NextNap needs Memories, so the offer is a real upgrade. Paying for `sleep_analysis` (already owned) was refused with `NO_ELIGIBLE_OFFER`.
3. **Two measurement gaps** (not fixed; recorded here):
   - *A pending purchase is invisible to "unresolved".* Only confirmed purchases count. A store purchase stuck in pending shows 0 unresolved, though the parent is waiting.
   - *Using the video feature doesn't count as "usable".* The evidence journey passed the `video_history_7d` gate and analysed a clip. The funnel still shows 0 / 1 usable and 1 unresolved, because "usable" counts access checks only, not gate passes (`evidence_access_checked`).

Before any real experiment, fix both gaps: either one would bias the primary metric.

## What would convince us not to ship this

Any one of these would stop it:

- **Upgrades fall.** Conversion to a plan the household didn't own drops by more than 1 percentage point (absolute) vs. control. The refusal is then blocking real sales, not duplicates.
- **Duplicates don't fall.** `DUPLICATE_PURCHASE` per 1,000 households with a purchase isn't lower than control at the planned sample. The check costs a step and buys nothing.
- **Parents wait longer.** The median time from purchase confirmed to first usable check rises. Verification would then be slowing people down.
- **Support contacts rise.** This isn't instrumented today (see experiment 1). Until it is, this guardrail can't be read, so the experiment can't run.
- **The refusal is wrong.** Any `NO_ELIGIBLE_OFFER` for a feature the household couldn't actually use is a correctness defect, not a growth result. It would stop the rollout whatever the metrics say.

## What is never randomized

Entitlements are never randomized. A household that paid gets every feature of its plan in both arms. Only the order and wording of the explanation may differ. This follows the rule in `docs/ACCESS_EXPERIMENT_DESIGN.md`.

## What real data would be needed

- The base rate of duplicate purchases per household, by channel (store vs. website).
- Upgrade conversion from the feature page today.
- Support contacts tagged "paid but can't use".

None of these is public. Any number here is a placeholder until they exist.
