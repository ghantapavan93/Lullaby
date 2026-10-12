# RT-5: Dev claim feed crashes on naive or far-future dates (500)

**Severity:** P3. Development-only route: it needs an owner/admin session and `PROOF_LAYER_DEV=1`.
**Test:** `test_rt_malformed_input_is_rejected_not_500[naive dev claim start | far-future dev claim]`

## Steps
With dev mode on, as Parent A:
1. `POST /v1/dev/claims` `{"channel":"web","external_ref":"rt-naive","event_status":"active","plan_code":"memories","starts_at":"2026-10-01T00:00:00"}`
2. `POST /v1/dev/claims` `{"channel":"web","external_ref":"rt-far","event_status":"active","plan_code":"memories","starts_at":"9999-12-01T00:00:00+00:00","days":3650}`

## Observed vs expected
- Observed: (1) `500 TypeError: can't compare offset-naive and offset-aware datetimes`;
  (2) `500 OverflowError: date value out of range`.
- Expected: `422`. Corrections already answer `AMBIGUOUS_TIMESTAMP` for naive times.

## Root cause
`ClaimIn.starts_at: Optional[datetime]` (`app/main.py:531`) accepts naive datetimes. The new claim is flushed,
but the identity-mapped object keeps the naive value, so `entitlements.effective_plan` compares it with an
aware `now` (`app/entitlements.py:213`). `starts + timedelta(days=body.days)` (`app/main.py:550`) overflows near year 9999.

## Smallest fix
Use `pydantic.AwareDatetime` for `starts_at` and bound it (e.g. year <= 2100), or validate with
`intervals.utc()` and turn `OverflowError` into a 422.
