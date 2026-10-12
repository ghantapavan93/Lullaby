# RT-1: Long path ids crash the request (500)

**Severity:** P2. Any authenticated member, including a caregiver, can trigger 500s. No data is exposed, but the denial's audit row is lost.
**Test:** `tests/redteam/test_rt_security.py::test_rt_malformed_input_is_rejected_not_500[long window id | long window id on correction | long intent id | long decision id | long device id]`

## Steps
1. Seed the demo and sign in as any member (e.g. Parent A).
2. `GET /v1/windows/<200 x "w">/brief` with a valid bearer token.
   Same for `GET /v1/purchase-intents/<300 chars>`, `GET /v1/capability-decisions/<300 chars>`,
   `GET /v1/devices/<300 chars>/plan-preview`, `POST /v1/windows/<200 chars>/corrections`.

## Observed vs expected
- Observed: `500 Internal Server Error`. psycopg `StringDataRightTruncation: value too long for type character varying(120)`.
- Expected: `404 NOT_FOUND` (as for any other unknown id) or `422`.
- Threshold: a 100-char id answers 404 (regression case `100-char window id (under the limit)`); above about 103 characters the audit row's `resource` exceeds 120.

## Root cause
Path parameters are unbounded `str` (`app/main.py:191, 205, 213, 226, 240, 251, 260, 307, 327, 377`).
Every lookup writes an authorization record with `resource=f"window:{window_id}"` (`app/main.py:194`) or
`resource=f"{model.__tablename__}:{resource_id}"` (`app/security.py:144`) into
`authorization_log.resource String(120)` (`app/models.py:446`). The kept record is committed after the
404 is raised (`app/db.py` `get_db` / `_write_after_failure`). That commit fails, so the 404 becomes a 500
and the denial is not audited.

## Smallest fix
Bound every id path parameter (`Path(min_length=1, max_length=40)`), and truncate `resource` to 120
characters in `security.record_authorization`.
