"""Renewals: a purchase can hold more than one period on its camera.

A customer-reported symptom ("my plan renewed but the app says I need to
subscribe") led us to check our own model, and it could not even represent a
renewal.
Our model could not even represent a renewal: one active claim per purchase
(uq_claim_event on channel, external_ref, event_status), so a renewed period
had nowhere to go and the camera lapsed while the store said active.

- uq_claim_event now includes starts_at: one event per purchase, status and
  period. A re-delivered event is still a no-op.
- The one-camera rule moves from a partial unique index that covered only
  activation codes to an exclusion constraint that covers every channel: two
  claims for the same purchase can never name different cameras, whatever
  their periods (btree_gist).
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_claim_periods"
down_revision = "0006_purchase_to_access"
branch_labels = None
depends_on = None


class UpgradeBlocked(RuntimeError):
    """A prerequisite isn't met. Nothing was changed: the migration runs in one
    transaction and stops before its first statement."""


def _preflight() -> None:
    bind = op.get_bind()
    # 1. Existing data must satisfy the new rule: one camera per purchase.
    split = bind.execute(sa.text("""
        SELECT channel, external_ref, string_agg(DISTINCT device_id, ', ' ORDER BY device_id) AS cameras
        FROM entitlement_claims GROUP BY channel, external_ref
        HAVING count(DISTINCT device_id) > 1 ORDER BY channel, external_ref LIMIT 20""")).all()
    if split:
        listing = "; ".join(f"{r.channel}:{r.external_ref} on {r.cameras}" for r in split)
        raise UpgradeBlocked(
            "0007_claim_periods: some purchases already have claims on more than one camera, which the new "
            f"one-camera-per-purchase constraint forbids: {listing}. Decide which camera each belongs to (a support "
            "decision), record that, then rerun the upgrade. No change was made.")
    # 2. The exclusion constraint needs btree_gist, and this role must be able to create it.
    if bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")).first():
        return
    available = bind.execute(sa.text(
        "SELECT bool_or(trusted) FROM pg_available_extension_versions WHERE name = 'btree_gist'")).scalar()
    if available is None:
        raise UpgradeBlocked(
            "0007_claim_periods needs the btree_gist extension, which isn't installed on this PostgreSQL server "
            "(it ships in the contrib package). Install it, then rerun the upgrade. No change was made.")
    can_create = bind.execute(sa.text(
        "SELECT rolsuper OR (:trusted AND has_database_privilege(current_database(), 'CREATE')) "
        "FROM pg_roles WHERE rolname = current_user"), {"trusted": bool(available)}).scalar()
    if not can_create:
        db = bind.execute(sa.text("SELECT current_database()")).scalar()
        raise UpgradeBlocked(
            f"0007_claim_periods needs the btree_gist extension in database {db}, and role "
            f"{bind.execute(sa.text('SELECT current_user')).scalar()} can't create it. Ask a database administrator "
            "to run: CREATE EXTENSION btree_gist; then rerun the upgrade. No change was made.")


def upgrade() -> None:
    _preflight()
    op.execute("ALTER TABLE entitlement_claims DROP CONSTRAINT uq_claim_event")
    op.execute("ALTER TABLE entitlement_claims ADD CONSTRAINT uq_claim_event "
               "UNIQUE (channel, external_ref, event_status, starts_at)")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("DROP INDEX IF EXISTS uq_activation_code_one_camera")
    op.execute("ALTER TABLE entitlement_claims ADD CONSTRAINT ex_claim_one_camera "
               "EXCLUDE USING gist (channel WITH =, external_ref WITH =, device_id WITH <>)")


def downgrade() -> None:
    # Fails if renewals were recorded: a second period of one purchase has no
    # place in the old schema.
    op.execute("ALTER TABLE entitlement_claims DROP CONSTRAINT ex_claim_one_camera")
    op.execute("CREATE UNIQUE INDEX uq_activation_code_one_camera ON entitlement_claims (external_ref) "
               "WHERE channel = 'activation_code' AND event_status = 'active'")
    op.execute("ALTER TABLE entitlement_claims DROP CONSTRAINT uq_claim_event")
    op.execute("ALTER TABLE entitlement_claims ADD CONSTRAINT uq_claim_event "
               "UNIQUE (channel, external_ref, event_status)")
