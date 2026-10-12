# Visual QA — Nanit Proof Layer

Status: checklist and capture plan (2026-10-10). **No visual QA has been run.** Do not mark items done, or claim screenshots exist, unless they were actually captured from the running app in `proof-layer/`.

## A. How to capture

- Run the app locally; drive it with Playwright (reuse `design/.tools-venv` or the app's own test tooling).
- Viewports: mobile 390x844 (DPR 2, touch) and desktop 1440x900. Light theme unless stated.
- Set scenario state through the developer scenario panel or its API, never by editing the DOM.
- Save to `design/qa/<NN>-<slug>-<390|1440>.png` and record each capture in `design/qa/QA_LOG.md` (date, commit, URL, viewport, theme, reduced-motion, pass/fail per checklist item, notes). Create these when QA actually runs.
- Freeze time-dependent text (offer expiry) via the scenario clock so captures are reproducible.

## B. Checklist (apply to every screen)

1. **Disclosure** visible at top with the exact text "Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit." Not covered by drawers, sheets or toasts.
2. **Identity**: no Nanit logo, wordmark styling, photography, product renders, icons or copied marketing copy. Fonts are Fraunces / DM Sans / JetBrains Mono only (check DevTools → Rendered fonts).
3. **Tokens only**: spot-check 5 elements; computed colors/radii/spacing match `tokens.css` values.
4. **Grayscale test**: apply `filter: grayscale(1)`; every state (sources, unobserved, purchase states, requirement states, selection) is still distinguishable.
5. **Contrast**: any new text/background pair not in `contrast.py` is measured and >= 4.5:1 (3:1 large/UI).
6. **Focus**: Tab through the screen; every stop has a visible 2px ring, nothing hidden behind sticky bars, order is logical.
7. **Targets**: interactive elements >= 44x44 (measure in DevTools).
8. **No horizontal page scroll** at 375, 390, 768, 1024, 1440, 1920 (`document.documentElement.scrollWidth <= innerWidth`).
9. **Numbers**: tabular figures align in tiles, diff rows and tables.
10. **Honest states**: unobserved is neutral (no red/amber); pending/unknown have no green, check or celebration; "Simulated payment. No money moves." present on offer/purchase screens.
11. **Live regions**: the expected announcement fires once (check with a screen reader or the accessibility tree / `aria-live` content).
12. **Reduced motion**: with `prefers-reduced-motion: reduce` emulated, no transforms animate; before→after text still visible.
13. **Dark theme** spot check on screens 1, 3, 8, 11.
14. **Text zoom 200%** spot check on screens 1 and 6.

## C. The 12 screens

| # | Screen | State setup (scenario) | Must show | Viewports |
|---|---|---|---|---|
| 01 | **Initial briefing** | Load initial night, rev 4, viewing as Parent A | Greeting "Last night, clearly."; tiles Recorded sleep 10 h 40 min, Final wake 06:50, Coverage 94.4%, Unobserved 40 min; timeline with hatched 03:40–04:20 gap and legend; source chips; Rev 4 badge; no sleep score | 390, 1440 |
| 02 | **Correction interaction** | Parent A opens Final wake → correction sheet, enters 06:35 (not saved) | Sheet with current value + "Camera observed"; time input 06:35; preview diff rows (Recorded sleep 10 h 40 min → 10 h 25 min; Coverage 94.4% · unchanged; Not observed 40 min · unchanged); note about Parent reported label; Save/Cancel | 390, 1440 |
| 03 | **Revised facts** | Save correction → rev 5 | Tiles show new values with "was …" lines and "Changed" chips; timeline edge at 06:35 with dashed ghost to 06:50; hatch gap unchanged; "Parent reported" chip on final wake; Rev 5 badge; toast "Correction saved as revision 5" | 390, 1440 |
| 04 | **Stale acknowledgment** | Caregiver B had rev 4 open, acknowledges after rev 5 exists | Ack card warning state: "You acknowledged the earlier version." / "Two details have changed since then." + 2 diff rows; "Review revision 5" | 390, 1440 (1440 as split view A/B) |
| 05 | **Hardware-ineligible feature** | Change mount → incompatible; open Feature availability | Blocker card "Blocked by hardware"; checklist mount "Not met", plan state shown; **no offer card**; explanation that a plan would not change this | 390, 1440 |
| 06 | **Eligible upgrade** | Restore compatible mount; fresh decision | "Needs a plan" blocker + offer card "Simulated subscription — $300/year", terms dl, expiry text, "Simulated payment. No money moves.", primary "Start simulated upgrade" | 390, 1440 |
| 07 | **Rejected stale offer** | Display offer, change mount, then submit the displayed offer | Offer card replaced in place by "We didn't submit this offer." panel with reason, diff row "Mount: compatible → not compatible", "Check eligibility again"; no charge implied | 390, 1440 |
| 08 | **Pending purchase** | Eligible offer submitted with provider delay | PENDING pill (clock, solid border), "Pending — waiting for provider", operation line "same operation retried N times"; buttons disabled with reason; no green | 390, 1440 |
| 09 | **Confirmed purchase** | Provider success (incl. after interruption + reconcile) | CONFIRMED pill "Confirmed (simulated)"; feature now "Available"; calm (no confetti); "Resolved from provider records" when reconciled | 390, 1440 |
| 10 | **Failed / unknown result** | Provider failure; separately provider result unknown | Two captures: FAILED pill + error alert "Failed — not charged" + next step; UNKNOWN pill (dashed) + "Outcome unknown — checking" explanation | 390, 1440 (10a, 10b) |
| 11 | **Engineer drawer** | After screen 03 or 09, open Engineering X-Ray | Dark mono drawer: revisions table (rev 4 → 5, cause), evidence table with retained camera evidence and parent correction, ack table showing B acked rev 4 / current 5, purchase operation with idempotency key and state history; "Not shown to parents" chip; disclosure still visible above | 390 (sheet), 1440 (drawer) |
| 12 | **Mobile navigation** | Any state with 2 unacknowledged changes | Bottom tab bar: 4 items, "Changes" badge "2", selected indicator; plus capture with focus on a tab item | 390 only (+ 1440 side nav for comparison) |

Extra captures (recommended): 01 with reduced motion + dark theme; 08 in grayscale for checklist B4; 01 at 375 and 1920 for B8.

## D. Sign-off

QA is complete only when `design/qa/QA_LOG.md` lists all 12 screens at the stated viewports with pass/fail per checklist item and any failures filed. Until then, describe the design as "specified, not visually verified".
