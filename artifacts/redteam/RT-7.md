# RT-7: A duplicate record forces a renewed review although nothing changed

**Severity:** P3. No minutes are double-counted. Caregivers are asked to re-review an unchanged fact, and
the briefing lists the same block twice.
**Test:** `tests/redteam/test_rt_evidence.py::test_rt_duplicate_record_does_not_require_renewed_review`

## Steps
1. Seed the demo. Caregiver B acknowledges revision 4 (all five facts).
2. Re-deliver the camera's 04:20-06:50 sleep record under a new event key, as a re-sent camera sync would:
   `engine.ingest(... NewEvidence("rt-dup-sleep-3b", "sleep_interval", 04:20, 06:50, {...}))`.
   There is no HTTP ingestion route; the test calls the real engine under the household lock.
3. Revision 5 is written. Read `/v1/windows/{w}/brief` and `/v1/windows/{w}/changes`.

## Observed vs expected
- Observed: revision 5 has the same `minutes` (640) and the same `sources` (`["CAMERA"]`), but a different
  `recorded_sleep` fingerprint. Caregiver B's `recorded_sleep` becomes `changed_since_acknowledgment`.
  `recorded_sleep.intervals` lists 04:20-06:50 twice.
- Expected: an identical duplicate changes neither value, provenance nor uncertainty, so the fingerprint and
  the caregiver's acknowledgment stand. DOMAIN.md: "A fingerprint is sha256 over a fact's value, provenance
  and uncertainty"; I6: "Duplicates and overlaps never add minutes".
- The same should apply to a record fully contained in another one (an overlap that adds no time).
  This is inferred from the same code path and was not run.

## Root cause
`app/briefing.py:198`: the `recorded_sleep` semantic includes one `(start, end, source)` tuple per *event*,
and `app/briefing.py:146-150` builds that list from every clipped event, so a duplicate event adds a tuple.

## Smallest fix
Fingerprint the de-duplicated set: `sorted({(i["start"], i["end"], i["source"]) for i in f["intervals"]})`,
or the per-source union of intervals. Optionally de-duplicate the displayed list the same way.
