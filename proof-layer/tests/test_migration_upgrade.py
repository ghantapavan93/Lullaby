"""Migrations 0007 to 0009 as an upgrade of an existing database.

Each test builds an isolated database at revision 0006 (the schema before
renewals), loads realistic existing data, then upgrades to head:

- valid data upgrades, keeps every row, and enforces the new rules;
- a purchase already split across two cameras blocks the upgrade with a
  diagnostic naming it, and leaves the database exactly at 0006;
- a role that can't create the btree_gist extension gets a diagnostic saying
  what a DBA must run; after the DBA runs it, the same upgrade succeeds;
- 0009 keeps existing purchase evidence as it was, accepts the store billing
  statuses, and refuses to downgrade while any row uses one.

Run against the local PostgreSQL cluster; databases and the role are dropped
afterwards.
"""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from .conftest import ROOT

ADMIN = "postgresql://proof@127.0.0.1:54329/postgres"
BEFORE = "0006_purchase_to_access"
HEAD = "0010_video_evidence"
T = datetime(2026, 10, 1, tzinfo=timezone.utc)


def alembic(url: str, target: str, command: str = "upgrade") -> subprocess.CompletedProcess:
    env = os.environ | {"DATABASE_URL": url.replace("postgresql://", "postgresql+psycopg://")}
    return subprocess.run([sys.executable, "-m", "alembic", command, target], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


@pytest.fixture
def old_db():
    """A fresh database at 0006, owned by the superuser; yields its URL."""
    name = f"upgrade_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = f"postgresql://proof@127.0.0.1:54329/{name}"
    try:
        done = alembic(url, BEFORE)
        assert done.returncode == 0, done.stderr[-2000:]
        yield url
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def load_existing_data(url: str, *, split_purchase: bool = False) -> None:
    """Two families, three cameras, claims from every channel, a refund and a
    pending event, as a 0006 database would hold them."""
    claims = [
        ("c1", "hh_a", "cam_a1", "trial", "trial-cam_a1", "active", "sleep", T - timedelta(days=40), T + timedelta(days=50)),
        ("c2", "hh_a", "cam_a1", "apple", "apple-1", "active", "memories", T - timedelta(days=300), T + timedelta(days=65)),
        ("c3", "hh_a", "cam_a1", "apple", "apple-2", "active", "memories", T - timedelta(days=200), T + timedelta(days=165)),
        ("c4", "hh_a", "cam_a1", "apple", "apple-2", "revoked", "memories", T - timedelta(days=200), T + timedelta(days=165)),
        ("c5", "hh_a", "cam_a2", "activation_code", "CODE-1", "active", "milestones", T - timedelta(days=10), T + timedelta(days=355)),
        ("c6", "hh_b", "cam_b1", "google", "gpa-1", "pending", "memories", T, T + timedelta(days=365)),
        ("c7", "hh_b", "cam_b1", "simulated_purchase", "op_1", "active", "memories", T, T + timedelta(days=365)),
    ]
    if split_purchase:
        # Allowed before 0007: one camera per purchase was enforced only for active activation codes.
        claims += [("c8", "hh_a", "cam_a1", "web", "web-9", "active", "sleep", T, T + timedelta(days=365)),
                   ("c9", "hh_a", "cam_a2", "web", "web-9", "pending", "sleep", T, T + timedelta(days=365))]
    with psycopg.connect(url, autocommit=False) as db:
        for hh in ("hh_a", "hh_b"):
            db.execute("INSERT INTO households (id, name, timezone) VALUES (%s, %s, 'America/New_York')",
                       (hh, f"Upgrade family {hh} (synthetic)"))
        for cam, hh, mount in (("cam_a1", "hh_a", "floor_stand"), ("cam_a2", "hh_a", "anywhere_stand"),
                               ("cam_b1", "hh_b", "flex_stand")):
            db.execute("INSERT INTO devices (id, household_id, label, model, mount) VALUES (%s, %s, %s, 'pro', %s)",
                       (cam, hh, cam, mount))
        for c in claims:
            db.execute("INSERT INTO entitlement_claims (id, household_id, device_id, channel, external_ref, "
                       "event_status, plan_code, starts_at, ends_at, request_hash) "
                       "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'h')", c)
        db.execute("INSERT INTO purchase_refs (channel, external_ref, household_id) VALUES ('web', 'web-1', 'hh_a')")
        db.execute("INSERT INTO purchase_evidence (id, household_id, channel, external_ref, plan_code, status, "
                   "provider_version, starts_at, ends_at, request_hash) VALUES ('pev1', 'hh_a', 'web', 'web-1', "
                   "'memories', 'verified', 1, %s, %s, 'h')", (T, T + timedelta(days=365)))
        db.commit()


def snapshot(url: str) -> dict:
    with psycopg.connect(url) as db:
        return {
            "version": db.execute("SELECT version_num FROM alembic_version").fetchone()[0],
            "claims": db.execute("SELECT count(*) FROM entitlement_claims").fetchone()[0],
            "evidence": db.execute("SELECT count(*) FROM purchase_evidence").fetchone()[0],
            "constraints": {r[0]: r[1] for r in db.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'entitlement_claims'::regclass").fetchall()},
            "code_index": db.execute("SELECT count(*) FROM pg_indexes WHERE indexname = "
                                     "'uq_activation_code_one_camera'").fetchone()[0],
            "btree_gist": db.execute("SELECT count(*) FROM pg_extension WHERE extname = 'btree_gist'").fetchone()[0],
        }


def test_valid_data_upgrades_keeps_every_row_and_enforces_the_new_rules(old_db):
    load_existing_data(old_db)
    before = snapshot(old_db)
    done = alembic(old_db, "head")
    assert done.returncode == 0, done.stderr[-2000:]
    after = snapshot(old_db)
    assert after["version"] == HEAD
    assert (after["claims"], after["evidence"]) == (before["claims"], before["evidence"])
    assert "ex_claim_one_camera" in after["constraints"] and after["code_index"] == 0
    assert "ends_at" in after["constraints"]["uq_claim_event"]
    assert "NULLS NOT DISTINCT" in after["constraints"]["uq_claim_event"]
    with psycopg.connect(old_db) as db:
        # A renewal (second period of apple-1) now fits; the same purchase on another camera doesn't.
        db.execute("INSERT INTO entitlement_claims (id, household_id, device_id, channel, external_ref, event_status, "
                   "plan_code, starts_at, ends_at, request_hash) VALUES ('r1', 'hh_a', 'cam_a1', 'apple', 'apple-1', "
                   "'active', 'memories', %s, %s, 'h')", (T + timedelta(days=65), T + timedelta(days=430)))
        db.commit()
        with pytest.raises(psycopg.errors.ExclusionViolation):
            db.execute("INSERT INTO entitlement_claims (id, household_id, device_id, channel, external_ref, "
                       "event_status, plan_code, starts_at, ends_at, request_hash) VALUES ('r2', 'hh_a', 'cam_a2', "
                       "'apple', 'apple-1', 'active', 'memories', %s, %s, 'h')", (T, T + timedelta(days=1)))
        db.rollback()
        # 0009: the existing evidence is unchanged; a store billing status fits; an unknown one doesn't.
        assert db.execute("SELECT status, provider_event FROM purchase_evidence WHERE id = 'pev1'").fetchone() ==             ("verified", None)
        db.execute("INSERT INTO purchase_evidence (id, household_id, channel, external_ref, plan_code, status, "
                   "provider_version, starts_at, ends_at, request_hash, provider_event) VALUES ('pev2', 'hh_a', "
                   "'web', 'web-1', 'memories', 'billing_retry', 2, %s, %s, 'h', 'test')", (T, T + timedelta(days=60)))
        db.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO purchase_evidence (id, household_id, channel, external_ref, plan_code, status, "
                       "provider_version, starts_at, request_hash) VALUES ('pev3', 'hh_a', 'web', 'web-1', "
                       "'memories', 'lapsed', 3, %s, 'h')", (T,))


def test_downgrade_from_0009_refuses_while_a_billing_status_is_in_use(old_db):
    load_existing_data(old_db)
    assert alembic(old_db, "head").returncode == 0
    clean = alembic(old_db, "0008_claim_period_end", command="downgrade")     # nothing uses the new statuses
    assert clean.returncode == 0, clean.stderr[-2000:]
    assert alembic(old_db, "head").returncode == 0
    with psycopg.connect(old_db) as db:
        db.execute("INSERT INTO purchase_evidence (id, household_id, channel, external_ref, plan_code, status, "
                   "provider_version, starts_at, ends_at, request_hash) VALUES ('pev2', 'hh_a', 'web', 'web-1', "
                   "'memories', 'grace', 2, %s, %s, 'h')", (T, T + timedelta(days=16)))
        db.commit()
    refused = alembic(old_db, "0008_claim_period_end", command="downgrade")
    assert refused.returncode != 0 and "billing lifecycle status" in refused.stderr
    assert snapshot(old_db)["version"] == HEAD                 # evidence is append-only: it stays at 0009


def test_downgrade_from_0009_refuses_while_a_row_was_recorded_from_a_store_event(old_db):
    load_existing_data(old_db)
    assert alembic(old_db, "head").returncode == 0
    with psycopg.connect(old_db) as db:
        db.execute("INSERT INTO purchase_evidence (id, household_id, channel, external_ref, plan_code, status, "
                   "provider_version, starts_at, ends_at, request_hash, provider_event) VALUES ('pev2', 'hh_a', "
                   "'web', 'web-1', 'memories', 'verified', 2, %s, %s, 'h', 'SUBSCRIBED/INITIAL_BUY')",
                   (T, T + timedelta(days=365)))
        db.commit()
    refused = alembic(old_db, "0008_claim_period_end", command="downgrade")
    assert refused.returncode != 0 and "recorded from a store event" in refused.stderr
    assert snapshot(old_db)["version"] == HEAD


def test_a_purchase_split_across_cameras_blocks_the_upgrade_and_changes_nothing(old_db):
    load_existing_data(old_db, split_purchase=True)
    before = snapshot(old_db)
    done = alembic(old_db, "head")
    assert done.returncode != 0
    assert "more than one camera" in done.stderr and "web:web-9 on cam_a1, cam_a2" in done.stderr
    assert "No change was made" in done.stderr
    assert snapshot(old_db) == before                       # still 0006, same rows, same constraints, no extension


def test_a_role_that_cannot_create_btree_gist_gets_a_clear_diagnostic_then_succeeds_after_a_dba_installs_it():
    name, role = f"upgrade_{uuid.uuid4().hex[:10]}", f"limited_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN, autocommit=True) as admin:
        admin.execute(f"CREATE ROLE {role} LOGIN")
        admin.execute(f"CREATE DATABASE {name}")
    try:
        with psycopg.connect(f"postgresql://proof@127.0.0.1:54329/{name}", autocommit=True) as owner:
            owner.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {role}")   # tables yes, extensions no
        limited = f"postgresql://{role}@127.0.0.1:54329/{name}"
        assert alembic(limited, BEFORE).returncode == 0
        load_existing_data(limited)
        before = snapshot(limited)

        blocked = alembic(limited, "head")
        assert blocked.returncode != 0
        assert "needs the btree_gist extension" in blocked.stderr
        assert "CREATE EXTENSION btree_gist" in blocked.stderr and "No change was made" in blocked.stderr
        assert snapshot(limited) == before

        with psycopg.connect(f"postgresql://proof@127.0.0.1:54329/{name}", autocommit=True) as dba:
            dba.execute("CREATE EXTENSION btree_gist")                        # the DBA's one step
        done = alembic(limited, "head")
        assert done.returncode == 0, done.stderr[-2000:]
        assert snapshot(limited)["version"] == HEAD and snapshot(limited)["claims"] == before["claims"]
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            admin.execute(f"DROP ROLE IF EXISTS {role}")
