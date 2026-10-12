# RT-4: A non-ASCII token signature crashes authentication (500, no credentials needed)

**Severity:** P2. Needs no credentials and works on every authenticated route. It does not bypass authentication, but the caller gets a 500 instead of a 401.
**Test:** `tests/redteam/test_rt_security.py::test_rt_non_ascii_token_signature_is_401_not_500`

## Steps
`GET /v1/me` with raw header bytes `Authorization: Bearer pl1.mem_parent_a.\xe9\xe9`.
Starlette decodes header bytes as latin-1, which gives the string `"éé"`. Reproduced in the TestClient and
by calling the raw ASGI app.

## Observed vs expected
- Observed: `500`, `TypeError: comparing strings with non-ASCII characters is not supported`.
- Expected: `401 UNAUTHENTICATED` ("invalid session token").
- A non-ASCII *member* part with an ASCII signature correctly gets 401 (`test_rt_non_ascii_member_part_of_token_is_401`).

## Root cause
`app/security.py:90`: `hmac.compare_digest(sig, expected)` on two `str` values raises `TypeError` when either
contains non-ASCII characters. `TypeError` is not mapped to a 4xx.

## Smallest fix
Compare bytes, e.g. `hmac.compare_digest(sig.encode("utf-8", "surrogateescape"), expected.encode())`, or
answer 401 when `not sig.isascii()` before comparing.
