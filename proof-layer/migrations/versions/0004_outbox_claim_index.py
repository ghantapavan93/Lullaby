"""Index the outbox jobs a worker can claim.

The claim query (`worker.claim`) filters on state and orders by id. Finished
jobs stay in the table as history, so without this index the claim walks every
job ever written before it reaches a waiting one. Measured on the benchmark
database with 1,000,000 finished jobs and 200 waiting: 356 ms median before,
0.026 ms after, same job claimed (`bench/outbox_index.py`,
`artifacts/benchmarks/optimization-outbox-claim-*.json`). Cost: about 26% more
time to insert and finish 10,000 jobs; the index holds only waiting and leased
jobs, so it stays small.
"""
from alembic import op

revision = "0004_outbox_claim_index"
down_revision = "0003_camera_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbox_claimable ON outbox (id) "
               "WHERE state IN ('pending', 'in_progress')")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_outbox_claimable")
