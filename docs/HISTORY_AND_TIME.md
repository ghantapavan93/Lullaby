# What we believed then, and what we know now

A design note. It describes what the current tables can and can't reconstruct. No temporal rewrite is proposed before the interview.

## The times the system already keeps

| Time | Where | Meaning |
|---|---|---|
| Effective period | `purchase_evidence.starts_at/ends_at`, `entitlement_claims.starts_at/ends_at` | When the store says access applies |
| Store order | `purchase_evidence.provider_version` | The store's sequence for one purchase. Synthetic here; real stores send none (see the gap matrix) |
| Receipt time | `purchase_evidence.received_at`, `entitlement_claims.received_at` | When this system recorded the fact (database clock) |
| Decision time | `access_checks.created_at` (`clock_timestamp()`) | When a capability was verified, with the status, plan, usable features and a fingerprint of the inputs |
| Display time | `acknowledgments` (night briefing), `video_acknowledgments` | A member acted on a specific immutable revision |
| Policy version | `access_checks` via `catalog_version` / `policy_version` on decisions; `video_analysis_runs.processor_version` + `config_digest` | Which rules produced the answer |

## What can be reconstructed today

| Question | Possible? | How |
|---|---|---|
| What did we tell this camera's owner at the last check? | Yes | The latest `access_checks` row: status, plan, features, fingerprint |
| Did later evidence change that answer? | Yes | Each later check is compared with the activation check (`/v1/access/cameras/{id}/window`: still verified, changed, access lost) |
| What did the store say, in order, about one purchase? | Yes | All `purchase_evidence` rows, by version and receipt time |
| A refund arrived late with an earlier effective date: what did we believe before it? | Partly | Checks before the refund's `received_at` show what was verified then. The status at an arbitrary past instant isn't stored; it could be recomputed from the facts received before it, but no replay function exists |
| What did a caregiver's screen show? | For acknowledged revisions, yes | Acknowledgments bind to an immutable revision and its fact fingerprints. Unacknowledged screens aren't recorded |
| A video interpretation changed after a parent's report: what was measured, what was said? | Yes | Measurements are never edited. Reports are new revisions with author and time; acknowledgments keep the revision they saw |

## What it does not claim

- No full bitemporal model: there is no "as known at time T" query across all tables.
- Recomputing a past status from received facts would use today's code and policy unless the policy version is pinned. The policy version is recorded but not replayable.
- What the client actually displayed is known only where an acknowledgment or a render receipt was sent.

## If it were built

Add a pure `assess_as_of(household, device, effective_at, known_at)`:
- filter evidence and claims to `received_at <= known_at`;
- evaluate at `effective_at` with the policy version recorded at `known_at`.

The existing property test (order and duplicates don't change state) would extend to: "the answer as known at T never depends on facts received after T".
