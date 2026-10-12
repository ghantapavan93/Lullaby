# RT-2: A NUL byte in any string field crashes the request (500)

**Severity:** P2. Any authenticated member can trigger it on every write and on id path parameters. No data is exposed.
**Test:** `tests/redteam/test_rt_security.py::test_rt_malformed_input_is_rejected_not_500[NUL in ...]` (8 cases)

## Steps
Seed the demo, sign in as Parent A, then send any of:
- `GET /v1/windows/ab%00cd/brief`
- `POST /v1/windows/{w}/corrections` with `"client_event_id": "rt-nul-\u0000-key"` or `"reason": "a\u0000b"`
- `POST /v1/windows/{w}/acknowledgments` with `"revision_id": "r\u0000"`
- `POST /v1/capability-decisions` with `"feature": "a\u0000"` (or `device_id`)
- `POST /v1/purchase-intents` with `"decision_id": "d\u0000"`
- `POST /v1/render-receipts` with `"subject_id": "x\u0000"`
- `POST /v1/analytics` with `"event_id": "rt-nul-\u0000"`
- `POST /v1/dev/sessions` with `"member_id": "a\u0000"` (dev only; seen in the probe, not in the suite)

## Observed vs expected
- Observed: `500`. psycopg `DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes`
  (VARCHAR parameters), or `UntranslatableCharacter: unsupported Unicode escape sequence` (JSONB `reason`).
- Expected: `422`. Ledger item F-15 says bad input gets a 4xx, not a 500.

## Root cause
Request models accept any code point (`app/main.py:98` `Id = Field(min_length=1, max_length=40)`, and the
other string fields in `app/main.py:101-143`). Path parameters are not validated. The first SQL statement
or JSONB insert carrying the string fails inside psycopg. The `ValueError` handler (`app/main.py:71`) does not
cover `sqlalchemy.exc.DataError`.

## Smallest fix
Reject `\x00` at the boundary. Use a shared constrained string type (e.g. `pattern=r"^[^\x00]*$"`) for every
request string and path id, or a middleware that answers 422 for any request whose decoded path or body contains `\x00`.
