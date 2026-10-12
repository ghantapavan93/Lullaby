"""one live upgrade per household; analytics event ids scoped per household

Critic findings F-01/F-02: two simultaneous upgrades could both be charged,
and a slower, cheaper one could overwrite the plan. At most one purchase
intent per household may be in flight (PREPARED, SUBMITTED or UNKNOWN).

Critic finding F-10: analytics event ids were global, so one household's id
silently swallowed another's event and leaked its existence.

Revision ID: 0002_live_upgrade
Revises: 7225b6115847
"""
from alembic import op

revision = "0002_live_upgrade"
down_revision = "7225b6115847"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE UNIQUE INDEX uq_intents_one_live_upgrade ON purchase_intents (household_id) "
               "WHERE state IN ('PREPARED', 'SUBMITTED', 'UNKNOWN')")
    op.execute("DELETE FROM analytics_events WHERE household_id IS NULL")
    op.execute("ALTER TABLE analytics_events DROP CONSTRAINT analytics_events_pkey")
    op.execute("ALTER TABLE analytics_events ALTER COLUMN household_id SET NOT NULL")
    op.execute("ALTER TABLE analytics_events ADD PRIMARY KEY (household_id, event_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE analytics_events DROP CONSTRAINT analytics_events_pkey")
    op.execute("ALTER TABLE analytics_events ALTER COLUMN household_id DROP NOT NULL")
    op.execute("ALTER TABLE analytics_events ADD PRIMARY KEY (event_id)")
    op.execute("DROP INDEX uq_intents_one_live_upgrade")
