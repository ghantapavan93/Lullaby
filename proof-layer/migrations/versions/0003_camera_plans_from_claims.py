"""plans per camera, derived from an append-only ledger of channel claims

Research (2026-10-10) found that Nanit attaches plans to each camera and
grants them through several channels (Apple, Google Play, nanit.com,
activation codes). The prototype had one subscription row per household and
checked the mount of any camera in it. This migration:

- adds a label and a primary flag to devices;
- replaces `subscriptions` with `entitlement_claims`, one row per channel
  event, idempotent per (channel, external_ref, event_status), append-only;
- allows an activation code to be active on one camera only;
- binds capability decisions and purchase intents to a camera, and allows one
  upgrade in flight per camera instead of per household.

Revision ID: 0003_camera_plans
Revises: 0002_live_upgrade
"""
from alembic import op

revision = "0003_camera_plans"
down_revision = "0002_live_upgrade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE devices ADD COLUMN label varchar(60) NOT NULL DEFAULT 'Camera'")
    op.execute("ALTER TABLE devices ADD COLUMN is_primary boolean NOT NULL DEFAULT false")
    op.execute("""UPDATE devices d SET is_primary = true WHERE d.id = (
                    SELECT min(id) FROM devices x WHERE x.household_id = d.household_id)""")
    op.execute("CREATE UNIQUE INDEX uq_devices_one_primary ON devices (household_id) WHERE is_primary")

    op.execute("""
        CREATE TABLE entitlement_claims (
            id varchar(40) PRIMARY KEY,
            household_id varchar(40) NOT NULL,
            device_id varchar(40) NOT NULL,
            channel varchar(24) NOT NULL,
            external_ref varchar(120) NOT NULL,
            event_status varchar(12) NOT NULL,
            plan_code varchar(40) NOT NULL,
            starts_at timestamptz NOT NULL,
            ends_at timestamptz,
            request_hash varchar(64) NOT NULL,
            trace_id varchar(40),
            received_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_claim_event UNIQUE (channel, external_ref, event_status),
            CONSTRAINT fk_claim_device FOREIGN KEY (household_id, device_id)
                REFERENCES devices (household_id, id),
            CONSTRAINT ck_claim_status CHECK (event_status IN ('active', 'pending', 'revoked')),
            CONSTRAINT ck_claim_channel CHECK (channel IN
                ('seed', 'trial', 'apple', 'google', 'web', 'activation_code', 'simulated_purchase')),
            CONSTRAINT ck_claim_period CHECK (ends_at IS NULL OR ends_at > starts_at)
        )""")
    op.execute("CREATE INDEX ix_claims_device ON entitlement_claims (household_id, device_id)")
    op.execute("""CREATE UNIQUE INDEX uq_activation_code_one_camera ON entitlement_claims (external_ref)
                  WHERE channel = 'activation_code' AND event_status = 'active'""")
    op.execute("CREATE TRIGGER entitlement_claims_append_only BEFORE UPDATE OR DELETE ON entitlement_claims "
               "FOR EACH ROW EXECUTE FUNCTION reject_mutation()")
    op.execute("""
        INSERT INTO entitlement_claims (id, household_id, device_id, channel, external_ref, event_status,
                                        plan_code, starts_at, request_hash)
        SELECT 'clm_seed_' || d.id, d.household_id, d.id, 'seed', 'seed-' || d.id, 'active', s.plan_code,
               now(), 'migrated'
        FROM devices d JOIN subscriptions s ON s.household_id = d.household_id
        WHERE s.plan_code <> 'none'""")
    op.execute("DROP TABLE subscriptions")

    for table in ("capability_decisions", "purchase_intents"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN device_id varchar(40)")
        op.execute(f"""UPDATE {table} t SET device_id = (SELECT id FROM devices d
                       WHERE d.household_id = t.household_id AND d.is_primary)""")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN device_id SET NOT NULL")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_device FOREIGN KEY (household_id, device_id) "
                   "REFERENCES devices (household_id, id)")
    op.execute("DROP INDEX uq_intents_one_live_upgrade")
    op.execute("CREATE UNIQUE INDEX uq_intents_one_live_upgrade ON purchase_intents (household_id, device_id) "
               "WHERE state IN ('PREPARED', 'SUBMITTED', 'UNKNOWN')")


def downgrade() -> None:
    raise NotImplementedError("one-way migration: claims cannot be folded back into one plan per household")
