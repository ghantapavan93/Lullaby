# Release proof (2026-10-12)

**Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.**

This is the one dated verdict. It replaces `RELEASE_PROOF_2026-10-11.md`, which is kept as history.

## Verdict

**READY TO SHOW AND SHARE, with one stated capacity limit: one reviewer at a time on the free host.**

- **Public sharing.** The link is public. Each visitor gets a private workspace (separate databases, process, signing key, worker and media), and a second visitor can't see or change the first one's data. On the live site, `deploy/live_check.py` opens a second browser while the first holds the workspace: it is told the workspace is in use and sees none of the first browser's data. Locally, `tests/e2e/test_reviewer_isolation.py` and `test_gateway.py` cover the same with several workspaces.
- **The limit.** On the free host there is one workspace at a time. A second visitor waits on a "someone else is using it" page that retries on its own; the first workspace ends 3 minutes after its tab closes (an open tab keeps it alive), or after 45 minutes in all. The backend sleeps after 15 quiet minutes, and the measured wake took 78 s.
- **Before forwarding widely.** The public repository's current tree is clean, but its earlier commits still contain a manifest of excluded private files, an old sentence about how research sources were collected, and one local path. Anyone who browses the history can find them. Replacing the history with one clean commit is the owner's decision (see Who decides).
- **Not claimed.** Production readiness. Authentication is development-only, store events and payments are simulated, and the four-process soak test still fails.

## Live

| Piece | Where |
|---|---|
| Frontend | https://lullaby-rho.vercel.app (Vercel, static) |
| Backend | https://lullaby-backend-v0ki.onrender.com (Render free: gateway, one workspace, its worker) |
| Database | Render free PostgreSQL; expires about 2026-11-10 |
| Public source | https://github.com/ghantapavan93/Lullaby, built from this repository by `release/build_public.py` (forbidden-content scan: 0 hits) |

## What was run, on what code

| Check | Result | Where |
|---|---|---|
| Backend tests (PostgreSQL) | **616 passed**, 0 failed, on `56fb389` (06:05 CDT). The public copy's own run: 616 tests, 0 failed, 2 skipped by design | `artifacts/test-results/final-backend.xml` |
| Browser tests (Chromium) | **62 passed**, 0 failed, on `56fb389`. Includes craft, phone, isolation, gateway and Present tests | `artifacts/test-results/final-e2e.xml` |
| Certification | **CERTIFIED, 15 of 15**, locally and on the live site. Live: running code `aaef42230ebb`, report `aaef42230ebb` (`deploy/live_certification.py`, 05:54 CDT) | Growth Lab |
| Visual QA | 168 captures, 0 console errors, 0 small targets. One 6 px overflow (Customer evidence, 390 px) was found and fixed, with a test that failed before the fix | `artifacts/visual-qa/final/` |
| Accessibility (axe-core) | 0 violations in 68 scans, light and dark, 390 and 1440 px | `artifacts/a11y/axe-report.json` |
| Live sweep (`deploy/live_sweep.py`) | 14 of 15 scenarios at 1440 and at 390 px, with no console errors. The one miss is the Worker-crash card (see Known limits) | `artifacts/deploy/` |
| Recorded walkthrough | 74.1 s, 1440×900, H.264 MP4 (4.9 MB) and VP8 WebM, real footage of the live site, decoded end to end | `artifacts/deploy/demo-recruiter.mp4` |
| Mutation testing | 66 of 68 planted defects caught (run on 2026-10-11; not rerun) | `artifacts/mutation/` |
| Four-process soak | **FAILED**: 17 of 30,400 requests got no answer within 60 s. Narrowed to the socket layer on Windows; not fixed, and no comparable Linux soak has run | `audits/SOAK_DIAGNOSIS_2026-10-11.md` |
| Firefox and WebKit | **Not run**: the browsers aren't installed for Playwright here | — |

## Reviews this release went through

Seven independent reviewers, none given a builder summary (`docs/FAILURE_LEDGER.md`, rounds 14 to 16):

- an end-to-end bug hunt on a local three-workspace stack, plus a scripted sweep of the live site;
- a frontend audit by four reviewers: visual craft, code and headers, product communication, mobile;
- a principal backend review: no P0 found;
- a Growth engineering review;
- a public repository and cloud release review;
- a blind hiring-manager review of the live link, with only the URL and the role description.

## Deadlocks, stated separately

- **Fixed:** two households redeeming one code at the same moment could deadlock inside the one-camera exclusion check. A per-purchase advisory lock fixed it: 1 failure in 25 runs before, 0 in 25 after.
- **Not diagnosed:** a rare row-level deadlock in the demo-reset race test (round 12). It is a different deadlock; the advisory lock is not its fix.

## Known limits

See `docs/LIMITATIONS.md`. The ones a reviewer meets first:

- One workspace at a time on the free host, and a cold start of about a minute.
- A refund of one renewal revokes the whole purchase on that camera. That is a conservative rule that doesn't match per-transaction store refunds.
- The workspace limit per address trusts `X-Forwarded-For`, so it can be bypassed by calling the Render URL directly.
- In browser sweeps of the live free host, the Worker-crash card measured the fresh-seed status in 3 of 4 runs. Built through the API on the same host (05:56 CDT), the purchase stayed pending as designed, and local sweeps with an always-on worker match 15 of 15. Not diagnosed.
- Tests ran on the author's machine; there is no CI.

## Who decides

Rewriting Lullaby's history to remove the retracted texts (the old manifest of excluded files, the old wording about how research sources were collected, and a local path in `87cdb4c`) needs the owner's word, as does deleting a stray Vercel project named `out`.
