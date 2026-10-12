# Demo walkthroughs

Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.

Start the app with `python scripts/demo.py` (from `proof-layer/`). The app opens on **Home**. Desktop is at least 1280 px wide; on a phone, the same steps run through the bottom tabs and the X-Ray sheet.

## 60-second overview

**The problem (10 s).** A parent's plan renewed with the store, but the app asks them to subscribe. That is a symptom customers report. We don't know the cause in any real case, so the prototype shows what a trustworthy answer looks like.

| Time | Do | Say |
|---|---|---|
| 0:10 | Open **Scenario Studio**, then *Plan ended, no renewal* → **Build**. Then open **Access journey** | "This family's Memories plan ended five days ago, and no renewal has arrived." |
| 0:20 | Point at the status card and **Plan history** | "It says what ended and when, and that Sleep still works. There's no paywall, and nothing to buy." |
| 0:35 | Click **Store sends the renewal** (the dashed simulation bar) | "The store's renewal arrives." |
| 0:45 | Point at **Renewed · runs to …**, *NextNap: Works here*, and the second Memories period tagged *renewal* | "Same camera. No new purchase, no new connect. The feature is back." |
| 0:55 | Click **Present** if time allows | "Every step here runs against the real API and database." |

## Five-minute engineering walkthrough

1. **The model (1 min).**
   - A camera's plan is a pure function of append-only claims (`entitlements.effective_plan`).
   - A store's purchase evidence is versioned per provider (`purchase_evidence.purchase_state`).
   - Status is one read-only decision with a 12-step trace (`access.assess`). Open **X-Ray → Access decision**.

2. **The gap the research exposed (1 min).**
   - Claims were unique per (channel, ref, status), so a renewal had nowhere to go.
   - Migration 0007 keys claims by period. 0008 adds the end, so an extension with the same start is a new fact.
   - An exclusion constraint keeps one camera per purchase in every channel.
   - Show **X-Ray → Plan periods on this camera** after the renewal: two Memories periods, one camera.

3. **Edge cases under a controlled clock (1 min).**
   - `tests/test_subscription_model.py` freezes application time (`app/clock.py`) and drives the API through ten operations:
     - renewal;
     - cheaper-plan renewal;
     - same-start extension;
     - late older period;
     - future period (`PLAN_SCHEDULED`);
     - refund before verification;
     - cross-household ownership;
     - same instant in another offset;
     - trial expiry;
     - concurrent connect and verify.
   - Each test asserts the stored claims, the effective entitlement, the parent's status and the allowed recovery.

4. **Ordering (30 s).**
   - A check whose transaction began before the connect committed waits for the household lock.
   - Its `created_at` is `clock_timestamp()`, so it sorts after the connect.
   - With `now()` (transaction start) it would sort first, and the camera would keep reading "Connected. Checking your camera" until another check ran. Mutant M42 shows the test catching exactly that.

5. **Upgrades (30 s).**
   - `tests/test_migration_upgrade.py` builds isolated databases at 0006 with realistic data, then upgrades.
   - Valid data upgrades and keeps every row.
   - A purchase already split across cameras stops with a diagnostic naming it, and the database stays at 0006.
   - A role without the extension privilege is told what a DBA must run; after the DBA runs it, the upgrade succeeds.

6. **Proof (1 min).**
   - **Growth Lab:** certification is computed from the JUnit report; "not run" is never "passed".
   - **Mutation:** `artifacts/mutation/mutation-report.md`.
   - **The failed four-process soak stays visible:** the cause is narrowed (a worker freezes accepting connections on the shared Windows socket) but not fixed, so it is a release gate, not a footnote.

## What not to say

- Don't call the renewal symptom a confirmed bug in anyone's real system. It is a reported symptom.
- Don't say Nanit lacks reconciliation. Internal systems aren't public.
- Don't quote counts of public reports as rates. They are a selected, partly agent-coded sample.
