"""Red-team reproductions: authorization surfaces, growth/analytics, dev routes, UI honesty.

Every test here FAILS on the reviewed code and should pass once the defect is fixed.
"""
import os
import re
import subprocess
import sys

import pytest
from sqlalchemy import select

from app import seed, worker
from app.db import SessionLocal
from app.models import AnalyticsEvent

from ..conftest import CAREGIVER, OTHER, PARENT, ROOT
from ._helpers import brief, buy, decide, lenient_client  # noqa: F401  (fixture)


# F-05 -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("properties", [
    {"revision": {"note": "Baby R vomited twice after the 2am feed, gave 2.5ml acetaminophen"}},
    {"revision": ["Baby R vomited twice after the 2am feed", "gave 2.5ml acetaminophen at 02:10"]},
    {"status": "Baby R had a fever; gave meds at 2am"},
], ids=["nested-object", "list-of-strings", "short-free-text"])
def test_analytics_rejects_free_text_in_any_shape(client, demo, properties):
    """growth.py claims free text and care notes are 'rejected at the door'. Only
    top-level strings longer than 64 characters are checked; nested objects, lists and
    short sentences are stored verbatim in analytics_events."""
    r = client.post("/v1/analytics", headers=PARENT,
                    json={"event_id": f"critic-pii-{abs(hash(str(properties)))}", "name": "briefing_viewed",
                          "properties": properties})
    assert r.status_code == 422, r.json()


# F-10 -----------------------------------------------------------------------------------------
def test_clients_cannot_forge_server_analytics_events(client, demo):
    """purchase_confirmed is emitted by the server after a provider SUCCEEDED; any
    signed-in member (even a caregiver) can post it from the browser."""
    r = client.post("/v1/analytics", headers=CAREGIVER, json={
        "event_id": "critic-forged-0001", "name": "purchase_confirmed",
        "properties": {"intent_id": "pi_forged", "plan_code": "milestones"}})
    assert r.status_code in (403, 422), r.json()


def test_analytics_event_ids_do_not_collide_across_households(client, demo):
    """event_id is a global primary key. hh_other's event is silently dropped
    (created=false) when hh_demo used the same id first; the flag is also an
    existence oracle for another household's event ids."""
    body = {"event_id": "critic-shared-evt-01", "name": "briefing_viewed", "properties": {"revision": 4}}
    assert client.post("/v1/analytics", headers=PARENT, json=body).json()["created"] is True
    other = client.post("/v1/analytics", headers=OTHER, json=body)
    with SessionLocal() as db:
        stored = db.execute(select(AnalyticsEvent.household_id).where(
            AnalyticsEvent.household_id == seed.OTHER)).all()
    assert other.json()["created"] is True and stored, other.json()


# F-11 -----------------------------------------------------------------------------------------
def test_variant_is_never_recorded_without_its_experiment(client, demo):
    """Omitting experiment_key skips the assignment check, so any variant string is
    stored on the exposure receipt that the experiment analysis reads."""
    d = decide(client, "nextnap")
    r = client.post("/v1/render-receipts", headers=PARENT, json={
        "render_id": "critic-render-variant-1", "surface": "feature_availability",
        "subject_id": d["decision_id"], "variant": "tampered_variant"})
    assert r.status_code == 422, r.json()


def test_render_id_reuse_for_a_different_subject_conflicts(client, demo):
    """A render id is idempotent per household, but a second receipt with the same id
    for a DIFFERENT subject is silently answered with the first receipt (created=false):
    the second exposure is lost with no error."""
    rev4 = brief(client, demo["window_id"])["revision_id"]
    control = client.get("/v1/me", headers=PARENT).json()["windows"]
    other_window = next(w["window_id"] for w in control if w["local_date"] == "2026-10-08")
    rev_control = brief(client, other_window)["revision_id"]
    body = {"render_id": "critic-render-reuse-1", "surface": "night_briefing"}
    assert client.post("/v1/render-receipts", headers=PARENT, json=body | {"subject_id": rev4}).status_code == 200
    r = client.post("/v1/render-receipts", headers=PARENT, json=body | {"subject_id": rev_control})
    assert r.status_code == 409, r.json()


# F-13 -----------------------------------------------------------------------------------------
def test_caregiver_cannot_read_purchase_data_through_xray(client, demo):
    """POLICY denies purchase.read to caregivers, but /v1/xray (xray.read includes
    caregiver) returns the household's intent transitions, provider ledger amounts and
    purchase analytics."""
    d = decide(client, "nextnap")
    intent = buy(client, d, "critic-xray-0001").json()
    worker.run_once("critic")
    assert client.get(f"/v1/purchase-intents/{intent['intent_id']}", headers=CAREGIVER).status_code == 403
    x = client.get("/v1/xray", headers=CAREGIVER)
    if x.status_code == 403:
        return
    body = x.json()
    assert not body["intent_transitions"] and not body["provider_ledger"], \
        "caregiver sees purchase transitions and provider charges"


# F-14 -----------------------------------------------------------------------------------------
def test_dev_routes_and_session_secret_are_not_on_by_default():
    """Spec §10: scenario controls are disabled outside the local demo configuration.
    With no environment set, PROOF_LAYER_DEV defaults to on (unauthenticated
    /v1/dev/sessions mints a token for any member of any household, /v1/dev/reset
    wipes the database) and the HMAC secret is a published constant."""
    env = {k: v for k, v in os.environ.items() if k not in ("PROOF_LAYER_DEV", "PROOF_LAYER_SECRET")}
    probe = ("import app.main as m, app.security as s; "
             "print(m.DEV, s.SECRET == b'dev-only-not-a-secret')")
    out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True, text=True)
    assert out.returncode != 0 or out.stdout.split() == ["False", "False"], out.stdout + out.stderr


def test_dev_worker_requires_a_session(client, demo):
    """/v1/dev/worker takes no credentials and drives every household's outbox
    (including the crash_after_provider kill switch)."""
    r = client.post("/v1/dev/worker", json={})
    assert r.status_code in (401, 403), r.json()


# F-15 -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("call", ["feature_41_chars", "idempotency_key_200_chars", "mount_41_chars",
                                  "unknown_provider_behaviour"])
def test_oversized_or_unknown_inputs_are_rejected_not_500(lenient_client, demo, call):
    """Spec §10: validate input shapes before side effects. These reach VARCHAR limits
    or a ValueError and return 500 (the decision one after an authorization record and
    household lock were taken)."""
    c = lenient_client
    if call == "feature_41_chars":
        r = c.post("/v1/capability-decisions", headers=PARENT, json={"feature": "f" * 41})
    elif call == "idempotency_key_200_chars":
        d = c.post("/v1/capability-decisions", headers=PARENT, json={"feature": "nextnap"}).json()
        r = c.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": "k" * 200},
                   json={"decision_id": d["decision_id"], "offer_id": d["offer"]["offer_id"]})
    elif call == "mount_41_chars":
        r = c.post("/v1/dev/mount", headers=PARENT, json={"mount": "m" * 41})
    else:
        r = c.post("/v1/dev/provider/next", headers=PARENT, json={"behavior": "explode"})
    assert r.status_code < 500, (call, r.status_code, r.text[:200])


# F-16 -----------------------------------------------------------------------------------------
def test_acknowledged_facts_are_all_displayed_on_the_briefing():
    """The briefing acknowledges FACTS (five keys incl. sleep_onset) and its copy says
    'you viewed this version of the five facts above', but briefingScreen renders only
    four tiles; 'Fell asleep' is never shown there, yet its fingerprint is acknowledged."""
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    facts = set(re.findall(r'"(\w+)"', re.search(r"const FACTS = \[(.*?)\];", js).group(1)))
    screen = js[js.index("function briefingScreen"):js.index("// ------------------------------------------------------------------ what changed")]
    shown = set(re.findall(r'tile\("(\w+)"', screen))
    assert facts <= shown, f"acknowledged but not displayed: {sorted(facts - shown)}"
