"""Attacks that HELD. These pass on the reviewed code and guard the behaviour the
red-team review could not break (beyond what the existing suite already covers)."""
import threading
import time
from datetime import date, datetime, timezone

from sqlalchemy import select, text

from app import intervals as iv, provider, seed, worker
from app.db import ProviderSessionLocal, SessionLocal, engine, transaction
from app.engine import current_revision, window_bounds
from app.models import Device
from app.provider import ScheduledBehavior
from app.security import issue_token
from app.seed import at, obs, sleep

from ..conftest import CAREGIVER, OTHER, PARENT
from ._helpers import ack, brief, buy, correct, decide, event_id, window_for


def test_forged_or_mismatched_tokens_are_rejected(client, demo):
    w = demo["window_id"]
    good = issue_token(seed.PARENT_A)
    other_sig = issue_token(seed.OTHER_OWNER).split(".")[2]
    for token in (good[:-1] + ("0" if good[-1] != "0" else "1"), f"pl1.{seed.PARENT_A}.{other_sig}",
                  good.replace("pl1", "pl2", 1), issue_token("mem_nobody"), "garbage"):
        r = client.get(f"/v1/windows/{w}/brief", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, (token, r.status_code)


def test_dev_routes_stay_inside_the_callers_household(client, demo):
    d = decide(client, "nextnap")
    intent = buy(client, d, "held-dev-0001").json()
    provider.schedule(seed.HOUSEHOLD, "pending")
    worker.run_once("held")
    r = client.post("/v1/dev/provider/resolve", headers=OTHER,
                    json={"operation_key": intent["provider_operation_key"], "status": "SUCCEEDED"})
    assert r.status_code == 404
    assert client.post("/v1/dev/mount", headers=OTHER, json={"mount": "flex_stand"}).status_code == 200
    with SessionLocal() as db:
        assert db.get(Device, seed.CAMERA).mount == "floor_stand"
    client.post("/v1/dev/provider/next", headers=OTHER, json={"behavior": "decline"})
    with ProviderSessionLocal() as pdb:
        refs = {b.merchant_reference for b in pdb.execute(select(ScheduledBehavior)
                                                          .where(ScheduledBehavior.consumed.is_(False))).scalars()}
    assert refs == {seed.OTHER}


def test_render_receipt_for_foreign_subject_is_not_found(client, demo):
    rev = brief(client, demo["window_id"])["revision_id"]
    r = client.post("/v1/render-receipts", headers=OTHER,
                    json={"render_id": "held-foreign-01", "surface": "night_briefing", "subject_id": rev})
    assert r.status_code == 404


def test_revocation_committed_while_waiting_for_the_lock_stops_the_write(client, demo):
    rid = brief(client, demo["window_id"])["revision_id"]
    result = {}
    with engine.connect() as conn:
        tx = conn.begin()
        conn.execute(text("SELECT id FROM households WHERE id = :h FOR UPDATE"), {"h": seed.HOUSEHOLD})
        t = threading.Thread(target=lambda: result.setdefault("r", ack(client, demo["window_id"], rid, CAREGIVER)))
        t.start()
        time.sleep(0.5)                                       # request authenticated, now blocked on the lock
        conn.execute(text("UPDATE members SET revoked_at = now() WHERE id = :m"), {"m": seed.CAREGIVER_B})
        tx.commit()
    t.join(10)
    assert result["r"].status_code == 403 and result["r"].json()["code"] == "ACCESS_REVOKED"


def test_correction_edge_cases(client, demo):
    w = demo["window_id"]
    wake = demo["final_wake_event_id"]
    # zero-length and end-before-start
    assert correct(client, w, target=wake, expected=4, end="2026-10-10T04:20:00-04:00").status_code == 422
    # only sleep records are correctable
    assert correct(client, w, target=event_id("cam-1009-offline"), expected=4,
                   end="2026-10-10T04:00:00-04:00").status_code == 422
    # another window's record cannot be corrected through this window
    assert correct(client, w, target=event_id("cam-1008-sleep"), expected=4,
                   end="2026-10-09T06:00:00-04:00").status_code == 422
    # exactly at the window edge, corrected_start only
    first = correct(client, w, target=event_id("cam-1009-sleep-1"), expected=4,
                    start="2026-10-09T19:00:00-04:00", key="held-edge-0001")
    assert first.status_code == 200 and first.json()["revision"] == 5
    second = correct(client, w, target=wake, expected=5, end="2026-10-10T07:00:00-04:00")
    assert second.status_code == 200 and second.json()["revision"] == 6
    # replay of the first after a newer revision exists: original result, no new revision
    again = correct(client, w, target=event_id("cam-1009-sleep-1"), expected=4,
                    start="2026-10-09T19:00:00-04:00", key="held-edge-0001")
    assert again.status_code == 200 and again.json()["replayed"] and again.json()["revision"] == 5
    assert brief(client, w)["revision"] == 6
    # superseded records cannot be corrected again
    r = correct(client, w, target=wake, expected=6, end="2026-10-10T06:00:00-04:00")
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_CORRECTED"
    # a correction of a correction is allowed
    r = correct(client, w, target=second.json()["correction_event_id"], expected=6,
                end="2026-10-10T06:40:00-04:00")
    assert r.status_code == 200 and brief(client, w)["facts"]["final_wake"]["at"].startswith("2026-10-10T06:40")


def test_ack_with_another_windows_revision_is_rejected(client, demo):
    control = window_for(date(2026, 10, 8))
    rev_control = brief(client, control.id)["revision_id"]
    assert ack(client, demo["window_id"], rev_control, CAREGIVER).status_code == 404


def test_purchase_against_a_decision_made_before_an_upgrade_is_stale(client, demo):
    d = decide(client, "nextnap")
    assert buy(client, d, "held-upgrade-01").status_code == 201
    worker.run_once("held")
    r = buy(client, d, "held-upgrade-02")
    assert r.status_code == 409 and r.json()["code"] == "STALE_DECISION"


def test_daylight_saving_windows_through_ingestion(demo):
    for evening, minutes in ((date(2026, 10, 31), 780), (date(2026, 3, 7), 660)):
        start, end = window_bounds(evening, seed.TZ)
        with transaction() as db:
            out = seed.camera_sync(db, seed.HOUSEHOLD, seed.CHILD, seed.CAMERA, [
                obs(f"held-dst-obs-{evening}", start, end),
                sleep(f"held-dst-sleep-{evening}", start, end)])
        w = window_for(evening)
        with SessionLocal() as db:
            facts = current_revision(db, w).facts
        assert facts["window"]["elapsed_minutes"] == minutes
        assert facts["observation_coverage"]["pct"] == 100.0
        assert facts["recorded_sleep"]["minutes"] == minutes
        assert len(out["revisions"]) == 1


def test_interval_edges():
    u = timezone.utc
    a, b, c = (datetime(2026, 1, 1, h, tzinfo=u) for h in (1, 2, 3))
    assert iv.union([(a, b), (b, c)]) == [(a, c)]                 # touching merges
    assert iv.union([(a, a)]) == []                               # zero length vanishes
    assert iv.clip([(a, b)], (b, c)) == []                        # touching the edge is outside
    assert iv.minutes([(a, c), (a, c), (b, c)]) == 120            # duplicates never add
