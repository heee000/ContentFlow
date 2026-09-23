"""Durable, quota-charged worker writes before external storage I/O."""

from alembic import op
import sqlalchemy as sa

revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None

OLD_STATUSES = "status IN ('reserved', 'active', 'delete_pending', 'missing', 'integrity_error', 'deleted', 'abandoned')"
NEW_STATUSES = OLD_STATUSES[:-1] + ", 'staging')"
STAGING_IDENTITY = ("status != 'staging' OR (write_job_id IS NOT NULL AND "
    "write_lease_token IS NOT NULL AND length(write_lease_token) = 32 AND checksum IS NOT NULL)")


def upgrade():
    with op.batch_alter_table("storage_object_allocations") as batch:
        batch.add_column(sa.Column("write_job_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("write_lease_token", sa.String(32), nullable=True))
        batch.drop_constraint(op.f("ck_storage_object_allocations_status"), type_="check")
        batch.create_check_constraint(op.f("ck_storage_object_allocations_status"), NEW_STATUSES)
        batch.create_check_constraint(op.f("ck_storage_object_allocations_staging_identity"), STAGING_IDENTITY)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM storage_object_allocations WHERE status = 'staging'")):
        raise RuntimeError("Resolve staged storage writes before downgrade; do not discard uncertain objects")
    with op.batch_alter_table("storage_object_allocations") as batch:
        batch.drop_constraint(op.f("ck_storage_object_allocations_staging_identity"), type_="check")
        batch.drop_constraint(op.f("ck_storage_object_allocations_status"), type_="check")
        batch.create_check_constraint(op.f("ck_storage_object_allocations_status"), OLD_STATUSES)
        batch.drop_column("write_lease_token")
        batch.drop_column("write_job_id")
