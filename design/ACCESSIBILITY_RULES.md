# Accessibility rules — Nanit Proof Layer (WCAG 2.2 AA)

Status: specification (2026-10-10). These are build requirements, not a compliance claim. No accessibility testing has been run on the prototype yet.

## 1. Contrast (SC 1.4.3, 1.4.11)

- Text >= 4.5:1; large text (>= 24px regular / 18.66px bold) >= 3:1; UI component boundaries, focus rings and meaningful graphics >= 3:1.
- Every token pair in `DESIGN_TOKENS.json → contrastAudit` was computed by `design/tools/contrast.py` and passes in light and dark. Lowest text pairs: muted text on sunken 4.97 (light), unknown text on hatch stripe 4.87 (light). Do not introduce new text/background pairs without adding them to `contrast.py` and rerunning.
- Never place text over the hatch pattern smaller than 14px/500; prefer placing "Not observed" labels beside, not on, the hatch.
- Disabled controls are exempt from contrast but must still show a text reason.

## 2. No color-only meaning (SC 1.4.1)

| Meaning | Non-color cue(s) |
|---|---|
| Camera vs parent-reported sleep | Parent: dotted top edge + "P" marker + legend label + source chip text |
| Not observed | Diagonal hatch + dashed outline + "Not observed" text |
| Awake | Thin 4px rule (shape) vs full block for sleep |
| Purchase states | Icon + state word + border style (solid / 2px solid / dashed) |
| Requirement met / not met / unknown | check / x / dashed-circle icon + "Met" / "Not met" / "Not checked yet" |
| Changed value | "Changed" chip + "was …" line + accent left border |
| Selected nav item | 3px indicator bar + weight 600 + `aria-current="page"` |

QA: grayscale screenshot test (VISUAL_QA §B4).

## 3. Targets and pointer (SC 2.5.8, 2.5.5 advisory)

- All interactive targets >= 44x44 CSS px (tokens `--pl-target-min`), preferred 48px. This exceeds the 24px AA minimum on purpose.
- Small visual elements (chips, copy-ID icons, timeline segments narrower than 44px) get an expanded hit area via padding or a pseudo-element; adjacent hit areas must not overlap.
- No drag-only interactions (SC 2.5.7); timeline corrections use a time input, not dragging.

## 4. Focus (SC 2.4.7, 2.4.11, 2.4.13 advisory)

- Every focusable element: `outline: 2px solid var(--pl-focus-ring); outline-offset: 2px` on `:focus-visible`. Never `outline: none` without an equivalent.
- Focus ring contrast vs adjacent surfaces >= 3:1 (computed 7.07-11.09).
- Sticky elements (disclosure banner, tab bar) must not obscure the focused element (SC 2.4.11): use `scroll-padding-top` = banner + header height and `scroll-padding-bottom` = tab bar height.
- Logical order: skip link → disclosure → header → main → nav (DOM order may place nav after main on mobile; visual order must match reading expectations).
- Modal sheets trap focus and restore it on close. Non-modal X-Ray drawer does not trap.

## 5. Semantics and structure

- Landmarks: `<aside role="note" aria-label="Prototype disclosure">`, `<header>`, `<nav aria-label="Primary">`, `<main id="main">`, `<aside aria-label="Engineering X-Ray">`, dev panel `<section aria-label="Scenario controls (developer)">`.
- One `<h1>` per surface (e.g. "Night briefing"); greeting is a `<p>` styled as display, not an extra h1.
- Stat tiles: `<dl>` (term = label, definition = value + source). Diff rows: `<dl>` or `<table>`.
- Timeline: `role="list"` of segment buttons plus an always-available `<table>` equivalent with `<caption>` "Intervals from 19:00 to 07:00, October 9-10".
- Navigation uses links with `aria-current`, not `role="tab"`.
- Icons `aria-hidden="true"`; icon-only buttons have `aria-label` matching any visible tooltip (SC 2.5.3).
- Durations written for screen readers in full ("10 hours 25 minutes") via visually-hidden text where the visible form is abbreviated ("10 h 25 min").
- Language: `<html lang="en">`. Times are 24h with the scenario timezone stated once ("Times in America/New_York").

## 6. Live regions for state changes (SC 4.1.3)

| Event | Region | Politeness |
|---|---|---|
| Revision change applied | shared `role="status"` region in main | polite |
| Stale acknowledgment / race result | ack card `role="status"` | polite |
| Offer withdrawn / rejected | offer card `role="status"`, focus to its heading | polite |
| Purchase PENDING / CONFIRMED / UNKNOWN | purchase region `role="status"` | polite |
| Purchase FAILED, load errors | `role="alert"` | assertive |
| Dev panel log | `role="log"` | polite |
| Offer expiry minute ticks | **not announced** except at "Expires in 2 min" and "Expired" | polite |

One message per transition; never re-announce unchanged content on polling.

## 7. Motion (SC 2.3.3 advisory, 2.2.2)

- Respect `prefers-reduced-motion` (INTERACTION_RULES §8).
- No auto-playing, moving or blinking content > 5s; nothing flashes.

## 8. Time limits (SC 2.2.1)

- Offer expiry is a server-side business rule, not a UI timeout; when expired, the user can always "Check eligibility again". State the expiry time as text from the start.

## 9. Forms and errors (SC 3.3.1-3.3.3, 3.3.7)

- Correction time input has a visible label, current value shown, and a server error explained in text next to the field ("06:35 is after the window end 07:00" style). No data the user entered is cleared on error.
- No authentication, no CAPTCHA, no real payment fields exist in this prototype.

## 10. Content honesty as accessibility

- The disclosure banner is real text (not an image) and in the accessibility tree first.
- Synthetic data is labelled: every caregiver/child name is visibly fictional ("Parent A", "Caregiver B", "Demo child").
