# Demo

## Start (once per machine)

```bash
cd proof-layer
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # macOS/Linux: .venv/bin/pip
.venv/Scripts/python scripts/devdb.py up              # private PostgreSQL on 127.0.0.1:54329, migrated and seeded
.venv/Scripts/python scripts/demo.py                # http://127.0.0.1:8710, scenario controls on
```

Open http://127.0.0.1:8710. Reset at any time with **Scenario controls → reset demo**, or `python scripts/devdb.py reset`.

Desktop width (1024px or wider) shows the scenario controls in the side nav. On a phone-sized window they sit at the bottom of the X-Ray drawer.

## Fastest path: Present Mode

Press **Present** in the header, or open `/?present=recruiter` (about 45 s), `?present=product` (about 1 min) or `?present=engineering` (about 1.5 min, including the worker crash and recovery). It drives the real app; captions quote live state and wait for the backend. Pause, Next (→) and Exit (Esc) are on the bar.

## The five-step story (about 90 seconds)

| # | Do | Point at | Proves |
|---|---|---|---|
| 1 | Open **Night briefing** as Parent A | 10 h 40 min recorded, 94.4% camera observation, 40 min unobserved (hatched, 03:40–04:20), Rev 4 | Missing observation stays unknown; coverage isn't sleep quality (Nanit's own graph also marks camera disconnects) |
| 2 | Scenario controls → **show both caregivers' screens**, then **Parent A corrects final wake → 06:35** (or use **Correct final wake** in the UI) | Left screen: Rev 5, 10 h 25 min "was 10 h 40 min", 06:35 *Parent reported*; coverage still 94.4%. Right screen (Caregiver B) still shows Rev 4 | Original evidence kept; one new revision; the old screen isn't swapped underneath |
| 3 | On Caregiver B's screen click **Acknowledge revision 4** (or **Caregiver B acknowledges on their screen**) | "You acknowledged the earlier version. Two details have changed since then." Final wake and recorded sleep shown before → after. **What changed** shows per-fact status | The acknowledgment binds to what was displayed, not the latest |
| 4 | **Feature availability** → Developmental milestones → **Check availability** | "Needs a plan", hardware Met, offer: Simulated Milestones plan — $300/year, terms, expiry | An offer appears only because the plan is the only blocker |
|   | Scenario controls → **camera moved → Flex Stand**, then click **Start simulated upgrade** on the old offer | "We didn't submit this offer… Nothing was charged." → **Check eligibility again** → "Blocked by hardware", no offer | Stale offer rejected before any provider call; never sells a plan that can't help |
| 5 | **camera back → Floor Stand** → **Check availability** → **interrupt worker after provider success** | Pill: "Pending — waiting for the provider". X-Ray: intent SUBMITTED, provider ledger SUCCEEDED, attempts 1 | A real worker process died between the provider's commit and ours |
|   | Wait about 3 seconds → **restart worker (reconcile from ledger)** | "Confirmed (simulated)"; plan chip shows Memories/Milestones; X-Ray: one provider operation, same key, transitions PREPARED → SUBMITTED → CONFIRMED (`reconciled_from_provider_ledger`) | Recovery from the provider's durable record, without inventing a result |

Close with the principle: **when the underlying facts change, the system changes its answer without losing the truth.**

## Optional beats

- **send 2 simultaneous requests (same key)**: the log shows one `201` and one `200 (replayed)`, with one intent.
- **next provider reply: decline**, then buy: "Failed — not charged". It stays failed after reconciliation.
- **next provider reply: pending**, then buy: "Outcome unknown — checking". It stays unknown until **provider settles pending →…**, then **restart worker**.
- Open **Engineering X-Ray**: revisions, fingerprints (same/changed), evidence including the superseded camera record, authorization decisions, outbox, provider ledger, exposure receipts.

## If something goes wrong live

- The page says it can't load: is the database up? Run `python scripts/devdb.py status`.
- "restart worker" didn't confirm: the crashed worker's 3-second lease hadn't expired yet. Click it again.
- A screen looks out of date: that's by design. Use **Show revision N**.

## Since the research gate: cameras, channels and past nights

| Do | Point at | Proves |
|---|---|---|
| Feature availability → pick **Playroom camera** → Check availability | Hardware block, no offer; the plan table shows Milestones adds only 7-day history there | Eligibility is per camera; a plan can't fix a mount |
| Scenario controls → **Apple purchase pending (Milestones)** → check the Nursery camera | "We're confirming a recent purchase… nothing more to buy" | No paywall while a purchase is being confirmed |
| **Apple confirms it late** | Nursery plan → Milestones, team up to 50 (an assumption: one Nanit page says unlimited) | The plan and team limit move together |
| **same Google event delivered twice** (a simulated channel; Nanit routes Android purchases through my.nanit.com) | Log: second delivery "(duplicate: no-op)" | Idempotent channel events |
| **activation code on Nursery** then **same code on Playroom** | Second is refused: `CODE_ALREADY_REDEEMED` | One code, one camera |
| **refund the Apple purchase** | Plan falls back to the next active claim | A revoked transaction never grants |
| **Parent A corrects a past night (Oct 8 → 06:15)** | Last 7 nights: Oct 8 edited, average updated | Past-night edits are reflected on the next read |
