# Proof Layer: purchase to access

**Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.**

A parent pays for a plan for their baby monitor. Then the plan stops working. Proof Layer is a working prototype of the backend and app behind one question: **"I paid for this. Why can't I use it?"**

For each camera it keeps five facts apart, and says which one is failing:

1. what was paid;
2. which family account it's on;
3. which camera it's connected to;
4. the camera's plan;
5. what the camera can actually do.

It never sells a family something they already paid for. When a store renewal arrives, it reaches the same camera with no new purchase and no new connect. When a plan really has ended, it says so and when, instead of showing a paywall.

Plan names, prices and mount rules are **assumed public rules**, restated in our own words and linked to their public pages. They may be out of date. Every customer symptom in the app is stated as a **hypothesis**, never a diagnosis of anyone's real system.

## What's here

| Path | What it is |
|---|---|
| `proof-layer/` | FastAPI and PostgreSQL backend, vanilla JS app, Alembic migrations, tests, benchmarks |
| `docs/` | Access contract, journey, verification, edge cases, certification, API, domain invariants, runbook, tests, failure ledger |
| `audits/` | Engineering audits: architecture, database, concurrency, performance, recovery |
| `artifacts/` | Test reports (JUnit), mutation results, benchmarks, accessibility report, 1440 px screenshots |

## Run it

See `proof-layer/README.md`. In short:
1. Start PostgreSQL 15 or later. The `btree_gist` extension is needed from migration 0007.
2. Run `python scripts/demo.py`.
3. Open the printed URL.

The app opens on **Home**. **Present** runs a guided walkthrough against the real API.

## How it is tested

| Check | What it covers |
|---|---|
| Backend tests | Domain rules, API, concurrency with real transactions, a controlled clock for subscription periods, migration upgrades on isolated databases, red-team suites, property and model-based tests |
| Browser tests | Playwright, Chromium |
| Mutation testing | 40 planted defects |
| Accessibility | axe-core |

Results are in `artifacts/`, with the commands that produced them.

## Limits

- **Not production software.** A 10-minute soak with four server processes had 17 client timeouts in 30,400 requests. The cause is not established, so it stays a failed release gate.
- **Simulated payments only.** No store or payment provider is contacted.
- **Plan rules may be outdated.** They should be re-checked against their public pages before you rely on them.
