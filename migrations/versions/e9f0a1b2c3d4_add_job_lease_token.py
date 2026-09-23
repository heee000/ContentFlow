"""Add per-claim identity; never manufacture ownership for legacy running jobs."""

from alembic import op
import sqlalchemy as sa

revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("lease_token", sa.String(32), nullable=True))


def downgrade():
    # Drain/stop workers and preserve a backup before a real coordinated rollback.
    # Removing this identity makes old/new attempts indistinguishable again.
    op.drop_column("jobs", "lease_token")
