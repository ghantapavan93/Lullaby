"""Every error a client can get is one JSON shape: {code, message, trace_id, ...}.
A mobile client decodes errors with one model (API review, 2026-10-12)."""
from .conftest import PARENT


def test_bad_input_uses_the_error_envelope_and_never_echoes_the_input(client, demo):
    long = "x" * 5000
    r = client.post("/v1/capability-decisions", headers=PARENT, json={"feature": long})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_INPUT" and body["message"] and "trace_id" in body
    assert long not in r.text, "the rejected value was echoed back"
    assert all({"loc", "type"} <= set(f) for f in body["fields"])


def test_a_missing_idempotency_key_is_named_in_the_envelope(client, demo):
    r = client.post("/v1/purchase-intents", headers=PARENT, json={})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_INPUT"


def test_trailing_slashes_are_not_redirected_to_an_internal_address(client, demo):
    r = client.get("/v1/me/", headers=PARENT, follow_redirects=False)
    assert r.status_code == 404 and "location" not in r.headers
    assert r.json()["code"] == "NOT_FOUND"


def test_a_wrong_method_uses_the_envelope(client, demo):
    r = client.put("/v1/me", headers=PARENT)
    assert r.status_code == 405 and r.json()["code"] == "METHOD_NOT_ALLOWED"
