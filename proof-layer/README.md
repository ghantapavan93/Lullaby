# Proof Layer — "I paid for this. Why can't I use it?"

**Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.**

The primary journey is **purchase to access**, per camera. The prototype shows three things.

1. **Purchase to access.** For each camera, it separates five stages: what was paid, which account it's on, which camera it's connected to, the camera's plan, and what the camera can actually do.
   - A confirmed purchase connects without a new one.
   - Access is verified as a separate step: until a check on the camera matches its current records, the feature stage says "Not checked yet".
   - An upgrade is offered only when paying would actually unlock the feature.
   - Stale, duplicated, foreign or re-priced purchase attempts are refused before the simulated provider is called.
   - A worker that dies after the provider succeeds is recovered from the provider's own ledger.
2. **Recorded evidence.** The plan gates a short clip's "7-day video history". The clip is decoded and measured by a separate worker, and the parent's report is kept apart from the measurement. It makes no sleep, breathing or health claim. See [VIDEO_EVIDENCE](../docs/VIDEO_EVIDENCE.md).
3. **The family evidence lab.** A parent corrects last night's wake time. The original record is kept, and the briefing gets a new revision. A caregiver whose screen still shows the old version is told exactly what changed. Their acknowledgment is never silently applied to the new version.

It began as a repair of an earlier AI-generated prototype; the defects found and their repairs are in `../docs/FAILURE_LEDGER.md`.

## Requirements

Python 3.12+ and PostgreSQL 16+. Tested with Python 3.13 and PostgreSQL 17 on Windows 11.

## Install, start, reset

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/...
.venv/Scripts/python scripts/devdb.py up           # private cluster 127.0.0.1:54329; creates DBs, migrates, seeds
.venv/Scripts/python scripts/demo.py                # http://127.0.0.1:8710, scenario controls on
```

Open http://127.0.0.1:8710.

A second checkout can run beside the first, on its own databases:

```bash
.venv/Scripts/python scripts/devdb.py up video      # proof_layer_video, provider_sim_video and their _test pair
.venv/Scripts/python scripts/demo.py 8720 video     # http://127.0.0.1:8720 on those databases
```

```bash
.venv/Scripts/python scripts/devdb.py reset        # reload the synthetic scenario
.venv/Scripts/python -m app.worker                 # optional: a long-running worker (the demo runs it on demand)
.venv/Scripts/python scripts/devdb.py down         # stop the private cluster
```

To point at your own PostgreSQL instead, set `DATABASE_URL` and `PROVIDER_DATABASE_URL` (both `postgresql+psycopg://…`), then run `alembic upgrade head`, `python -c "from app import provider; provider.create_schema()"` and `python -m app.seed`.

## Test

```bash
.venv/Scripts/python -m pytest tests --ignore=tests/e2e   # domain, API, concurrency, recovery (PostgreSQL)
.venv/Scripts/python -m pytest tests/e2e                  # browser checks (Playwright Chromium)
.venv/Scripts/python scripts/visual_qa.py access          # visual QA: 7 viewports, measured (server running on 8710)
.venv/Scripts/python scripts/a11y_audit.py               # axe accessibility audit (server running on 8710)
```

Tests use `proof_layer_test` and `provider_sim_test` unless `DATABASE_URL` and `PROVIDER_DATABASE_URL` say otherwise. They never use SQLite: the guarantees under test are PostgreSQL behavior.

## Recorded evidence

In the app: **Scenario Studio → "Renewal, then recorded evidence"**. The clip is first refused: the plan ended. Then **Store sends the renewal → Recorded evidence → Run the worker**.

| Setting | Default | Effect |
|---|---|---|
| `PROOF_LAYER_VIDEO` | `1` | `0` removes the module; the access demo is unaffected |
| `PROOF_LAYER_SAMPLE` | `staged` | The generated clip (no person). `cc0` uses the local CC0 infant recording from git-ignored `local-media/`, if present and unaltered; its caption says it is a real baby |
| `PROOF_LAYER_MEDIA_ROOT` | `../local-media/store` | Where stored clips live, outside every served directory |
| `PROOF_LAYER_MAX_UPLOAD_BYTES` | 64 MB | Development upload limit |

## Layout

```
app/
  security.py      identity, authorization, household-scoped loading, household lock
  intervals.py     interval algebra (UTC-normalized)
  briefing.py      facts and fingerprints from evidence (pure)
  engine.py        windows, ingestion, corrections, revisions
  acks.py          revision-bound acknowledgments
  entitlements.py  versioned public-rule policy and simulated catalog
  decisions.py     capability decisions
  purchases.py     intents, submission authorization, outcomes
  provider.py      simulated provider with its own ledger (separate DB)
  worker.py        outbox worker, crash injection, reconciliation
  growth.py        assignment, render receipts, analytics
  access.py        per-camera access status and its five stages
  purchase_evidence.py, claims.py, provider_lifecycle.py   store evidence, entitlement claims, billing lifecycle
  access_metrics.py  the purchase-to-access funnel
  scenarios.py, compare.py   Scenario Studio, and "same symptom, two causes" against the real backend
  video_processing.py  decoding (PyAV) and frame measurement (numpy)
  video_evidence.py    clips, runs, the plan gate, reports, signed media links
  staged_clip.py       the generated test clip
  proof.py         certification and build provenance
  main.py          API; /v1/dev/* scenario controls when PROOF_LAYER_DEV=1
  seed.py          the synthetic scenario
migrations/        Alembic (schema + append-only triggers)
static/            UI (vanilla JS, design tokens)
tests/             PostgreSQL integration, property, concurrency, recovery, e2e
scripts/           devdb.py, demo.py, visual_qa.py, a11y_audit.py, mutate.py, growth_run.py
```

More: [ARCHITECTURE](../docs/ARCHITECTURE.md) · [DOMAIN](../docs/DOMAIN.md) · [API](../docs/API.md) · [DEMO](../docs/DEMO.md) · [RUNBOOK](../docs/RUNBOOK.md) · [TEST_STRATEGY](../docs/TESTS.md) · [LIMITATIONS](../docs/LIMITATIONS.md).
