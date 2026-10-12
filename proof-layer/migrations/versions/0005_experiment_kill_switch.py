"""A run-time kill switch for experiments.

A killed experiment serves its control variant to everyone, writes no new
assignments and refuses exposure receipts, so nothing after the kill can be
counted toward its result. Reviving it resumes the stored (sticky)
assignments. Flipping it is a data change, not a deploy.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_experiment_kill_switch"
down_revision = "0004_outbox_claim_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_controls",
        sa.Column("experiment_key", sa.String(60), primary_key=True),
        sa.Column("killed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("reason", sa.Text),
        sa.Column("changed_by", sa.String(40)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("experiment_controls")
