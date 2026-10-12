# Experiment design: billing-recovery guidance

**Status: designed, not built, not run.** It is not registered in `app/growth.py`. No result exists.

## Question

When a renewal payment fails, does a short step list shorten the time to recovery? The alternative is today's one-sentence instruction ("Update the payment method in your App Store account").

## What is never randomized

Both arms get exactly the same:

- **Entitlement and access.** The camera's plan, the features that work, the grace end date, and when access stops.
- **The status.** `BILLING_GRACE` or `BILLING_RETRY`, its headline, its dates and its allowed actions.
- **Recovery.** A store recovery is applied the moment it arrives, in both arms.
- **No sale.** Both arms say "there's nothing more to buy", and neither carries an offer.

Only the layout of the payment instruction differs.

A control group is never denied, delayed or shown less access than it paid for.

## Design

| Item | Choice |
|---|---|
| Unit | Household. Assignment is persisted on first use and never recomputed (as `availability_explanation`) |
| Eligible | A household with a purchase on a camera, the first time that camera enters `BILLING_GRACE` or `BILLING_RETRY` |
| Exposure | A render receipt for the access status while in that state. **Not built:** it needs an `access_status` surface in `growth.SURFACES` |
| Arms | `sentence` (control, today's copy) and `steps` (the same facts as three numbered steps) |
| Kill switch | The existing `experiment_controls` row. While it is killed, everyone sees control and no exposure is recorded |

## Metrics

| Kind | Metric | Source today |
|---|---|---|
| Primary | Share of eligible households whose purchase recovers within 16 days of the first failure of its latest failed-renewal episode (ordered by the store's version; a late delivery is excluded from timing, not counted negative) | `access_metrics.funnel` → `billing_recovery` |
| Secondary | Median time from failure to recovery; from recovery to the first usable check | Same |
| Guardrail | Support contacts per eligible household | **Not instrumented:** no support-contact event exists |
| Guardrail | Second purchases for the same camera (`DUPLICATE_PURCHASE`) | Access status |
| Guardrail | Refunds after recovery | Purchase evidence |
| Invariant, not a metric | After recovery, every check finds the plan's features usable | `test_billing_recovery_restores_access_on_the_same_camera` |
| Validity gate | Sample-ratio mismatch on assignment and on exposure (p < 0.001 stops the read) | `growth.sample_ratio` |

## Analysis plan, written before any data

- Compare the primary metric with a two-proportion test at α = 0.05, two-sided.
- Read once, at the planned sample. No peeking, and no sequential method.
- Read nothing if the sample-ratio check fails.

**Sample size is unknown,** because the base recovery rate is unknown. This prototype has no real data. The formula per arm is

`n ≈ 2 · (z₀.₉₇₅ + z₀.₈)² · p(1 − p) / δ²`

As an illustration only, with p = 0.6 and δ = 0.05: about 1,500 households per arm.

## Before it could run

1. Add the `access_status` render surface and its receipt.
2. Instrument support contacts.
3. Write the `steps` copy and review it for accuracy against the store's own help pages.
4. Get a measured base recovery rate from real (not synthetic) data.

---

# Experiment design 2: when the access explanation appears

**Status: designed, not built, not run.**

## Question

Does showing the five-stage access explanation *proactively* change how many paying families reach verified access? The alternative is showing it only after the family opens the camera's plan.

Proactive means on the home screen, the first time a camera's status needs attention.

## Fixed in both arms

- The status, its dates and its allowed actions.
- Entitlement and recovery.
- No offer anywhere.

Only the moment and the place the explanation first appears differ.

## Measured from events the prototype already records

Each is a separate fact; none is inferred from another:

| Event | Source |
|---|---|
| Purchase confirmed by the store | `purchase_evidence` |
| Camera entitlement written (connect or renewal) | `entitlement_claims` / `access_actions` |
| Capability verified | `access_checks`, `feature_access_verified` |
| Feature opened after verification | `access_feature_opened` (client) |
| Recorded evidence: video history verified, evidence opened, analysis completed, report added | `evidence_access_checked`, `evidence_review_opened`, `video_analysis_runs`, `video_annotations` |

- **Primary:** share of families with a paid, unresolved camera that reach a verified usable feature within 7 days.
- **Guardrails:** support contacts, duplicate purchases, refunds, and time spent on the home screen before reaching the camera.

A sample clip being opened says nothing about retention, and is never used as a success metric.

## What real Nanit data would be needed

- Real store and web purchase events with the camera they were meant for, to measure how often purchase and access disagree.
- Support-contact reasons tagged to the entitlement state at the time.
- Feature usage after access, to separate "access restored" from "value received".
- A base rate for each, to size the experiment. None exists in this prototype; every number here is synthetic.
