# Release proof (2026-10-12)

**Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.**

This is the one dated verdict. It replaces `RELEASE_PROOF_2026-10-11.md`, which is kept as history.

## Verdict

**Ready for a controlled demonstration, and safe to share as a link, with one stated capacity limit.**

- **Public sharing.** The link is public. Each visitor gets a private workspace (separate databases, process, signing key, worker and media), and a second visitor can't see or change the first one's data. On the live site, `deploy/live_check.py` opens a second browser while the first holds the workspace: it is told the workspace is in use and sees none of the first browser's data. Locally, `tests/e2e/test_reviewer_isolation.py` and `test_gateway.py` cover the same with several workspaces.
- **The limit.** On the free host there is one workspace at a time. A second visitor waits on a "someone else is using it" page that retries on its own; the first workspace ends after 10 idle minutes or 45 in all. The backend sleeps after 15 quiet minutes, and the measured wake took 78 s.
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
| Backend tests (PostgreSQL) | FILL | `artifacts/test-results/final-backend.xml` |
| Browser tests (Chromium) | FILL | `artifacts/test-results/final-e2e.xml` |
| Certification | FILL | Growth Lab, locally and on the live site |
| Visual QA | FILL | `artifacts/visual-qa/` |
| Accessibility (axe-core) | FILL | `artifacts/a11y/axe-report.json` |
| Live sweep (`deploy/live_sweep.py`) | FILL | `artifacts/deploy/sweep-1440.json`, `sweep-390.json` |
| Recorded walkthrough | 72.2 s, 1440×900, H.264 MP4 and VP8 WebM, decoded end to end | `artifacts/deploy/demo-recruiter.mp4` |
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
- The Worker-crash scenario once showed the fresh-seed status on the live free host at 390 px. It did not reproduce in 15 local runs with an always-on worker.
- Tests ran on the author's machine; there is no CI.

## Who decides

Rewriting Lullaby's history to remove two retracted texts (an old manifest of excluded files and an old wording about how research sources were collected) needs the owner's word, as does deleting a stray Vercel project named `out`.
