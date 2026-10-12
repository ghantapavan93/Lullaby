# Decisions

Each entry: decision · why · trade-off. The newest are at the bottom.

1. **Repair Kimi v2, don't restart.** v2 is newer than the package reviewed earlier: it came from Downloads as the v2 archive (kept locally). Its module boundaries, story and visual direction were sound; its guarantees were not (12 of 14 reproductions failed). Kept the module names, rewrote their internals. · *Trade-off:* little v2 code survives verbatim. The lineage is in git and `research/intake/`.
2. **PostgreSQL only, no SQLite fallback.** The guarantees are row locks, `ON CONFLICT`, `SKIP LOCKED`, triggers and composite FKs. `app/db.py` refuses non-PostgreSQL URLs. · *Trade-off:* setup needs Postgres, which `scripts/devdb.py` makes one command.
3. **Household row lock as the single serialization point** (build spec §9). Lock order is household, then window or intent. · *Trade-off:* writes within one household serialize. Fine for a family; revisit only with measured contention.
4. **Composite tenant foreign keys** on every household-owned reference, plus household-scoped loading. · *Trade-off:* wider keys, but cross-tenant references are impossible even through raw SQL.
5. **Append-only by trigger** for evidence, revisions, acknowledgments and intent transitions. Demo reset uses `TRUNCATE`, which row triggers don't see. · *Trade-off:* no "fix the row" shortcut. Corrections must be new rows, which is the point.
6. **Coverage is observation availability only.** It comes from explicit `camera_availability` reports. No report means unknown, not 0% or 100%. · *Trade-off:* the seed must model camera availability explicitly (four syncs, so the night reaches revision 4).
7. **Window 19:00–07:00 in the household's named timezone, measured in UTC.** This matches the spec's 720 minutes. Kimi's 18:00–12:00 window inflated coverage to 96.3%. · Found and fixed: Python's same-tzinfo subtraction ignores DST, so all interval math normalizes to UTC.
8. **A correction is a selection, not a conflict.** An explicit correction supersedes one record and keeps PARENT provenance. Independent reported sleep where the camera saw no sleep is *contested*, and contested time never enters an exact total.
9. **Fingerprints over value + provenance + uncertainty.** The same 06:50 from a different source is a change. · *Trade-off:* more "needs review" events. Honest by design.
10. **Acknowledgment requires `revision_id` and `displayed_facts`.** There is no default to latest. The per-fact standing acknowledgment is the highest revision, and `ack_heads` enforces monotonicity in SQL.
11. **The Sleep Score is removed from the central view.** The spec and the review both called it an arbitrary formula. Nanit ships a real Sleep Score, and we don't imitate it. · This overrides Kimi's addendum, which kept it.
12. **Offers are verified by re-running the rules with the offered plan.** The cheapest purchasable plan that makes the feature AVAILABLE wins, and no offer is made if any blocker is hardware or unknown.
13. **Demo feature: Developmental Milestones.** Nanit documents both its plan requirement (Milestones) and its mount requirement (Floor Stand or Wall Mount). This replaces "milestone clips", which had no local source, and NextNap, whose mount rule is partly inferred.
14. **Public list prices, labeled simulated.** USD 120 and 300 per year, with their source. The label always says simulated and that no money moves. · *Risk noted by the design review:* real prices could read as affiliation. The disclosure is on every screen and every offer.
15. **The provider is a separate PostgreSQL database**, not a SQLite file (the spec allowed either). Its operations are real committed rows in a different transaction boundary.
16. **PREPARED → SUBMITTED is the submission authorization boundary.** The worker rechecks eligibility under the household lock and commits SUBMITTED before calling the provider. After that, a household change cannot recall the submission. This is documented and tested.
17. **The crash is a real `os._exit(17)`** in the demo and in one test. The in-process `SimulatedCrash` (a BaseException) is used for the faster tests.
18. **Signed development tokens; dev routes behind `PROOF_LAYER_DEV`.** Identity is server-resolved and roles are never client-supplied. · *Trade-off:* not real auth, and labeled as such.
19. **Kimi's scoped caregiver grants are folded into roles and revocation.** They weren't part of either demonstration moment (spec §5: "technical appendix").
20. **Exposure is one per member, surface and subject**, sent by an IntersectionObserver when at least 50% of the surface is visible. A refresh is the same exposure.
21. **Saved Nanit page text is kept local.** The hashes are in the ledger. Reference screenshots are local too, so neither is redistributed.
22. **Vanilla JS, no framework.** One page and no build step. Container queries let the same components work in the two-screen view.
