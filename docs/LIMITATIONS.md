# Limitations

This is a tested, independent research prototype. It is not production software and is not affiliated with Nanit.

## Not integrated

- No Nanit systems, accounts, cameras, telemetry or models. The night is synthetic.
- No payment provider. The provider is a local simulation with its own PostgreSQL ledger, and no money moves.
- No real authentication. Signed development tokens are issued to anyone on localhost.
- No push notifications. In-app notification rows are written by the worker.

## Not proven

- **Demand.** No parent has used this. Every Growth hypothesis is unvalidated.
- **Scale.** Measured on one laptop up to 100,000 synthetic households and 200 concurrent clients (`audits/PERFORMANCE_AND_SCALE.md`). The 1,000,000-household profile was not run. Nothing was measured across machines or beyond 4 server processes, so no production capacity claim is made.
- **Contention.** The household lock serializes all writes within a household. It was measured with 16 clients on one household (p99 1.4 s, invariants held). It would be a hotspot for an account with many devices and caregivers; finer locks weren't needed or built.
- **Nanit's rules.** The rules are a model of public claims, and Nanit's own pages disagree on several limits (DATA.md). The live entitlement logic is unknown.
- **Accessibility.** Contrast is computed for every token pair. axe-core (WCAG 2.0–2.2 A and AA rules) reports no violations on 16 screen, width and theme combinations (`artifacts/a11y/axe-report.json`). No real screen reader or full manual keyboard audit was run.
- **Browsers.** Visual QA covered Chromium only (Playwright), at 375 to 1920px, in light and dark. Safari and Firefox were not tested.

- **Channels.** Apple, Google, web and activation-code events are synthetic scenario inputs with our own fields. Real store notifications (receipts, signed payloads, grace periods, billing retry) are not modeled.
- **Per-camera model.** The per-camera plan and team-member limits are our reading of Nanit's public pages. The Milestones cap of 50 is an assumption: one Nanit page says unlimited.
- **Benchmarks are noisy.** The same read scenario measured 52 req/s with research agents running and 88.9 req/s after they finished. Treat the figures as relative.

- **Overload behavior.** There is no admission control or load shedding. In a 200-client reconnect storm nothing failed, but p99 reached 17 s. Clients have no backoff policy either.
- **Referrals.** Not built. Nanit's public referral terms are captured in the research; no parent feedback mentions referrals, so they were deferred.
- **Public data.** No row-level Nanit dataset is public. Research data is aggregate only (`audits/PUBLIC_DATA_AVAILABILITY.md`).

## Deliberate scope limits

- Corrections cover the end or start of an existing sleep record within its own window. Cross-window moves, deleting records and adding new sleep from the correction sheet are not supported.
- Conflicting claims from different sources stay unresolved. There is no workflow for resolving them.
- Recomputation is bounded to whole windows, not a fine-grained dependency graph.
- The acknowledgment fact set is fixed at five facts.
- A submitted purchase cannot be cancelled. Undoing a provider charge would need a refund flow, which is not modeled.
- The decision TTL (15 min), the lease (30 s; 3 s in the demo) and the retry backoff are demo values, not tuned.
- Experiment analysis is partial: assignment, exposure, event capture and a sample-ratio check exist (`growth.sample_ratio`). A run-time kill switch exists (migration 0005). Power analysis, outcome metrics and guardrail evaluation don't.
- The Kimi grants feature (time-boxed caregiver write scopes) was folded into membership roles and revocation. Scoped grants are not in this version.

## Environment

- Needs PostgreSQL 16+ locally. `scripts/devdb.py` creates a private cluster. It was verified on Windows 11 with PostgreSQL 17 and Python 3.13.
- Fonts load from Google Fonts, which the privacy page now states. Offline, the UI falls back to system fonts and shows an offline banner.
