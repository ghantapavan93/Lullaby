"""Video evidence: a real clip, its analysis runs, measured intervals, and
what people said about it.

Five narrow tables. The night-briefing tables don't fit: they describe an
eight-hour window of synthetic events, and a 34-second clip must not be
presented as part of a night.

- video_assets: one stored clip per household and source file. The stored
  copy is video only (audio dropped, no re-encode). Provenance, retention,
  deletion. The bytes live outside any served directory.
- video_analysis_runs: one per (clip, processor version, config). Its state
  is the durable record of the work (REGISTERED, VALIDATING, ANALYZING,
  COMPLETED, FAILED); the outbox job only delivers it.
- video_observations: the measured intervals of a completed run.
  Append-only: written once, in the run's final transaction.
- video_annotations: what a person reported about an interval. Append-only;
  a correction is a new row that names the one it replaces.
- video_acknowledgments: a member saw a given evidence revision. Append-only.

Computer measurements and human reports are different tables, never
different flags on one row.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_video_evidence"
down_revision = "0009_billing_lifecycle"
branch_labels = None
depends_on = None

APPEND_ONLY = ("video_observations", "video_annotations", "video_acknowledgments")
RUN_STATES = "('REGISTERED', 'VALIDATING', 'ANALYZING', 'COMPLETED', 'FAILED')"
KINDS = ("('insufficient_evidence', 'low_quality', 'camera_motion', 'movement', 'small_change', "
         "'no_significant_movement')")
LABELS = "('caregiver_present', 'infant_moving', 'infant_still', 'camera_moved', 'not_sure')"
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "video_assets",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("household_id", sa.String(40), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("device_id", sa.String(40), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("storage_key", sa.String(120), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),   # the file as received: the dedupe key
        sa.Column("sha256", sa.String(64), nullable=False),          # the stored video-only copy: what is served
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("container", sa.String(40), nullable=False),
        sa.Column("codec", sa.String(16), nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("fps", sa.Float),
        sa.Column("duration_s", sa.Float),
        sa.Column("provenance", postgresql.JSONB, nullable=False),
        sa.Column("evidence_revision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("household_id", "id", name="uq_video_assets_tenant"),
        sa.UniqueConstraint("household_id", "source_sha256", name="uq_video_assets_content"),
        sa.ForeignKeyConstraint(["household_id", "device_id"], ["devices.household_id", "devices.id"],
                                name="fk_video_assets_device"),
        sa.CheckConstraint("source_kind IN ('licensed_sample', 'staged_sample', 'local_import')",
                           name="ck_video_assets_source"),
        sa.CheckConstraint("evidence_revision >= 0", name="ck_video_assets_revision"),
    )
    op.create_table(
        "video_analysis_runs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("asset_id", sa.String(40), nullable=False),
        sa.Column("processor_version", sa.String(40), nullable=False),
        sa.Column("config_digest", sa.String(16), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="REGISTERED"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(40)),
        sa.Column("error_detail", postgresql.JSONB),
        sa.Column("summary", postgresql.JSONB),
        sa.Column("windows", postgresql.JSONB),
        sa.Column("frame_samples", postgresql.JSONB),
        sa.Column("elapsed_ms", sa.Integer),
        sa.Column("trace_id", sa.String(40)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("household_id", "id", name="uq_video_runs_tenant"),
        sa.UniqueConstraint("asset_id", "processor_version", "config_digest", name="uq_video_runs_identity"),
        sa.ForeignKeyConstraint(["household_id", "asset_id"], ["video_assets.household_id", "video_assets.id"],
                                name="fk_video_runs_asset"),
        sa.CheckConstraint(f"state IN {RUN_STATES}", name="ck_video_runs_state"),
        sa.CheckConstraint("(state = 'FAILED') = (error_code IS NOT NULL)", name="ck_video_runs_error"),
    )
    op.create_table(
        "video_observations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(48), nullable=False),
        sa.Column("t_start", sa.Float, nullable=False),
        sa.Column("t_end", sa.Float, nullable=False),
        sa.Column("windows", sa.Integer, nullable=False),
        sa.Column("frames", sa.Integer, nullable=False),
        sa.Column("coverage_min", sa.Float),
        sa.Column("local_change_mean", sa.Float),
        sa.Column("local_change_max", sa.Float),
        sa.Column("brightness_mean", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("run_id", "seq", name="uq_video_observations_seq"),
        sa.ForeignKeyConstraint(["household_id", "run_id"], ["video_analysis_runs.household_id",
                                                             "video_analysis_runs.id"], name="fk_video_obs_run"),
        sa.CheckConstraint(f"kind IN {KINDS}", name="ck_video_obs_kind"),
        sa.CheckConstraint("t_end > t_start AND t_start >= 0", name="ck_video_obs_interval"),
    )
    op.create_table(
        "video_annotations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("asset_id", sa.String(40), nullable=False),
        sa.Column("member_id", sa.String(40), nullable=False),
        sa.Column("client_ref", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("label", sa.String(24), nullable=False),
        sa.Column("t_start", sa.Float, nullable=False),
        sa.Column("t_end", sa.Float, nullable=False),
        sa.Column("supersedes_annotation_id", sa.String(40)),
        sa.Column("trace_id", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("household_id", "id", name="uq_video_annotations_tenant"),
        sa.UniqueConstraint("household_id", "client_ref", name="uq_video_annotations_client_ref"),
        sa.UniqueConstraint("asset_id", "revision", name="uq_video_annotations_revision"),
        sa.UniqueConstraint("supersedes_annotation_id", name="uq_video_annotations_single_correction"),
        sa.ForeignKeyConstraint(["household_id", "asset_id"], ["video_assets.household_id", "video_assets.id"],
                                name="fk_video_annotations_asset"),
        sa.ForeignKeyConstraint(["household_id", "member_id"], ["members.household_id", "members.id"],
                                name="fk_video_annotations_member"),
        sa.ForeignKeyConstraint(["household_id", "supersedes_annotation_id"],
                                ["video_annotations.household_id", "video_annotations.id"],
                                name="fk_video_annotations_supersedes"),
        sa.CheckConstraint(f"label IN {LABELS}", name="ck_video_annotations_label"),
        sa.CheckConstraint("t_end > t_start AND t_start >= 0", name="ck_video_annotations_interval"),
    )
    op.create_table(
        "video_acknowledgments",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("asset_id", sa.String(40), nullable=False),
        sa.Column("member_id", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("member_id", "asset_id", "revision", name="uq_video_ack_once"),
        sa.ForeignKeyConstraint(["household_id", "asset_id"], ["video_assets.household_id", "video_assets.id"],
                                name="fk_video_ack_asset"),
        sa.ForeignKeyConstraint(["household_id", "member_id"], ["members.household_id", "members.id"],
                                name="fk_video_ack_member"),
        sa.CheckConstraint("revision >= 1", name="ck_video_ack_revision"),
    )
    for table in APPEND_ONLY:
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                   f"FOR EACH ROW EXECUTE FUNCTION reject_mutation()")


def downgrade() -> None:
    for table in ("video_acknowledgments", "video_annotations", "video_observations", "video_analysis_runs",
                  "video_assets"):
        op.drop_table(table)
