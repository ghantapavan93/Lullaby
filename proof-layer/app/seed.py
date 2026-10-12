"""Synthetic demonstration scenario. NOT Nanit data.

Every record below is invented for the demo and labeled synthetic. Product
rules (plans, mounts, prices) come from public Nanit documentation; the
child, the caregivers and the night are fiction.

Household ``hh_demo`` (America/New_York)
  Parent A (owner, can bill), Caregiver B (caregiver), one child, two cameras:
  the nursery Pro on a Floor Stand (Sleep trial, from a trial claim) and a
  playroom Nanit Essential on its Anywhere Stand (no plan).

  Control night 2026-10-08: fully observed; must never change.

  Night 2026-10-09, window 19:00 -> 07:00 (720 min), arriving as four camera
  syncs so the briefing reaches revision 4:
    r1  observation 19:00-23:05, sleep 19:00-23:00
    r2  observation 23:05-03:40, sleep 23:30-03:40
    r3  observation 04:20-07:00, sleep 04:20-06:50   (camera reconnected)
    r4  camera reports it was unavailable 03:40-04:20 (Wi-Fi lost)
  Revision 4: recorded sleep 10h 40m, final wake 06:50, coverage 94.4%,
  40 minutes unobserved.

Household ``hh_other``: a second family used only to prove isolation.
"""
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from psycopg import errors
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from . import claims, clock, provider
from .db import ProviderSessionLocal
from .engine import NewEvidence, ingest
from .models import Child, Device, Household, Member
from .security import lock_household

TZ = "America/New_York"
NY = ZoneInfo(TZ)
HOUSEHOLD, OTHER = "hh_demo", "hh_other"
PARENT_A, CAREGIVER_B, OTHER_OWNER = "mem_parent_a", "mem_caregiver_b", "mem_other_owner"
CHILD, OTHER_CHILD = "child_demo", "child_other"
CAMERA, OTHER_CAMERA = "dev_cam_demo", "dev_cam_other"
PLAYROOM = "dev_cam_playroom"
NIGHT, CONTROL_NIGHT = date(2026, 10, 9), date(2026, 10, 8)
APP_TABLES = ("video_acknowledgments, video_annotations, video_observations, video_analysis_runs, video_assets, "
              "access_checks, access_actions, purchase_evidence, purchase_refs, "
              "analytics_events, authorization_log, intent_transitions, acknowledgments, ack_heads, "
              "purchase_intents, outbox, notifications, render_receipts, experiment_assignments, "
              "capability_decisions, experiment_controls, briefing_revisions, briefing_windows, evidence_events, "
              "entitlement_claims, devices, children, members, households")
# The reset's lock order. Every authenticated request reads `members` first, so
# the reset takes it first: once it holds it, no request is half-way through and
# the other locks are uncontended. Taking it last (the TRUNCATE order) let a
# request that held `members` wait on a table the reset already held, a cycle
# that only the lock timeout broke; under steady polling every retry could lose
# (a flaky test, 2026-10-11).
LOCK_FIRST = ("members", "households")
LOCK_ORDER = ", ".join(LOCK_FIRST + tuple(t for t in (x.strip() for x in APP_TABLES.split(","))
                                          if t not in LOCK_FIRST))


def at(day: int, hhmm: str, month: int = 10) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, month, day, hour, minute, tzinfo=NY)


def obs(key: str, start: datetime, end: datetime, usable: bool = True, **extra: Any) -> NewEvidence:
    return NewEvidence(key, "camera_availability", start, end, {"usable": usable, "synthetic": True, **extra})


def sleep(key: str, start: datetime, end: datetime) -> NewEvidence:
    return NewEvidence(key, "sleep_interval", start, end, {"synthetic": True})


def reset(db: Session) -> None:
    # Every lock first, with a wait shorter than PostgreSQL's deadlock check
    # (1 s): the reset queues for the locks (which stops new readers jumping
    # ahead), and if a cycle forms it gives up first, so no page request is
    # ever chosen as a deadlock victim. load_fresh() retries.
    db.execute(text("SET LOCAL lock_timeout = '500ms'"))
    db.execute(text(f"LOCK TABLE {LOCK_ORDER} IN ACCESS EXCLUSIVE MODE"))
    db.execute(text(f"TRUNCATE {APP_TABLES} RESTART IDENTITY CASCADE"))
    db.execute(text("UPDATE commercial_config SET catalog_version='cat-2026-10-public', "
                    "policy_version='pol-2026-10-public', updated_at=now() WHERE id=1"))
    with ProviderSessionLocal() as pdb, pdb.begin():
        pdb.execute(text("TRUNCATE provider_operations, provider_scheduled_behaviors RESTART IDENTITY"))


def camera_sync(db: Session, household: str, child: str, device: str, items: list[NewEvidence]) -> dict:
    lock_household(db, household)
    return ingest(db, household_id=household, child_id=child, timezone=TZ, source="CAMERA",
                  actor_member_id=None, device_id=device, items=items)


def load(db: Session) -> dict[str, Any]:
    """Caller owns the transaction."""
    reset(db)
    db.add_all([Household(id=HOUSEHOLD, name="Demo household (synthetic)", timezone=TZ),
                Household(id=OTHER, name="Isolation household (synthetic)", timezone=TZ)])
    db.flush()
    db.add_all([
        Member(id=PARENT_A, household_id=HOUSEHOLD, display_name="Parent A", role="owner", can_bill=True),
        Member(id=CAREGIVER_B, household_id=HOUSEHOLD, display_name="Caregiver B", role="caregiver"),
        Member(id=OTHER_OWNER, household_id=OTHER, display_name="Other parent", role="owner", can_bill=True),
        Child(id=CHILD, household_id=HOUSEHOLD, display_name="Baby R (synthetic)"),
        Child(id=OTHER_CHILD, household_id=OTHER, display_name="Baby O (synthetic)"),
        Device(id=CAMERA, household_id=HOUSEHOLD, label="Nursery camera", model="pro", mount="floor_stand",
               is_primary=True),
        Device(id=PLAYROOM, household_id=HOUSEHOLD, label="Playroom camera", model="essential",
               mount="anywhere_stand"),
        Device(id=OTHER_CAMERA, household_id=OTHER, label="Nursery camera", model="pro", mount="flex_stand",
               is_primary=True),
    ])
    db.flush()
    # Plans attach to cameras and come from claims: each Pro camera starts on the
    # 3-month Sleep trial; the playroom Essential has no plan.
    for household_id, device_id in ((HOUSEHOLD, CAMERA), (OTHER, OTHER_CAMERA)):
        # Relative to today, so the demo camera is always mid-trial (a fixed
        # date made it lapse on 2026-11-30; critique 2026-10-11).
        trial_start = clock.now().astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=40)
        claims.record(db, db.get(Household, household_id), db.get(Device, device_id), channel="trial",
                      external_ref=f"trial-{device_id}", event_status="active", plan_code="sleep",
                      starts_at=trial_start, ends_at=trial_start + timedelta(days=90),
                      catalog_version="cat-2026-10-public")

    # Five earlier nights (Oct 3-7) so the week view has history. Each is fully
    # observed with one or two wake-ups; one (Oct 5) has a short camera gap.
    earlier = {3: [("19:15", "01:10"), ("01:30", "06:40")], 4: [("19:30", "02:00"), ("02:20", "06:50")],
               5: [("19:05", "23:40"), ("00:10", "06:30")], 6: [("19:40", "06:55")],
               7: [("19:10", "00:30"), ("01:00", "03:15"), ("03:35", "06:45")]}
    for day, segments in earlier.items():
        items = [obs(f"cam-10{day:02d}-obs", at(day, "19:00"), at(day + 1, "07:00"))] if day != 5 else [
            obs(f"cam-10{day:02d}-obs-a", at(day, "19:00"), at(day, "23:50")),
            obs(f"cam-10{day:02d}-obs-b", at(day, "23:58"), at(day + 1, "07:00"))]
        for i, (a, b) in enumerate(segments):
            end_day = day if b > a and b >= "12:00" else day + 1
            start_day = day if a >= "12:00" else day + 1
            items.append(sleep(f"cam-10{day:02d}-sleep-{i}", at(start_day, a), at(end_day, b)))
        camera_sync(db, HOUSEHOLD, CHILD, CAMERA, items)

    # Control night (fully observed), one sync.
    camera_sync(db, HOUSEHOLD, CHILD, CAMERA, [
        obs("cam-1008-obs", at(8, "19:00"), at(9, "07:00")),
        sleep("cam-1008-sleep", at(8, "19:20"), at(9, "06:45")),
    ])
    # The night that matters, in four syncs.
    camera_sync(db, HOUSEHOLD, CHILD, CAMERA, [
        obs("cam-1009-obs-1", at(9, "19:00"), at(9, "23:05")),
        sleep("cam-1009-sleep-1", at(9, "19:00"), at(9, "23:00")),
    ])
    camera_sync(db, HOUSEHOLD, CHILD, CAMERA, [
        obs("cam-1009-obs-2", at(9, "23:05"), at(10, "03:40")),
        sleep("cam-1009-sleep-2", at(9, "23:30"), at(10, "03:40")),
    ])
    sync3 = camera_sync(db, HOUSEHOLD, CHILD, CAMERA, [
        obs("cam-1009-obs-3", at(10, "04:20"), at(10, "07:00")),
        sleep("cam-1009-sleep-3", at(10, "04:20"), at(10, "06:50")),
    ])
    sync4 = camera_sync(db, HOUSEHOLD, CHILD, CAMERA, [
        obs("cam-1009-offline", at(10, "03:40"), at(10, "04:20"), usable=False, reason="wifi_lost"),
    ])
    # The other household has its own night, for isolation tests.
    camera_sync(db, OTHER, OTHER_CHILD, OTHER_CAMERA, [
        obs("cam-other-obs", at(9, "19:00"), at(10, "07:00")),
        sleep("cam-other-sleep", at(9, "19:30"), at(10, "06:30")),
    ])
    return {
        "household_id": HOUSEHOLD, "other_household_id": OTHER,
        "members": {"parent_a": PARENT_A, "caregiver_b": CAREGIVER_B, "other_owner": OTHER_OWNER},
        "child_id": CHILD, "night": NIGHT.isoformat(), "control_night": CONTROL_NIGHT.isoformat(),
        "window_id": sync4["revisions"][0]["window_id"],
        "revision": sync4["revisions"][0]["revision"],
        "final_wake_event_id": sync3["created"][1],
        "devices": {"nursery": CAMERA, "playroom": PLAYROOM},
        "synthetic": True,
    }


def load_fresh(attempts: int = 60) -> dict[str, Any]:
    """Reset and reload in its own transaction. A page still open on the demo
    keeps reading and writing (its poll, the authorization log). TRUNCATE used
    to take the tables' locks one by one while those requests held others, and
    PostgreSQL broke the deadlock by failing one side: a 500 from the scenario
    endpoint or from the page (found by visual QA on 2026-10-11). Now reset()
    takes every lock up front with a short lock timeout and this retries.
    Seeding outside the demo endpoints (tests, scripts) still calls load()."""
    from .db import transaction
    for attempt in range(1, attempts + 1):
        try:
            with transaction() as db:
                return load(db)
        except OperationalError as exc:
            if attempt == attempts or not isinstance(exc.orig, (errors.DeadlockDetected, errors.LockNotAvailable)):
                raise
            time.sleep(0.1)
    raise RuntimeError("unreachable")


def main() -> None:
    from .db import transaction
    provider.create_schema()
    with transaction() as db:
        result = load(db)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
