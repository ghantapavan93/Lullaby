# Release proof (2026-10-11)

**Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.**

> **Superseded by [RELEASE_PROOF_2026-10-12.md](RELEASE_PROOF_2026-10-12.md).** Kept as history. Its verdict, and its line linking the advisory lock to round 12's flaky test, are corrected there.

## Verdict

**Ready for a controlled review. Not ready for a public link.**

A controlled review means one reviewer at a time, on the author's machine or a private instance, with the author present or reachable. A public link is blocked by one missing design: visitor isolation (below). Nothing was pushed or deployed.

## Since the first version of this proof (Oct 11, evening)

- **A deadlock fixed.** Two households redeeming one code at the same moment could deadlock inside the one-camera exclusion check and return a 500. A per-purchase transaction lock fixed it. The race test failed 1 of 25 runs before the fix, and 0 of 25 after. This was the flaky failure of round 12.
- **Playback made honest.** It now stops exactly on the interval's end, renews the media link, says when it fails, and keeps the selected interval after navigation.
- **Stage labels.** A stage reads "Verified" only when a check backs it.
- **Present captions** state only their own writes.
- **Isolation built for a public demo.** A review gateway gives each browser its own isolated workspace from a pool. Each workspace has its own databases, process, signing key, worker and media folder, and it expires and is reseeded.
- **Not deployed.** Oracle's free A1 capacity is unavailable in the home region, so the verdict below is unchanged. See `docs/DEPLOYMENT_CHECKPOINT.md`.

## What was run, on what code

| Check | Result | When (UTC) | Where |
|---|---|---|---|
| Backend tests (PostgreSQL) | **597 passed**, 0 failed, 0 skipped. The local CC0 clip was present, so its test ran | 01:45Z (Oct 12), on `f457666` | `artifacts/test-results/final-backend.xml` |
| Browser tests (Chromium) | **45 passed**, including playback, reviewer isolation and the review gateway | 01:51Z, same code | `artifacts/test-results/final-e2e.xml` |
| Certification | **CERTIFIED**: 15 of 15 blocking invariants. The report names code digest `21e0453fea1a`, the same as the running code | from the backend report above | Growth Lab; `proof-layer/app/proof.py` |
| Mutation testing | **66 of 68 killed** on `fa07106` (before the M63 test and the reset fix below). Survivors, listed below | 16:00Z | `artifacts/mutation/` |
| Accessibility (axe-core, WCAG 2.2 A/AA rules) | 0 violations in 68 scans: 17 screens, light and dark, 390 and 1440 px, including Scenario Studio's comparison and recorded evidence | 01:56Z (Oct 12), on `f457666` | `artifacts/a11y/axe-report.json` |
| Visual QA | 168 captures (24 scenes × 7 viewports): 0 overflow, 0 small targets, 0 console errors. 12 expected 404s are counted apart: the page detecting a reset made by the QA script | 02:03Z (Oct 12), on `f457666` | `artifacts/visual-qa/access/` |
| Public copy | Rebuilt from committed files at `937af9f` (local only, no remote). The forbidden-content scan found 0 hits after raw soak dumps, run scripts and plain test logs were excluded. Its own suites: **585 passed, 2 skipped** (private notes and the CC0 clip are absent by design); browser **31 passed**. On that fresh tree, certification matched the report's code digest and said CERTIFIED | 17:15Z | `release/build_public.py`, `../proof-layer-public` |
| Real clip | The local CC0 clip decoded and measured. With `motion-baseline-2`, the intervals and every window's measurement matched version 1 | 2026-10-11 | `artifacts/video-evidence/cc0-sample-analysis.json` (numbers only) |
| Four-process soak | **FAILED**: 17 of 30,400 requests got no answer within 60 s. Narrowed to one worker freezing in `accept()` on a shared Windows socket; not fixed | 2026-10-11 | `audits/SOAK_DIAGNOSIS_2026-10-11.md` |
| Cold reviews | Two reviewer agents with no builder context. The first, on the branch: 10 defects, all repaired (ledger round 10). The second, on the merged code: the core claims held (10 targeted tests passed); 7 paperwork and provenance defects, all repaired (round 11) | 2026-10-11 | `docs/FAILURE_LEDGER.md` |

**Mutation survivors and doubtful kills, stated plainly:**
- **M45** (a demo reset takes every lock up front). It was killed in the previous run by a race test and survived this one. With the reset's 500 ms lock timeout, the mutated reset also gives up and retries, so the mutant is close to equivalent. Not counted as proven.
- **M63** (a window with only part of its frames is missing evidence). It survived: no test covered a thin window. `test_a_window_missing_half_its_frames_is_missing_evidence` was added after the run (`9752b67`). With the mutant applied by hand, that test fails; it was not re-run through `mutate.py`.
- **M65** (camera motion). It was recorded as killed, but by a concurrency test that failed under the run's load: a flake, not the guard. With the mutant applied by hand, two video tests fail, so the guard is covered.
- **M16** survived the run before the merge; its property test now compares each purchase's own plan, and it was killed in this run.

**A flaky test, found by the public copy and partly fixed:**
- `test_a_scenario_reset_never_fails_while_a_page_keeps_polling` failed in the public copy's run, then 1 of 5 times when run after `test_camera_plans.py`. All 60 reset attempts timed out on their locks.
- Cause: every request reads `members` first, but the reset locked `members` last. A request holding `members` could wait on a table the reset already held, so under steady polling every retry could lose.
- Fix: the reset now locks `members` and `households` first (`app/seed.py`, `472475f`). The pair then passed 34 of 35 runs.
- The one later failure was a different error: a row-level deadlock between two app transactions, which didn't recur in the next 25 runs. **Not diagnosed.** A reviewer may see this test fail rarely.

**Not supported:** widths under 320 px. At 280 px the header controls overflow (second cold review).

**Not run:** Safari and Firefox; the 1,000,000-household profile; referral faults; any test on real store payloads, real devices or real customer data.

Each test report records a digest of the code it ran on. Certification compares that digest with the running code: it says CERTIFIED only when they match, and UNVERIFIED otherwise. This works on a fresh clone, where file times mean nothing (second cold review).

## Verdict by category

| Category | Verdict | Why |
|---|---|---|
| Entitlement correctness | **Strong** | Certification's blocking invariants (no resale, no transfer, bind once, provider order, refunds, renewals, billing lifecycle) map to named tests; the mutation row above says which mutants the suite kills |
| Recorded evidence | **Good, narrow** | Real decoding; deterministic; plan-gated on every media request; missing, dark or stalled video is never stillness; reports never change measurements. Narrow: one handheld clip, and pixel change can't separate the baby from the camera |
| Security and tenancy | **Good for a prototype** | Every `/v1` route is in the red-team sweeps (revoked member, foreign family). Media links are HttpOnly, path-scoped and re-checked. Authentication is development-only by design |
| Recovery | **Good** | Worker crashes after the provider and mid-analysis recover (real OS process exits) |
| Accessibility and layout | **Good, automated only** | 0 axe violations and 0 layout faults as measured. A screen-reader pass and keyboard-only flow by a person were not done |
| Growth measurement | **Honest, unproven** | One falsifiable hypothesis with synthetic counts and stop conditions (`docs/GROWTH_HYPOTHESIS.md`). Two measurement gaps found and not yet fixed; no real data |
| Operations under load | **Weak** | The four-process soak fails, with the cause narrowed but not fixed |
| Visitor isolation | **Weakest: missing** | One shared synthetic household. A visitor's reset replaces another visitor's state, and any visitor can open a dev session for any synthetic member |

**Weakest category:** visitor isolation. It is the only one that would let one person's actions show up on another person's screen in a public demo.

**The one next step:** give each reviewer session its own synthetic household, created on first visit and expired after an hour. Then make dev controls act only on that household.

## Public reviewer isolation

| Question | Answer, from the code |
|---|---|
| Is a visitor's state separate? | No. `seed.load_fresh()` resets the one demo database (`/v1/dev/reset`, `main.py`) |
| Can a visitor act as anyone? | Yes, inside the demo: `/v1/dev/sessions` issues a token for any synthetic member (dev mode only) |
| Can real data enter? | Uploads are development-only and need the owner or admin role. There is no public upload, by design |
| Is the real infant clip exposed? | No. It is git-ignored, off by default (`PROOF_LAYER_SAMPLE=cc0` opts in), and never in the public copy |

So the release is a controlled review only. Visitors can't see real data, because none exists, but they can see and undo each other's synthetic actions.

## First 15 seconds

The cold reviewer wrote this before reading any builder document:

- **What it is:** "a research repo plus a FastAPI/PostgreSQL prototype … reconciles 'I paid but can't use it' per camera".
- **Who it's for:** reviewers of a Senior Backend Engineer, Growth application.
- **What it proves:** answers change correctly when facts change, without losing history.
- **First screen:** "Your cameras", with the disclosure visible.

That matches the intent. The only gap was the proof-layer README, which led with the night briefing; it now leads with purchase to access.

## Cold start

Followed as a stranger would, from the README:

| Step | Before the review | Now |
|---|---|---|
| Install and start | Worked | Unchanged |
| Find the video module | Not mentioned in either README | Both READMEs describe it; the proof-layer README says how to reach it and lists the switches |
| Run a second checkout | `demo.py <port> <suffix>` existed; nothing created the databases | `devdb.py up <suffix>` creates, migrates and seeds them (run on a fresh suffix, then dropped) |
| Open tab after a server restart | Failed silently with 401 | Renews the synthetic session once and repeats the request (seen in the browser) |

A second cold review ran after these fixes, on the merged code. It confirmed the core claims (10 targeted tests passed; the media gate held by hand). It found stale counts in four documents, this file's unfilled rows, an axe report older than the last frontend change, and a staleness check based on file times. All of these are corrected here; the reruns are the rows above.

## Demos

**60 seconds.** Open `/?present=recruiter`. It drives the real app: a confirmed purchase that isn't on a camera is connected without paying again, then access is verified as a separate step.

**3–5 minutes.**
1. *(0:00)* Home: one purchase, two cameras. "It can go on one camera." Connect it to the Nursery camera, then watch access verify.
2. *(0:45)* Scenario Studio → **Same symptom, two causes**: billing retry against incompatible hardware. Same parent symptom, different backend decisions and refusals, run against the real backend and rolled back.
3. *(1:45)* Scenario Studio → **Renewal, then recorded evidence**. The clip is refused: the plan ended. The store renews it; the worker analyses the clip in its own process. Select an interval and see what was measured and what isn't known. Report "The baby was still" on a movement interval: revision 2, and the measurement is unchanged.
4. *(3:15)* Growth Lab: the funnel counts, labelled synthetic, and certification with the running commit.
5. *(4:00)* Failure honesty: the four-process soak, still shown as failed.

## Who decides

Pushing or deploying needs the owner's explicit word. This document authorises neither.
