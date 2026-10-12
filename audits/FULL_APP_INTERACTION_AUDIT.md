# Full app interaction audit — Proof Layer

Date: 2026-10-10. Auditor: independent frontend and product critic (automated, Playwright + Chromium).
Server: `scripts/demo.py 8711` on `proof_layer_lab` / `provider_sim_lab`. Restarted once (06:16 CDT) because `app/proof.py` and `static/app.js` changed during the audit; proof findings were re-checked on the new code.
Raw evidence: `artifacts/app-audit/` (`inventory.json`, `flows.json`, `ux.json`, `mobile.json`, `present.json`, `axe-report.json`, `proof-snapshot.json`, `findings.json`, `screens/`, `scripts/`).

## Verdict

**A real app.** Every product control calls a real endpoint. Every write lands in PostgreSQL, verified by direct queries. State survives reload where it should. A second browser context sees the other caregiver's changes within one 4 s poll. Stale actions are refused server-side and labelled honestly. No success state appeared for anything that didn't commit, with one exception: the correction sheet closes silently on a second Save (D1).

The weak points are client plumbing, not the backend:
- The 4 s poll rebuilds the chrome and the X-Ray, so keyboard focus and open panels are lost (D2, D3).
- There is no handling for network failure (D6).
- The Data & proof explorer misreports 3 artifact facts (D4, D8, D9).

| Measure | Result |
|---|---|
| Controls audited | 88 (375 px and 1280 px) |
| OK / ISSUE | 64 / 24 |
| Distinct defects | 17 (2 high, 5 medium, 10 low) |
| Horizontal overflow | 0 px on 33 screen captures (375 and 1280) |
| axe-core (WCAG 2.0–2.2 A/AA) | 0 violations on 46 screen/theme/width combinations |
| Unintended 4xx/5xx | `POST /v1/render-receipts` 404 at every Present start (D13); poll 404 loop after another client resets (D10) |
| Page errors | Only offline: unhandled `Failed to fetch` (D6) |
| Broken links | 0 (`/`, `/privacy`, GitHub 200; `/v1/*` unknown → JSON 404; others → 404 page) |
| e2e (`tests/e2e`, refreshed `final-e2e.xml`) | **1 failed, 9 passed** — the failure is a flaky locator (D16); it passed on its own |

## Control-by-control

Class: **W** working backend experience · **S** deterministic synthetic simulation · **F** future concept · **P** static research presentation.
"Other client" means a second browser context (the other caregiver or device), observed via the 4 s poll.

### Global chrome

| Route | Control (n) | Endpoint | State store | Survives reload | Other client sees | Failure handled | Class | Verdict |
|---|---|---|---|---|---|---|---|---|
| all | Skip to briefing (1) | — | — | n/a | n/a | n/a | — | OK |
| all | Viewer switch Parent A / Caregiver B (2) | re-reads `GET …/brief`, `/changes` | memory | No (resets to Parent A) | n/a (view only) | n/a | W | ISSUE D2, D14 |
| all | Present (header) (1) | drives real endpoints | memory | n/a | yes (DB writes) | step failures logged | S | ISSUE D5, D14 (36 px; hidden below 600 px) |
| all | Theme toggle (1) | — | localStorage `pl-theme` | Yes | No (per browser, correct) | try/catch | — | OK |
| all | Side nav: briefing / changes / features (3) | hash route | URL hash | Yes | n/a | n/a | — | ISSUE D2 |
| all | Side nav: Engineering X-Ray (1) | `GET /v1/xray`, `/revisions`, `/evidence` | memory | No | n/a | `/v1/xray` error shown | W | ISSUE D7 |
| ≤1023 px | Tab bar: 3 routes (3) | hash route | URL hash | Yes | n/a | n/a | — | ISSUE D2 |
| ≤1023 px | Tab bar: X-Ray (1) | as above | memory | No | n/a | as above | W | ISSUE D7, D15 |
| all | Footer Privacy, GitHub (2) | static | — | n/a | n/a | n/a | P | OK |

### Night briefing

| Control (n) | Endpoint | State store | Survives reload | Other client sees | Failure handled | Class | Verdict |
|---|---|---|---|---|---|---|---|
| Correct final wake (1) | — (opens sheet) | — | n/a | n/a | n/a | W | OK |
| Wake time input (1) | — | DOM | n/a | n/a | min/max/required | W | OK |
| Save correction (1) | `POST /v1/windows/{id}/corrections` 200, then `POST /v1/dev/worker` | `evidence_events`, `briefing_revisions` (rev 4→5), `outbox`, `notifications` (B, rev 5) | Yes (Rev 5) | Yes: B shows "A newer version exists" ≤4 s | 409 shown inline the first time; **second Save closes the sheet silently** | W | ISSUE D1, D11 |
| Cancel (1) / Escape (1) | — | — | n/a | n/a | focus returns to "Correct final wake" | — | OK |
| Show as list (1) | — | DOM | No (collapses) | n/a | n/a | W | OK |
| Acknowledge revision N (1) | `POST …/acknowledgments` 200 + `POST /v1/analytics` | `acknowledgments`, `ack_heads` (B=4, then 5) | Yes | Yes: Parent A's Changes shows "Acknowledged revision 4 · has not acknowledged revision 5" | Server refusal → toast. **Offline → unhandled rejection, no feedback** | W | ISSUE D6 |
| Show revision N (stale banner) (1) | `GET …/brief`, `/week` | memory (screen revision) | reload opens latest | n/a | n/a | W | OK |
| Review revision N (stale ack card) (1) | same | memory | as above | n/a | n/a | W | OK — tiles show "Changed" + "was" |

### Feature availability

| Control (n) | Endpoint | State store | Survives reload | Other client sees | Failure handled | Class | Verdict |
|---|---|---|---|---|---|---|---|
| Camera picker Nursery / Playroom (2) | `GET /v1/devices/{id}/plan-preview` | memory | No (back to Nursery) | n/a | n/a | W | OK (re-check by design) |
| Feature picker (4) | — | memory | No | n/a | n/a | W | OK |
| Check availability (1) | `POST /v1/capability-decisions` | `capability_decisions` | DB row yes; UI forgets the decision (re-check needed) | n/a | 500 → "We couldn't check availability" | W | OK |
| Start simulated upgrade (1) | `POST /v1/purchase-intents` (Idempotency-Key) → `POST /v1/dev/worker` → `GET /v1/purchase-intents/{id}` | `purchase_intents`, `intent_transitions`, `outbox`; provider `provider_operations` | Yes: panel restored "Confirmed (simulated)"; plan chip "Milestones plan" | Plan visible to any owner session | Stale (camera moved): 409 STALE_DECISION, "We didn't submit this offer… Nothing was charged", 0 intents, 0 provider ops, no success pill | S (payment) on W rails | OK |
| Check eligibility again (1) | `POST /v1/capability-decisions` | as above | — | — | — | W | OK → "Blocked by hardware", no offer |
| Check availability again (post-purchase) (1) | same | — | — | — | — | W | OK → "Available on this account" |
| Plan comparison region (1) | data from plan-preview | — | — | — | — | W | OK |
| Caregiver B view | no request | — | — | — | "Only the account owner can check plans" | W | OK (counted under viewer switch) |

### Engineering X-Ray drawer and Data & proof explorer

| Control (n) | Endpoint | State store | Survives reload | Failure handled | Class | Verdict |
|---|---|---|---|---|---|---|
| Close button (1) | — | memory | — | — | — | ISSUE D7 (focus → body), D14 (35×44 at 375 px) |
| Escape (1) | — | memory | — | — | — | ISSUE D7 |
| Scrim (1) | — | — | — | — | — | OK (600–1023 px only; drawer is full width at 375) |
| Live trace / Data & proof switch (2) | `GET /v1/proof` | memory | No | 500 → "The proof explorer could not load." | W | ISSUE D3, D14 |
| Live trace tables (group, 1) | `/v1/xray`, `/revisions`, `/evidence` | DB reads | — | — | W | OK (revisions, fingerprints, evidence incl. superseded, acks, decision JSON, transitions, ledger, claims, outbox, authz, receipts, analytics) |
| Tab: Flows (1) | `/v1/proof` flows | code via `inspect` | — | missing step shown "(missing in code)" | P (code-checked) | OK — all 36 steps and 27 cited tests resolve |
| Tab: Sources (1) | manifest | artifact | — | "artifact: missing" fallback | P | OK — manifest is script-generated (`import_public_research.py`) |
| Tab: Data availability (1) | manifest | artifact | — | — | P | OK — "row-level public: no" |
| Tab: Domain model (1) | live `pg_catalog` | database | — | — | W | ISSUE D9 (all "rows (est.)" = 0) |
| Tab: Stress (1) | `artifacts/benchmarks/*.json` | artifact | — | — | P (measured, labelled one laptop) | OK — 11 load runs and the 100k dataset row match their files exactly (0 mismatches) |
| Tab: Reliability (1) | recovery traces + failure lab | artifact | — | "Failure lab not run yet." | P | ISSUE D8 |
| Tab: Tests (1) | JUnit + mutation JSON | artifact | — | — | P | ISSUE D4 |
| Tab: Unknowns (1) | hand-written | code | — | — | P | OK (labelled "hand-written") |
| Flow picker (6) | — | memory | — | — | P | OK (referral says "Not built") |
| Evidence `<details>` (group, 1) | — | DOM | collapses every 4 s | — | P | ISSUE D3 |

### Scenario controls (developer panel: side nav ≥1024 px, inside X-Ray below)

| Control (n) | Endpoint | DB effect verified | Other client sees | Failure / precondition handled | Class | Verdict |
|---|---|---|---|---|---|---|
| Summary toggle (1) | — | — | — | — | — | OK |
| present · recruiter / product (2) | real endpoints | rev 5, ack B=4, 409 stale offer, 0 intents | yes | — | S | ISSUE D5 (leaves `devices.mount = flex_stand`) |
| present · engineering (1) | + crash/worker | intent CONFIRMED, provider SUCCEEDED ×1, mount restored | yes | — | S | OK |
| reset demo (1) | `POST /v1/dev/reset` | rev 4, no acks | **other open client 404-loops** | — | S | ISSUE D10 |
| show both screens (1) | — | memory | — | — | S | OK (not persisted; fine) |
| Parent A corrects → 06:35 (1) | corrections + worker | rev 5 | yes | — | S | OK |
| Caregiver B acknowledges (1) | acknowledgments | ack_heads B=4, stale card | yes | — | S | OK |
| past night Oct 8 → 06:15 (1) | `POST …/corrections` (Oct 8 window) | Oct 8 rev 2; week shows "edited" after reload | yes | refused → logged | S | OK |
| camera moved → Flex / back → Floor (2) | `POST /v1/dev/mount` | `devices.mount`, `households.state_revision` | yes | — | S | OK |
| store claims (6) | `POST /v1/dev/claims` | `entitlement_claims`; pending → UNKNOWN, no offer; late confirm → Milestones, team 50; duplicate → "(duplicate: no-op)"; reused code → refused; refund → fallback | yes | refusal logged | S | OK |
| 2 simultaneous requests (1) | 2× `POST /v1/purchase-intents` | log 201 + 200 (replayed); 1 intent | — | no offer → "check availability first" | S | OK |
| interrupt worker after provider (1) | `POST /v1/dev/worker {crash_after_provider}` | SUBMITTED, provider SUCCEEDED | — | guarded | S | OK |
| restart worker (1) | `POST /v1/dev/worker` | PREPARED→SUBMITTED→CONFIRMED (`reconciled_from_provider_ledger`), 1 provider op | — | — | S | OK |
| next provider reply ×3 (3) | `POST /v1/dev/provider/next` | decline → FAILED "not charged" (offer shown again); pending → UNKNOWN; lose_response → CONFIRMED, attempts 1 | — | — | S | OK |
| provider settles → succeeded / failed (2) | `POST /v1/dev/provider/resolve` | stays UNKNOWN until reconcile, then CONFIRMED / FAILED | — | no purchase → "no purchase yet" | S | OK |

### Present bar, static pages

| Control (n) | Behaviour | Verdict |
|---|---|---|
| Pause / Next / Exit (3) | Work. Exit mid "camera moves" step restores `floor_stand` (undo verified in DB) | OK |
| Keys → / Esc / Space (1) | Next / exit / pause | OK |
| Privacy: Back, GitHub (2); 404: Open briefing (1) | 200 | OK |

## Screen classification

| Screen | Class | Why |
|---|---|---|
| Night briefing (incl. week strip) | Working backend experience | Facts, revisions, fingerprints and week totals come from the API; corrections and acks write to the DB; data is synthetic and labelled |
| Two-screen mode | Working backend experience (demo framing) | Two real sessions, one per member token, in one tab |
| What changed | Working backend experience | Diff and per-member ack state from `GET …/changes` |
| Feature availability + plan table | Working backend experience; payment is a deterministic synthetic simulation | Real decisions, intents, worker, separate provider DB; money is simulated and labelled on the offer card |
| X-Ray → Live trace | Working backend experience | Live DB reads for this household |
| X-Ray → Data & proof | Static research presentation (artifact + live catalog + code-resolved) | Every section names its origin; numbers trace to files, the catalog or `inspect` (D4, D8, D9 aside) |
| Scenario controls | Deterministic synthetic simulation | Dev-only `/v1/dev/*`; store events and provider are simulated |
| Present Mode | Deterministic synthetic simulation driving W screens | Captions quote live state; waits for the backend |
| Referral flow | Future interactive concept | Shown only as "Not built" |
| Privacy, 404 | Static | — |

## Data & proof explorer: traceability

| Section | Source | Traced? | Presented as measured but isn't? |
|---|---|---|---|
| Flows | `app/proof.py` FLOWS (hand-written) + `inspect` | Yes: 36/36 steps resolve to file:line; 27/27 cited tests exist | No; labelled "hand-written; code locations resolved at runtime" |
| Sources / availability | `data/public_research/manifest.json` (generated by `import_public_research.py`) | Yes | No |
| Domain model | live catalog (`pg_class.reltuples`) | Yes, but wrong | **Yes: "rows (est.)" = 0 while tables hold rows (D9)** |
| Stress | `artifacts/benchmarks/*.json` | Yes: 0 mismatches for 11 measured runs; dataset row = file | No; "one laptop, relative numbers" stated |
| Reliability | recovery traces, `failure-lab.json` | Yes | **Not-run scenario shown as red "broken" (D8)** |
| Tests | `artifacts/test-results/final-*.xml`, `mutation-results.json` | Yes | **"Caught 0 of 0" hides a RED baseline (D4)**. Note: `final-e2e.xml` now reports 10 tests, 1 failure (this audit's run) |
| Unknowns | hand-written | n/a | No; labelled |

## Layout, accessibility, console, network, links

| Check | Result |
|---|---|
| Overflow | 0 px horizontal overflow on all 33 captures (375 and 1280); tables scroll inside `.table-scroll` / `.xray-scroll` |
| Touch targets ≥44 px | Main product controls pass (44–104 px; tab bar 64 px). Below 44: viewer switch 38, Present 36, X-Ray close 35 wide at 375, Live/Data switch 38, proof tabs 32, dev buttons 40, dev summary 36, footer text links 18 (inline, exempt) — D14 |
| Keyboard order | Logical; skip link first; visible 2 px outline on every focused control in the first 30 Tab stops |
| Escape | Closes the X-Ray, correction sheet and Present. Sheet returns focus; X-Ray does not (D7) |
| Focus stability | Lost every 4 s on nav, tab bar, viewer switch and inside the X-Ray (D2, D3) |
| Drawer containment | None; Tab leaves the drawer, including at 375 px where it is full screen (D7) |
| axe-core 4.10.2 | 0 violations, 46 combinations (light/dark × 375/1280 × briefing, changes, offer, sheet, X-Ray live, 4 proof tabs, two-screen + dev panel, privacy, 404) |
| Console | Only intended 404/409 plus D10 and D13 |
| Error states | Boot 500 → "We couldn't load the prototype" (leaks a JS TypeError, D12). Decision 500 → inline error. Proof 500 → message. Offline → none (D6) |
| Loading | Skeleton + "Loading last night…"; X-Ray "Loading…"; Present shows a pending line until the backend reaches the state |

## Defects (prioritized)

| ID | Sev | Cause (file:line) | Reproduction |
|---|---|---|---|
| D1 | High | `static/app.js:731` (submit listener `{once:true}`) with `<form method="dialog">` at `:718` | Open "Correct final wake" as Parent A. From another device, correct the same night. Press Save → "Not saved… changed since this screen loaded". Press Save again → no request is sent, the sheet closes, and the edit is lost silently |
| D2 | High | `static/app.js:220` → `renderChrome()` rewrites `innerHTML` at `:242`, `:250`, `:251` | Tab to "What changed" in the side nav (or the viewer switch). Wait 5 s → focus is on `<body>` |
| D3 | Med | `static/app.js:221` → `loadXray()` rewrites the drawer at `:834` / `:843` | Open X-Ray → Data & proof → Sources. Expand a source. Wait 5 s → it collapses and focus is lost |
| D4 | Med | `static/app.js:956-960` filters out BASELINE; artifact `artifacts/mutation/mutation-results.json` is BASELINE RED (`test_proof.py::test_every_flow_step_and_cited_test_exists` failed) | X-Ray → Data & proof → Tests → "the suite caught 0 of 0", with no sign that the run failed |
| D5 | Med | `static/app.js:1290` (undo runs only on exit); step at `:1212` | Open `/?present=recruiter&fast=1` and let it finish. Features → Check availability → "Blocked by hardware" (`devices.mount = flex_stand`) |
| D6 | Med | `static/app.js:74-83` (`fetch` not caught); poll swallows errors at `:222` | Go offline (DevTools). Click "Acknowledge revision 4" → nothing happens and the console shows "Failed to fetch". No offline indicator anywhere |
| D7 | Med | `static/app.js:995-1000` (`closeXray` never restores focus); no focus trap | Focus "Engineering X-Ray", press Enter, then Escape → focus on `<body>`. At 375 px, Tab from the drawer reaches "Skip to briefing" and "Parent A" behind it |
| D8 | Low | `static/app.js:950` (`held` null → "broken") | X-Ray → Data & proof → Reliability → "Invalid referral qualification … broken" (artifact says "not run") |
| D9 | Low | `app/proof.py:172` (`GREATEST(reltuples,0)`; reltuples = −1 before ANALYZE) | Reset the demo. Data & proof → Domain model → every "rows (est.)" is 0 (evidence_events actually 27) |
| D10 | Low | `static/app.js:213-222` (poll ignores 404; window id from `loadNight` `:189-194` never re-resolved) | Two browsers. In A: Scenario controls → reset demo. B polls `GET …/brief` and `/changes` → 404 every 4 s with no message |
| D11 | Low | `static/app.js:759-762` (toast, then synchronous `/v1/dev/worker`, then `showLatest`) | Save a correction: the toast "saved as revision 5" shows at 0.2 s; the own screen shows Rev 4 until about 3.8 s |
| D12 | Low | `static/app.js:190-191` (`me.ok` unchecked) | Make `/v1/me` return 500 → "Cannot read properties of undefined (reading 'find')" |
| D13 | Low | `static/app.js:661-675` + reset in Present step 1 (`:1185`) | Open `/?present=recruiter` → `POST /v1/render-receipts` 404 in the console |
| D14 | Low | `static/app.css:97`, `:266` + `:94` (flex shrink), `:289`, `:292`, `:315`, `:374` | See targets above |
| D15 | Low | `static/app.js:243` (`<a href="#" role="button">`) | At 375 px, focus X-Ray in the tab bar and press Space → nothing |
| D16 | Low (test) | `tests/e2e/test_browser.py:161` (text locator also matches the sr-only announcer) | Full e2e run: strict-mode violation. Passes alone (timing) |
| D17 | Low | `static/privacy.html:24-27`; `index.html:10-12` | The privacy page lists what the app never contacts but not Google Fonts, which every load requests |

## 19-item website checklist vs `docs/BUILD_STATUS.md`

| # | Item | BUILD_STATUS | My evidence | Mine | Agree? |
|---|---|---|---|---|---|
| 1 | Custom 404 | DONE | `/no-such-page` → 404 HTML; `/v1/*` → JSON | DONE | Yes |
| 2 | Above-the-fold CTA | DONE | Present works; in-surface CTAs work. Present is hidden below 600 px and is 36 px tall | DONE | Yes (note D5, D14) |
| 3 | Page titles | DONE | index, privacy, 404 have titles | DONE | Yes |
| 4 | Meta descriptions | DONE | index, privacy (404 has none; noindex) | DONE | Yes |
| 5 | OG image | N.A. | noindex local prototype | N.A. | Yes |
| 6 | Favicon | DONE | `/static/favicon.svg` linked; `/favicon.ico` route exists (route not fetched) | DONE | Yes |
| 7 | robots.txt | DONE | `Disallow: /` | DONE | Yes |
| 8 | Sitemap | N.A. | — | N.A. | Yes |
| 9 | Alt text | DONE | axe 0 violations; timeline `role="img"` + table | DONE | Yes |
| 10 | Responsive | DONE | 0 overflow at 375/1280 (1920 not re-checked) | DONE | Yes |
| 11 | Sticky mobile CTA | N.A. | Tab bar is navigation | N.A. | Yes |
| 12 | Honest loading states | DONE | Skeletons and pending captions yes; no offline or lost-night state (D6, D10) | DONE | Partly |
| 13 | Form error states | DONE | First error shows inline; the retry path discards silently (D1) | MISSING (retry) | **No** |
| 14 | Completion state | DONE | Toast only after the 200; own screen lags 3.6 s (D11) | DONE | Yes |
| 15 | Privacy notice | DONE | Accurate except the Google Fonts request (D17) | DONE | Partly |
| 16 | Terms | N.A. | — | N.A. | Yes |
| 17 | Cookie consent | N.A. | No cookie code in `app/` or `static/`; theme in localStorage (verified) | N.A. | Yes |
| 18 | Privacy-respecting analytics | DONE | `POST /v1/analytics` 200 with allow-listed properties; idempotent event ids | DONE | Yes |
| 19 | Contact | DONE | GitHub link 200 | DONE | Yes |

Other BUILD_STATUS disagreements:
- "288 passed": `final-backend.xml` reports 407 tests (0 failures, 1 skipped).
- "Browser tests 10 passed": this run gave 9 passed, 1 failed (flaky, D16).
- "What failed: Nothing open": `mutation-results.json` records a RED baseline and no mutants (D4).
- The a11y claim (0 violations, 16 combinations) is confirmed and extended to 46.

## Not done in this audit

- No real screen reader.
- Browsers other than Chromium not run.
- 768 px and 1920 px not captured.
- `/favicon.ico` not fetched.
- The 100k-household benchmark was not re-run; its numbers are only traced to its artifact.

## Repairs applied after this audit (added by the builder, not the auditor)

All 17 defects were fixed the same day. They are recorded in `docs/FAILURE_LEDGER.md` (round 5).

| Defect | Repair | Evidence |
|---|---|---|
| D1 | The submit handler is kept. A conflict issues a new key and offers "Show the newer revision" | e2e `test_a_rejected_correction_can_be_retried_without_losing_the_sheet` (fails on the previous `app.js`) |
| D2 | `paint()` replaces markup only when it changed | e2e `test_background_refresh_keeps_keyboard_focus` (fails before) |
| D3 | The live X-Ray refreshes only when its data changed; the explorer never auto-refreshes | checked in the browser |
| D4 | The explorer states a failed mutation baseline before any count | checked in the browser |
| D5 | Present's closing step puts the camera back on its Floor Stand | e2e `test_recruiter_present_run_leaves_the_camera_on_its_stand` (fails before) |
| D6 | `api()` turns network errors into `OFFLINE` with a banner | checked in the browser |
| D7 | Focus returns to the opener; Tab is trapped below 1024 px | e2e `test_closing_xray_returns_focus_to_its_opener` (fails before) |
| D8 | Scenarios that weren't run show "not run" | checked in the browser |
| D9 | Exact counts for small tables that haven't been analyzed | `app/proof.py` `schema()` |
| D10 | A reset on another screen shows a notice and stops polling | `app.js` `resetNotice()` |
| D11 | The parent's screen updates before the toast | `submitCorrection()` |
| D12 | Plain load-failure message | `boot().catch` |
| D13 | Render observer cleared on reset | `devAction("dev-reset")` |
| D14 | 44 px minimum targets on coarse pointers and narrow screens | `app.css` |
| D15 | Space activates link-buttons | keydown handler |
| D16 | Re-enable and click in one step | `tests/e2e/test_browser.py` |
| D17 | The privacy page names the Google Fonts request | `static/privacy.html` |

After the repairs: `python -m pytest tests/e2e` → **14 passed** (10 existing plus 4 new).

Not re-checked by the auditor: real screen readers, Safari and Firefox, 768 and 1920 px.
