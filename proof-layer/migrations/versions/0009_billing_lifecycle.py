"""Store billing lifecycle: grace, billing retry, pause and expiry.

Purchase evidence could say verified, pending, refunded or unverified. A store
also reports a grace period (access continues until the grace end), billing
retry or account hold (no access; the purchase stays on its camera), a pause
(no access until it resumes) and an expiry. See app/provider_lifecycle.py and
docs/PROVIDER_LIFECYCLE_FACTS.md.

- purchase_evidence.status allows the four new values ("billing_retry" needs
  16 characters; widening a varchar rewrites nothing in PostgreSQL);
- purchase_evidence.provider_event keeps the store event a row was translated
  from (for example "DID_FAIL_TO_RENEW/GRACE_PERIOD"), or NULL for evidence
  recorded by status, as before.

Downgrade refuses while any row uses a new status (dropping them would change
what a family's purchases say) or was recorded from a store event (its replay
check hashes that event).
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_billing_lifecycle"
down_revision = "0008_claim_period_end"
branch_labels = None
depends_on = None

OLD = "('verified', 'pending', 'refunded', 'unverified')"
NEW = "('verified', 'pending', 'refunded', 'unverified', 'grace', 'billing_retry', 'paused', 'expired')"


def upgrade() -> None:
    op.alter_column("purchase_evidence", "status", type_=sa.String(16), existing_type=sa.String(12))
    op.execute("ALTER TABLE purchase_evidence DROP CONSTRAINT ck_evidence_status")
    op.execute(f"ALTER TABLE purchase_evidence ADD CONSTRAINT ck_evidence_status CHECK (status IN {NEW})")
    op.add_column("purchase_evidence", sa.Column("provider_event", sa.String(64), nullable=True))


def downgrade() -> None:
    used = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM purchase_evidence WHERE status NOT IN " + OLD)).scalar_one()
    if used:
        raise RuntimeError(f"{used} purchase evidence row(s) use a billing lifecycle status; "
                           "downgrading would change what those purchases say")
    # A row recorded from a store event hashes that event: dropping the column
    # would turn a replay of it into a conflict (adversarial review, 2026-10-11).
    translated = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM purchase_evidence WHERE provider_event IS NOT NULL")).scalar_one()
    if translated:
        raise RuntimeError(f"{translated} purchase evidence row(s) were recorded from a store event; "
                           "downgrading would drop the event their replay check depends on")
    op.drop_column("purchase_evidence", "provider_event")
    op.execute("ALTER TABLE purchase_evidence DROP CONSTRAINT ck_evidence_status")
    op.execute(f"ALTER TABLE purchase_evidence ADD CONSTRAINT ck_evidence_status CHECK (status IN {OLD})")
    op.alter_column("purchase_evidence", "status", type_=sa.String(12), existing_type=sa.String(16))
