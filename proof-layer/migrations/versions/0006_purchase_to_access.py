"""Purchase-to-access: purchase evidence that may not yet be bound to a camera.

Until now every entitlement claim named a camera, so "paid, but not connected
to a camera" could not be represented. This migration adds:

- purchase_refs: one owner household per purchase reference (channel + ref).
  Evidence rows reference (channel, ref, household) here, so the database
  refuses evidence for a purchase that belongs to another household.
- purchase_evidence: append-only provider events about a purchase (verified,
  pending, refunded, unverified), versioned by the provider. A purchase's
  status is its highest version; two different statuses at that version are
  a conflict, never silently resolved.
- access_actions: append-only log of access commands (connect a purchase to a
  camera), idempotent per household and key.
- access_checks: append-only record of each time access was verified for a
  camera, so later events can be compared against it.

Camera binding itself is not a new concept: binding writes an ordinary
entitlement claim through claims.record, so the camera's plan stays derived
from one place.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_purchase_to_access"
down_revision = "0005_experiment_kill_switch"
branch_labels = None
depends_on = None

CHANNELS = "('web', 'apple', 'google', 'activation_code')"
APPEND_ONLY = ("purchase_evidence", "access_actions", "access_checks")


def upgrade() -> None:
    op.create_table(
        "purchase_refs",
        sa.Column("channel", sa.String(24), primary_key=True),
        sa.Column("external_ref", sa.String(120), primary_key=True),
        sa.Column("household_id", sa.String(40), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("channel", "external_ref", "household_id", name="uq_purchase_ref_owner"),
        sa.CheckConstraint(f"channel IN {CHANNELS}", name="ck_purchase_ref_channel"),
    )
    op.create_table(
        "purchase_evidence",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("external_ref", sa.String(120), nullable=False),
        sa.Column("plan_code", sa.String(40), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("provider_version", sa.Integer, nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("trace_id", sa.String(40)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["channel", "external_ref", "household_id"],
                                ["purchase_refs.channel", "purchase_refs.external_ref", "purchase_refs.household_id"],
                                name="fk_evidence_owner"),
        sa.UniqueConstraint("channel", "external_ref", "provider_version", "status", name="uq_evidence_event"),
        sa.CheckConstraint("status IN ('verified', 'pending', 'refunded', 'unverified')", name="ck_evidence_status"),
        sa.CheckConstraint("provider_version >= 1", name="ck_evidence_version"),
        sa.CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_evidence_period"),
    )
    op.create_index("ix_purchase_evidence_household", "purchase_evidence", ["household_id"])
    op.create_table(
        "access_actions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("member_id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("external_ref", sa.String(120), nullable=False),
        sa.Column("device_id", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("outcome", postgresql.JSONB, nullable=False),
        sa.Column("trace_id", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["household_id", "device_id"], ["devices.household_id", "devices.id"], name="fk_access_action_device"),
        sa.ForeignKeyConstraint(["household_id", "member_id"], ["members.household_id", "members.id"], name="fk_access_action_member"),
        sa.UniqueConstraint("household_id", "idempotency_key", name="uq_access_action_key"),
        sa.CheckConstraint("kind IN ('bind')", name="ck_access_action_kind"),
    )
    op.create_table(
        "access_checks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("device_id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("plan_code", sa.String(40), nullable=False),
        sa.Column("usable_features", postgresql.JSONB, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("state_revision", sa.Integer, nullable=False),
        sa.Column("trace_id", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["household_id", "device_id"], ["devices.household_id", "devices.id"], name="fk_access_check_device"),
        sa.CheckConstraint("kind IN ('activation', 'recheck')", name="ck_access_check_kind"),
    )
    op.create_index("ix_access_checks_device", "access_checks", ["household_id", "device_id", "created_at"])
    for table in APPEND_ONLY:
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                   f"FOR EACH ROW EXECUTE FUNCTION reject_mutation()")


def downgrade() -> None:
    for table in ("access_checks", "access_actions", "purchase_evidence", "purchase_refs"):
        op.drop_table(table)
