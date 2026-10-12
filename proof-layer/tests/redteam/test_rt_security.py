"""Red team: tenancy, revocation, dev isolation, token hygiene, malformed input (I1, SECURITY.md)."""
import json
import logging
import os
import subprocess
import sys

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import func, select, text

from app import seed
from app.db import SessionLocal, transaction
from app.main import app
from app.models import Acknowledgment, BriefingWindow, EvidenceEvent, PurchaseIntent

from ..conftest import CAREGIVER, OTHER, PARENT, ROOT, auth
from .conftest import ALL_FACTS

# The media route takes a signed link instead of a bearer token; its own tests
# cover forged, expired, foreign and revoked links (tests/test_video_evidence.py).
SIGNED_LINK_ROUTES = ("/v1/video/media/",)
# Public on purpose: it answers "no review gateway here" with a constant, reads
# nothing and writes nothing (pinned by test_rt_the_workspace_route_is_a_constant).
PUBLIC_ROUTES = ("/v1/workspace",)
V1_ID_ROUTES = sorted(
    {(r.path, m) for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/v1/")
     and not r.path.startswith("/v1/dev/") and not r.path.startswith(SIGNED_LINK_ROUTES) and "{" in r.path
     for m in r.methods})
V1_ROUTES = sorted(
    {(r.path, m) for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/v1/")
     and not r.path.startswith("/v1/dev/") and not r.path.startswith(SIGNED_LINK_ROUTES)
     and r.path not in PUBLIC_ROUTES for m in r.methods})
DEV_ROUTES = sorted(
    {(r.path, m) for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/v1/dev/") for m in r.methods})


def household_ids(client, headers, household, child, device, offer_feature) -> dict:
    """Real ids owned by one household: window, decision, intent, device, revision, event."""
    with SessionLocal() as db:
        window = db.execute(select(BriefingWindow).where(BriefingWindow.child_id == child)
                            .order_by(BriefingWindow.local_date.desc())).scalars().first()
        event = db.execute(select(EvidenceEvent.id).where(EvidenceEvent.household_id == household,
                                                          EvidenceEvent.kind == "sleep_interval",
                                                          EvidenceEvent.started_at >= window.starts_at,
                                                          EvidenceEvent.ended_at <= window.ends_at)).scalars().first()
    revision = client.get(f"/v1/windows/{window.id}/brief", headers=headers).json()
    d = client.post("/v1/capability-decisions", headers=headers,
                    json={"feature": offer_feature, "device_id": device}).json()
    i = client.post("/v1/purchase-intents", headers=headers | {"Idempotency-Key": f"rt-ids-{household}"},
                    json={"decision_id": d["decision_id"], "offer_id": d["offer"]["offer_id"]})
    assert i.status_code == 201, i.text
    clip = client.post("/v1/dev/video/sample", headers=headers, json={"device_id": device})
    assert clip.status_code == 200, clip.text
    return {"window_id": window.id, "decision_id": d["decision_id"], "intent_id": i.json()["intent_id"],
            "device_id": device, "revision_id": revision["revision_id"], "revision": revision["revision"],
            "event_id": event, "asset_id": clip.json()["asset"]["id"]}


def call(client, path, method, ids, headers):
    url = path
    for name in ("window_id", "decision_id", "intent_id", "device_id", "asset_id"):
        url = url.replace("{" + name + "}", ids[name])
    assert "{" not in url, f"unmapped path parameter in {path}: extend this fuzzer"
    body = None
    if path.endswith("/corrections"):
        body = {"target_event_id": ids["event_id"], "expected_revision": ids["revision"],
                "client_event_id": "rt-foreign-correction", "corrected_end": None}
    elif path.endswith("/acknowledgments"):
        body = {"revision_id": ids["revision_id"], "displayed_facts": ALL_FACTS}
    elif path.endswith("/annotations"):
        body = {"label": "not_sure", "t_start": 0, "t_end": 1, "expected_revision": 1}
    elif path.endswith("/acknowledge"):
        body = {"revision": 1}
    return client.request(method, url, headers=headers | {"Idempotency-Key": "rt-foreign-key"}, json=body)


def snapshot():
    with SessionLocal() as db:
        return (db.execute(select(BriefingWindow.id, BriefingWindow.current_revision)).all(),
                db.execute(select(func.count()).select_from(Acknowledgment)).scalar_one(),
                db.execute(select(func.count()).select_from(EvidenceEvent)).scalar_one(),
                db.execute(select(func.count()).select_from(PurchaseIntent)).scalar_one())


# ---------------------------------------------------------------- foreign ids (I1)

def test_rt_every_id_route_hides_the_other_household(client, demo):
    assert len(V1_ID_ROUTES) >= 10, V1_ID_ROUTES
    a = household_ids(client, PARENT, seed.HOUSEHOLD, seed.CHILD, seed.CAMERA, "nextnap")
    b = household_ids(client, OTHER, seed.OTHER, seed.OTHER_CHILD, seed.OTHER_CAMERA, "video_history_7d")
    before = snapshot()
    leaks = []
    for attacker, victim in ((OTHER, a), (PARENT, b), (CAREGIVER, b)):
        for path, method in V1_ID_ROUTES:
            r = call(client, path, method, victim, attacker)
            # 404 (indistinguishable from missing), or a caregiver's role denial before any lookup.
            ok = r.status_code == 404 or (attacker is CAREGIVER and r.status_code == 403
                                          and r.json()["code"] == "FORBIDDEN")
            if not ok:
                leaks.append((method, path, r.status_code, r.text[:120]))
    assert leaks == []
    assert snapshot() == before


def test_rt_foreign_ids_inside_request_bodies_are_rejected(client, demo):
    b = household_ids(client, OTHER, seed.OTHER, seed.OTHER_CHILD, seed.OTHER_CAMERA, "video_history_7d")
    w = demo["window_id"]
    before = snapshot()
    attempts = [
        client.post(f"/v1/windows/{w}/corrections", headers=PARENT, json={
            "target_event_id": b["event_id"], "expected_revision": 4, "client_event_id": "rt-foreign-target",
            "corrected_end": "2026-10-10T06:00:00-04:00"}),
        client.post(f"/v1/windows/{w}/acknowledgments", headers=CAREGIVER, json={
            "revision_id": b["revision_id"], "displayed_facts": ALL_FACTS}),
        client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap", "device_id": seed.OTHER_CAMERA}),
        client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": "rt-foreign-dec"},
                    json={"decision_id": b["decision_id"], "offer_id": "offer-milestones-year-cat-2026-10-public"}),
        client.post("/v1/render-receipts", headers=PARENT, json={
            "render_id": "rt-foreign-render-1", "surface": "night_briefing", "subject_id": b["revision_id"]}),
        client.post("/v1/render-receipts", headers=PARENT, json={
            "render_id": "rt-foreign-render-2", "surface": "feature_availability", "subject_id": b["decision_id"]}),
        client.post("/v1/dev/mount", headers=PARENT, json={"mount": "wall_mount", "device_id": seed.OTHER_CAMERA}),
        client.post("/v1/dev/claims", headers=PARENT, json={"device_id": seed.OTHER_CAMERA, "channel": "web",
                                                            "external_ref": "rt-foreign-claim", "event_status": "active",
                                                            "plan_code": "milestones"}),
        client.post("/v1/dev/provider/resolve", headers=PARENT, json={"operation_key": f"op_{b['intent_id']}",
                                                                      "status": "FAILED"}),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts), [r.text for r in attempts]
    assert snapshot() == before
    with SessionLocal() as db:
        assert db.execute(text("SELECT mount FROM devices WHERE id=:d"), {"d": seed.OTHER_CAMERA}).scalar_one() == "flex_stand"
        assert db.execute(text("SELECT count(*) FROM entitlement_claims WHERE external_ref='rt-foreign-claim'")).scalar_one() == 0


# ---------------------------------------------------------------- revocation and roles

ROUTE_BODIES = {
    "/v1/windows/{window_id}/corrections": {"target_event_id": "x", "expected_revision": 4,
                                            "client_event_id": "rt-revoked-corr"},
    "/v1/windows/{window_id}/acknowledgments": {"revision_id": "x", "displayed_facts": ["recorded_sleep"]},
    "/v1/capability-decisions": {"feature": "nextnap"},
    "/v1/purchase-intents": {"decision_id": "x", "offer_id": "y"},
    "/v1/render-receipts": {"render_id": "rt-revoked-render", "surface": "night_briefing", "subject_id": "x"},
    "/v1/analytics": {"event_id": "rt-revoked-event", "name": "briefing_viewed", "properties": {}},
    "/v1/access/check": {"device_id": seed.CAMERA, "channel": "web", "external_ref": "web-x"},
    "/v1/access/bind": {"device_id": seed.CAMERA, "channel": "web", "external_ref": "web-x",
                        "expected_state_revision": 0},
    "/v1/access/verify": {"device_id": seed.CAMERA},
    "/v1/video/assets/{asset_id}/annotations": {"label": "not_sure", "t_start": 0, "t_end": 1, "expected_revision": 1},
    "/v1/video/assets/{asset_id}/acknowledge": {"revision": 1},
}


@pytest.mark.parametrize("member,headers", [(seed.CAREGIVER_B, CAREGIVER), (seed.PARENT_A, PARENT)])
def test_rt_revoked_member_is_refused_on_every_route(client, demo, member, headers):
    ids = {"window_id": demo["window_id"], "decision_id": "dec_x", "intent_id": "pi_x", "device_id": seed.CAMERA,
           "asset_id": "vas_x"}
    with transaction() as db:
        db.execute(text("UPDATE members SET revoked_at = now() WHERE id = :m"), {"m": member})
    before = snapshot()
    allowed = []
    for path, method in V1_ROUTES:
        url = path
        for k, v in ids.items():
            url = url.replace("{" + k + "}", v)
        r = client.request(method, url, headers=headers | {"Idempotency-Key": "rt-revoked-key"},
                           json=ROUTE_BODIES.get(path) if method == "POST" else None)
        if not (r.status_code == 403 and r.json().get("code") == "ACCESS_REVOKED"):
            allowed.append((method, path, r.status_code))
    assert allowed == []
    assert snapshot() == before


def test_rt_role_demotion_takes_effect_on_the_same_token(client, demo):
    with transaction() as db:
        db.execute(text("UPDATE members SET role='caregiver', can_bill=false WHERE id=:m"), {"m": seed.PARENT_A})
    assert client.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).status_code == 403
    r = client.post(f"/v1/windows/{demo['window_id']}/corrections", headers=PARENT, json={
        "target_event_id": demo["final_wake_event_id"], "expected_revision": 4,
        "client_event_id": "rt-demoted-corr", "corrected_end": "2026-10-10T06:30:00-04:00"})
    assert r.status_code == 403
    assert client.get("/v1/household/commercial", headers=PARENT).status_code == 403
    assert client.get("/v1/xray", headers=PARENT).json()["commerce_visible"] is False


def test_rt_tokens_are_bound_to_one_member_and_cannot_be_forged(client, demo):
    good = PARENT["Authorization"].removeprefix("Bearer ")
    _, member, sig = good.split(".", 2)
    # Flip the last signature character to a *different* one: the secret is random per
    # process, so a fixed "0" equalled the real signature in 1 run of 16 (found by
    # mutation testing, where the flake was credited with two kills).
    flipped = sig[:-1] + ("1" if sig[-1] == "0" else "0")
    forged = [f"pl1.{seed.OTHER_OWNER}.{sig}", f"pl2.{member}.{sig}", f"pl1.{member}.{flipped}",
              f"pl1.{member}.", "pl1..", f"pl1.{member}.{sig}.extra", f"Bearer pl1.{member}.{sig}", ""]
    for token in forged:
        r = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, (token, r.text)


# ---------------------------------------------------------------- dev controls off

DEV_OFF_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import app.main as m
from app.security import issue_token
from app import seed
bodies = {"/v1/dev/sessions": {"member_id": seed.PARENT_A}, "/v1/dev/mount": {"mount": "flex_stand"},
          "/v1/dev/claims": {"channel": "web", "external_ref": "rt-devoff", "event_status": "active",
                             "plan_code": "milestones"},
          "/v1/dev/provider/next": {"behavior": "decline"},
          "/v1/dev/provider/resolve": {"operation_key": "op_x", "status": "SUCCEEDED"},
          "/v1/dev/worker": {"crash_after_provider": False}, "/v1/dev/reset": None}
tok = {"Authorization": "Bearer " + issue_token(seed.PARENT_A)}
out = {"DEV": m.DEV, "results": []}
with TestClient(m.app, raise_server_exceptions=False) as c:
    for r in m.app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/v1/dev/"):
            for method in r.methods:
                for h in (tok, {}):
                    resp = c.request(method, r.path, headers=h, json=bodies.get(r.path))
                    out["results"].append([method, r.path, bool(h), resp.status_code])
print("RESULT " + json.dumps(out))
"""


def test_rt_dev_controls_are_unreachable_when_dev_mode_is_off(demo):
    env = {k: v for k, v in os.environ.items() if k != "PROOF_LAYER_DEV"}
    done = subprocess.run([sys.executable, "-c", DEV_OFF_SCRIPT], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=120)
    line = next((x for x in done.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, done.stderr[-2000:]
    out = json.loads(line.removeprefix("RESULT "))
    assert out["DEV"] is False
    assert len({(m, p) for m, p, _, _ in out["results"]}) == len(DEV_ROUTES) >= 8
    assert [r for r in out["results"] if r[3] != 404] == []
    # Nothing the scenario controls touch changed.
    with SessionLocal() as db:
        assert db.execute(text("SELECT mount FROM devices WHERE id=:d"), {"d": seed.CAMERA}).scalar_one() == "floor_stand"
        assert db.execute(text("SELECT count(*) FROM entitlement_claims WHERE external_ref='rt-devoff'")).scalar_one() == 0


# ---------------------------------------------------------------- tokens never logged or stored

def test_rt_tokens_are_absent_from_logs_and_storage(client, demo, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)    # SQL and bound parameters
    try:
        session = client.post("/v1/dev/sessions", json={"member_id": seed.CAREGIVER_B}).json()["token"]
        w = demo["window_id"]
        tokens = [PARENT, CAREGIVER, OTHER, {"Authorization": f"Bearer {session}"},
                  {"Authorization": "Bearer pl1.mem_parent_a.deadbeefdeadbeefdeadbeefdeadbeef"}]
        for h in tokens:
            client.get("/v1/me", headers=h)
            client.get(f"/v1/windows/{w}/brief", headers=h)
            client.post(f"/v1/windows/{w}/acknowledgments", headers=h,
                        json={"revision_id": "nope", "displayed_facts": ["recorded_sleep"]})
            client.post("/v1/capability-decisions", headers=h, json={"feature": "nextnap"})
            client.get("/v1/xray", headers=h)
        with transaction() as db:
            db.execute(text("UPDATE members SET revoked_at=now() WHERE id=:m"), {"m": seed.CAREGIVER_B})
        client.get("/v1/me", headers=CAREGIVER)
    finally:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    secrets_ = [h["Authorization"].removeprefix("Bearer ") for h in tokens]
    signatures = [t.rsplit(".", 1)[1] for t in secrets_]
    captured = caplog.text + "".join(capfd.readouterr())
    assert "pl1." not in captured
    assert not any(s in captured for s in signatures)
    with SessionLocal() as db:
        tables = [t for (t,) in db.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
        for t in tables:
            hits = db.execute(text(f"SELECT count(*) FROM {t} x WHERE x::text LIKE '%pl1.%' "
                                   + "".join(f" OR x::text LIKE '%{s}%'" for s in signatures))).scalar_one()
            assert hits == 0, t


# ---------------------------------------------------------------- malformed input never 500s

def _x(n, reason):
    """RT-n was a reproduced failure; it is fixed and the case stays as a
    regression (artifacts/redteam/RT-n.md has the original reproduction)."""
    return ()


RT1 = _x(1, "path id longer than ~103 chars overflows authorization_log.resource varchar(120) -> 500")
RT2 = _x(2, "a NUL byte in any string reaching PostgreSQL raises psycopg DataError -> 500")
RT3 = _x(3, "revision/since_revision query ints are unbounded; > int4 raises NumericValueOutOfRange -> 500")
RT4 = _x(4, "non-ASCII token signature makes hmac.compare_digest raise TypeError -> 500 (unauthenticated)")
RT5 = _x(5, "dev claims starts_at: naive datetime -> TypeError, far-future -> OverflowError -> 500")


def _corr(w, target, **over):
    return {"target_event_id": target, "corrected_end": "2026-10-10T06:35:00-04:00", "expected_revision": 4,
            "client_event_id": "rt-malformed-0001"} | over


MALFORMED = [
    # name, method, path template, body, headers-extra
    ("huge reason", "POST", "/v1/windows/{w}/corrections", lambda w, t: _corr(w, t, reason="x" * 100_000), {}),
    ("huge client key", "POST", "/v1/windows/{w}/corrections", lambda w, t: _corr(w, t, client_event_id="k" * 5000), {}),
    ("naive corrected_end", "POST", "/v1/windows/{w}/corrections",
     lambda w, t: _corr(w, t, corrected_end="2026-10-10T06:35:00"), {}),
    ("date-only corrected_end", "POST", "/v1/windows/{w}/corrections",
     lambda w, t: _corr(w, t, corrected_end="2026-10-10"), {}),
    ("year 9999 corrected_end", "POST", "/v1/windows/{w}/corrections",
     lambda w, t: _corr(w, t, corrected_end="9999-12-31T23:59:59-14:00"), {}),
    ("year 1 corrected_start", "POST", "/v1/windows/{w}/corrections",
     lambda w, t: _corr(w, t, corrected_start="0001-01-01T00:00:00+14:00"), {}),
    ("end before start", "POST", "/v1/windows/{w}/corrections",
     lambda w, t: _corr(w, t, corrected_start="2026-10-10T06:40:00-04:00", corrected_end="2026-10-10T05:00:00-04:00"), {}),
    ("negative expected_revision", "POST", "/v1/windows/{w}/corrections", lambda w, t: _corr(w, t, expected_revision=-1), {}),
    ("huge expected_revision", "POST", "/v1/windows/{w}/corrections", lambda w, t: _corr(w, t, expected_revision=10 ** 40), {}),
    ("float expected_revision", "POST", "/v1/windows/{w}/corrections", lambda w, t: _corr(w, t, expected_revision=4.5), {}),
    ("nested object as id", "POST", "/v1/windows/{w}/corrections", lambda w, t: _corr(w, t, target_event_id={"$ne": ""}), {}),
    ("unicode reason and key", "POST", "/v1/windows/{w}/corrections",
     lambda w, t: _corr(w, t, client_event_id="rt-ключ-🙂-مفتاح", reason="נכון 🙂 ‮ evil"), {}),
    ("ack 6 facts", "POST", "/v1/windows/{w}/acknowledgments",
     lambda w, t: {"revision_id": "r", "displayed_facts": ALL_FACTS + ["recorded_sleep"]}, {}),
    ("ack unicode fact", "POST", "/v1/windows/{w}/acknowledgments",
     lambda w, t: {"revision_id": "r", "displayed_facts": ["récorded_sleep"]}, {}),
    ("ack nested facts", "POST", "/v1/windows/{w}/acknowledgments",
     lambda w, t: {"revision_id": "r", "displayed_facts": [["recorded_sleep"]]}, {}),
    ("negative price", "POST", "/v1/purchase-intents",
     lambda w, t: {"decision_id": "d", "offer_id": "o", "expected_amount_minor": -1}, {"Idempotency-Key": "rt-mal-neg"}),
    ("huge price", "POST", "/v1/purchase-intents",
     lambda w, t: {"decision_id": "d", "offer_id": "o", "expected_amount_minor": 10 ** 30}, {"Idempotency-Key": "rt-mal-big"}),
    ("short idempotency key", "POST", "/v1/purchase-intents",
     lambda w, t: {"decision_id": "d", "offer_id": "o"}, {"Idempotency-Key": "k"}),
    ("analytics deep nesting", "POST", "/v1/analytics",
     lambda w, t: {"event_id": "rt-mal-deep", "name": "briefing_viewed", "properties": {"revision": [[[[[[[[1]]]]]]]]}}, {}),
    ("analytics huge string", "POST", "/v1/analytics",
     lambda w, t: {"event_id": "rt-mal-huge", "name": "availability_viewed", "properties": {"feature": "a" * 1_000_000}}, {}),
    ("analytics many keys", "POST", "/v1/analytics",
     lambda w, t: {"event_id": "rt-mal-keys", "name": "briefing_viewed", "properties": {f"k{i}": i for i in range(5000)}}, {}),
    ("decision unknown surface", "POST", "/v1/capability-decisions", lambda w, t: {"feature": "nextnap", "surface": "x"}, {}),
    ("dev claim negative days", "POST", "/v1/dev/claims",
     lambda w, t: {"channel": "web", "external_ref": "rt-neg", "event_status": "active", "plan_code": "sleep", "days": -5}, {}),
    ("100-char window id (under the limit)", "GET", "/v1/windows/" + "w" * 100 + "/brief", None, {}),
    ("negative revision", "GET", "/v1/windows/{w}/brief?revision=-3", None, {}),
    ("non-int revision", "GET", "/v1/windows/{w}/brief?revision=NaN", None, {}),
    pytest.param("long window id", "GET", "/v1/windows/" + "w" * 200 + "/brief", None, {}, marks=RT1),
    pytest.param("long window id on correction", "POST", "/v1/windows/" + "w" * 200 + "/corrections",
                 lambda w, t: _corr(w, t), {}, marks=RT1),
    pytest.param("long intent id", "GET", "/v1/purchase-intents/" + "i" * 300, None, {}, marks=RT1),
    pytest.param("long decision id", "GET", "/v1/capability-decisions/" + "d" * 300, None, {}, marks=RT1),
    pytest.param("long device id", "GET", "/v1/devices/" + "v" * 300 + "/plan-preview", None, {}, marks=RT1),
    pytest.param("NUL in path id", "GET", "/v1/windows/ab%00cd/brief", None, {}, marks=RT2),
    pytest.param("NUL in client key", "POST", "/v1/windows/{w}/corrections",
                 lambda w, t: _corr(w, t, client_event_id="rt-nul-\u0000-key"), {}, marks=RT2),
    pytest.param("NUL in reason", "POST", "/v1/windows/{w}/corrections",
                 lambda w, t: _corr(w, t, client_event_id="rt-nul-reason", reason="a\u0000b"), {}, marks=RT2),
    pytest.param("NUL in ack revision", "POST", "/v1/windows/{w}/acknowledgments",
                 lambda w, t: {"revision_id": "r\u0000", "displayed_facts": ["recorded_sleep"]}, {}, marks=RT2),
    pytest.param("NUL in feature", "POST", "/v1/capability-decisions", lambda w, t: {"feature": "a\u0000"}, {}, marks=RT2),
    pytest.param("NUL in decision id", "POST", "/v1/purchase-intents",
                 lambda w, t: {"decision_id": "d\u0000", "offer_id": "o"}, {"Idempotency-Key": "rt-mal-nul"}, marks=RT2),
    pytest.param("NUL in render subject", "POST", "/v1/render-receipts",
                 lambda w, t: {"render_id": "rt-nul-render", "surface": "night_briefing", "subject_id": "x\u0000"}, {},
                 marks=RT2),
    pytest.param("NUL in analytics event id", "POST", "/v1/analytics",
                 lambda w, t: {"event_id": "rt-nul-\u0000", "name": "briefing_viewed", "properties": {}}, {}, marks=RT2),
    pytest.param("revision beyond int4", "GET", "/v1/windows/{w}/brief?revision=2147483648", None, {}, marks=RT3),
    pytest.param("since_revision beyond int4", "GET", "/v1/windows/{w}/changes?since_revision=99999999999999999999",
                 None, {}, marks=RT3),
    pytest.param("naive dev claim start", "POST", "/v1/dev/claims",
                 lambda w, t: {"channel": "web", "external_ref": "rt-naive", "event_status": "active",
                               "plan_code": "memories", "starts_at": "2026-10-01T00:00:00"}, {}, marks=RT5),
    pytest.param("far-future dev claim", "POST", "/v1/dev/claims",
                 lambda w, t: {"channel": "web", "external_ref": "rt-far", "event_status": "active",
                               "plan_code": "memories", "starts_at": "9999-12-01T00:00:00+00:00", "days": 3650}, {},
                 marks=RT5),
]


@pytest.mark.parametrize("name,method,path,body,extra", MALFORMED,
                         ids=[c.values[0] if hasattr(c, "marks") else c[0] for c in MALFORMED])
def test_rt_malformed_input_is_rejected_not_500(lenient_client, demo, name, method, path, body, extra):
    w, t = demo["window_id"], demo["final_wake_event_id"]
    before = snapshot()
    r = lenient_client.request(method, path.replace("{w}", w), headers=PARENT | extra,
                               json=body(w, t) if body else None)
    assert r.status_code < 500, (name, r.status_code, r.text[:200])
    if r.status_code >= 400:
        assert snapshot() == before


# RT-4 (fixed): non-ASCII signatures used to raise TypeError in compare_digest.
def test_rt_non_ascii_token_signature_is_401_not_500(lenient_client, demo):
    r = lenient_client.get("/v1/me", headers=[(b"Authorization", b"Bearer pl1.mem_parent_a.\xe9\xe9")])
    assert r.status_code == 401


def test_rt_non_ascii_member_part_of_token_is_401(lenient_client, demo):
    r = lenient_client.get("/v1/me", headers=[(b"Authorization", b"Bearer pl1.\xe9\xe9.abc")])
    assert r.status_code == 401


# RT-6 (fixed): ?revision=0 used to return the current revision.
def test_rt_revision_zero_is_not_silently_the_current_revision(client, demo):
    r = client.get(f"/v1/windows/{demo['window_id']}/brief?revision=0", headers=PARENT)
    assert r.status_code in (404, 422)          # refused, never silently the current revision


def test_rt_the_workspace_route_is_a_constant(client, demo):
    before = snapshot()
    for headers in ({}, PARENT, {"Authorization": "Bearer forged"}):
        r = client.post("/v1/workspace", headers=headers)
        assert r.status_code == 200 and r.json() == {"gateway": False, "synthetic": True}
    assert snapshot() == before
