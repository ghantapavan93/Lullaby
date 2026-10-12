# RT-6: `?revision=0` silently returns the current revision

**Severity:** P3. A client that asks for a revision that does not exist is shown a different one with 200.
**Test:** `tests/redteam/test_rt_security.py::test_rt_revision_zero_is_not_silently_the_current_revision`

## Steps
1. Seed the demo (the window is at revision 4), sign in as Parent A.
2. `GET /v1/windows/{w}/brief?revision=0`

## Observed vs expected
- Observed: `200` with `"revision": 4, "is_current": true`.
- Expected: `404 NOT_FOUND "no such revision"`, as for `?revision=-3` and `?revision=99`.
  `/changes?since_revision=0` already answers 404.

## Root cause
`app/main.py:196`: `number = revision or window.current_revision` treats `0` as "not given".

## Smallest fix
`number = window.current_revision if revision is None else revision`.
