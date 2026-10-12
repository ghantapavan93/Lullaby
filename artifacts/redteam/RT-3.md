# RT-3: Revision numbers beyond int4 crash briefing reads (500)

**Severity:** P3. Read-only endpoints, any member.
**Test:** `test_rt_malformed_input_is_rejected_not_500[revision beyond int4 | since_revision beyond int4]`

## Steps
1. Seed the demo, sign in as Parent A.
2. `GET /v1/windows/{w}/brief?revision=2147483648`
3. `GET /v1/windows/{w}/changes?since_revision=99999999999999999999`

## Observed vs expected
- Observed: `500`, psycopg `NumericValueOutOfRange: integer out of range`.
- Expected: `404 NOT_FOUND` (no such revision) or `422`. Negative values already get 404.

## Root cause
`revision: Optional[int]` (`app/main.py:191`) and `since_revision: Optional[int]` (`app/main.py:260`) are
unbounded Python ints compared against the `integer` column `briefing_revisions.revision`.

## Smallest fix
`revision: Optional[int] = Query(None, ge=1, le=2_147_483_647)`, and the same for `since_revision`.
