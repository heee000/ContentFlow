"""Bound new metrics; preserve legacy invalid observations in quarantine.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
"""

from alembic import op
import sqlalchemy as sa

revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None

# Freeze historical semantics, rather than importing a future runtime contract.
BOUNDS = " AND ".join(
    f"{field} >= 0 AND {field} <= 9007199254740991"
    for field in ("impressions", "clicks", "likes", "comments", "shares")
)


def upgrade():
    op.add_column(
        "metric_snapshots",
        sa.Column(
            "validation_status", sa.String(24), server_default="valid", nullable=False
        ),
    )
    # Only label; never delete, clamp, or replace the original observed values.
    op.execute(
        sa.text(
            "UPDATE metric_snapshots SET validation_status = 'quarantined' "
            "WHERE NOT COALESCE((" + BOUNDS + "), FALSE)"
        )
    )
    with op.batch_alter_table("metric_snapshots") as batch:
        batch.create_check_constraint(
            "validation_status", "validation_status IN ('valid', 'quarantined')"
        )
        batch.create_check_constraint(
            "counters_bounded", "validation_status = 'quarantined' OR (" + BOUNDS + ")"
        )


def downgrade():
    with op.batch_alter_table("metric_snapshots") as batch:
        batch.drop_constraint(
            op.f("ck_metric_snapshots_counters_bounded"), type_="check"
        )
        batch.drop_constraint(
            op.f("ck_metric_snapshots_validation_status"), type_="check"
        )
        batch.drop_column("validation_status")
