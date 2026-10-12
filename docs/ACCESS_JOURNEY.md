# Access journey

Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.

The parent-facing side of purchase-to-access: "I paid for this. Why can't I use it?" Every status, stage and button comes from the server (`docs/ACCESS_RECONCILIATION_CONTRACT.md`). The client only lays them out (`proof-layer/static/app.js`, section "purchase to access").

## The five screens

| Screen | Route | What the parent sees | Server source |
|---|---|---|---|
| Family home | `#/home` (default) | One card per camera: status pill, headline, a calm sentence, a five-dot stage rail, "Check my plan". Owners also see their purchases and where each is connected. | `GET /v1/access` |
| Purchase recognized | `#/access/{camera}` | The stage rail with one fact per stage: what was paid, on which account, on which camera, which plan, which features. Payment and camera assignment are separate facts. | `GET /v1/access/cameras/{id}` |
| Connect to the right camera | Connect sheet on the same page | Purchase, store status, camera and mount, what will work here, what the mount still limits, "Simulated. Nothing is charged." One idempotency key per attempt. A stale screen is refused in the sheet. | `POST /v1/access/bind` |
| Access verified | Same page after the check | "Access verified · time", what works, and any mount limits still shown. A line explains that connecting, verifying and using are three different things. | `POST /v1/access/verify` |
| We need another step | Same page, other statuses | Waiting, still confirming, on another camera, already has this plan, refunded, conflicting, not verified. Each one has a calm explanation and the actions the server allows. None offers a purchase. | same |

A code or receipt lookup sits below ("Have a code or a receipt?"). It runs `POST /v1/access/check` against the selected camera. This is how "paid, wrong camera" shows: `ALREADY_ASSIGNED`, a support summary to copy, and no connect button.

## Rules the screens keep

- **No status shows an offer or a buy button.** A browser test checks five scenarios across Home and both cameras.
- **Connected isn't verified.** After a connect, the page shows "Connected. Checking your camera" until a separate check runs. The UI starts that check right away, as its own request.
- **Hardware limits stay visible**, both before connecting (connect preview) and after verification.
- **Caregivers see status, not purchases.** They see "Ask Parent A to connect it". Connect and the lookup form are not rendered for them.
- **Waiting and unknown never celebrate.** Dashed outlines, a clock or question icon, and text.
- **Colour is never the only signal.** Every state has an icon and a label.

## Navigation

- **Side nav (≥ 1024 px)** has three groups:
  - Family: Home, Access journey.
  - Family evidence lab: Night briefing, What changed, Plans & features.
  - For engineers: Scenario Studio, Growth Lab, Customer evidence, Engineering X-Ray, Scenario controls.
- **Customer evidence (`#/customers`):** public reports paraphrased with dates and sources. Each card has "Try: …" buttons that build its scenario through the API and open the journey on it. Evidence: `research/EVIDENCE_TO_SCENARIO_MAP.md`.
- **Phone tab bar:** Home, Access, Evidence, Studio, X-Ray.
- **Evidence tabs:** the evidence pages share a tab row, so "What changed" and "Plans & features" stay reachable on a phone.
- **Old links** (`#/briefing`, `#/changes`, `#/features`) still work.

## Present Mode

Every audience now opens with the access story: "paid, not active", then connect, then verify. Product and engineering add "paid, wrong camera"; engineering adds the certification lab. Then the existing evidence and recovery steps follow. Each step waits for the server to reach the state its caption describes.

## Not verified

- Real store receipts and real support contact: this prototype contacts no one.
- Screen-reader wording was checked by axe rules only. A person has not reviewed it with a screen reader.
