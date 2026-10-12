"""A claim's period includes its end: a store that extends a period (same
start, later end) is a new fact, not a conflict.

Found by the 2026-10-11 critique: an extension was dropped, and the camera
read "ended" on the old date while the store said the plan ran on.
NULLS NOT DISTINCT keeps open-ended periods unique too (PostgreSQL 15+).
"""
from alembic import op

revision = "0008_claim_period_end"
down_revision = "0007_claim_periods"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE entitlement_claims DROP CONSTRAINT uq_claim_event")
    op.execute("ALTER TABLE entitlement_claims ADD CONSTRAINT uq_claim_event "
               "UNIQUE NULLS NOT DISTINCT (channel, external_ref, event_status, starts_at, ends_at)")


def downgrade() -> None:
    op.execute("ALTER TABLE entitlement_claims DROP CONSTRAINT uq_claim_event")
    op.execute("ALTER TABLE entitlement_claims ADD CONSTRAINT uq_claim_event "
               "UNIQUE (channel, external_ref, event_status, starts_at)")
