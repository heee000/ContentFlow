"""add workspace storage allocation ledger

Revision ID: b0c1d2e3f4a5
Revises: 9a7b2c3d4e5f
Create Date: 2026-09-03 22:30:00
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, None] = "9a7b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BACKFILL_BATCH_SIZE = 500


def _integer_size(value: object) -> tuple[int, bool]:
    if isinstance(value, bool):
        return 0, False
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isdecimal():
        parsed = int(value)
    else:
        return 0, False
    return (parsed, True) if parsed > 0 else (0, False)


def _checksum(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        return None
    return normalized


def _filename(uri: str) -> str:
    name = uri.rstrip("/").rsplit("/", 1)[-1] or "legacy-object"
    return name[-255:]


def _allocation_row(
    *,
    workspace_id: object,
    owner_type: str,
    owner_id: object,
    category: str,
    uri: object,
    size_value: object,
    checksum_value: object = None,
    mime_type: object = None,
    now: datetime,
) -> dict[str, Any] | None:
    if not isinstance(workspace_id, str) or not workspace_id:
        return None
    if not isinstance(owner_id, str) or not owner_id:
        return None
    if not isinstance(uri, str) or not uri.startswith(("file://", "s3://")):
        return None
    size_bytes, size_verified = _integer_size(size_value)
    return {
        "id": str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"contentflow-storage-allocation:{uri}")
        ),
        "workspace_id": workspace_id,
        "owner_type": owner_type,
        "owner_id": owner_id[-160:],
        "category": category[-160:],
        "filename": _filename(uri),
        "status": "active",
        "storage_uri": uri,
        "checksum": _checksum(checksum_value),
        "size_bytes": size_bytes,
        "size_verified": size_verified,
        "mime_type": str(mime_type)[:120] if mime_type else None,
        "reserved_until": None,
        "delete_attempts": 0,
        "last_error": None,
        "deleted_at": None,
        "created_at": now,
        "updated_at": now,
    }


def _insert_rows(connection, allocations, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    if connection.dialect.name == "postgresql":
        statement = postgresql_insert(allocations).values(rows)
        statement = statement.on_conflict_do_nothing(index_elements=["storage_uri"])
    elif connection.dialect.name == "sqlite":
        statement = sqlite_insert(allocations).values(rows)
        statement = statement.on_conflict_do_nothing(index_elements=["storage_uri"])
    else:
        raise RuntimeError("storage ledger backfill supports only PostgreSQL and SQLite")
    connection.execute(statement)


def _backfill_query(
    connection,
    allocations,
    query,
    row_factory,
) -> None:
    result = connection.execute(
        query.execution_options(
            stream_results=True,
            max_row_buffer=BACKFILL_BATCH_SIZE,
        )
    ).mappings()
    try:
        while batch := result.fetchmany(BACKFILL_BATCH_SIZE):
            rows = [
                row for source in batch if (row := row_factory(source)) is not None
            ]
            _insert_rows(connection, allocations, rows)
    finally:
        result.close()


def upgrade() -> None:
    op.create_table(
        "workspace_storage_usage",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("used_objects", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "reserved_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "reserved_objects", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "unverified_objects", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("used_bytes >= 0", name=op.f("ck_workspace_storage_usage_used_bytes_non_negative")),
        sa.CheckConstraint("used_objects >= 0", name=op.f("ck_workspace_storage_usage_used_objects_non_negative")),
        sa.CheckConstraint("reserved_bytes >= 0", name=op.f("ck_workspace_storage_usage_reserved_bytes_non_negative")),
        sa.CheckConstraint("reserved_objects >= 0", name=op.f("ck_workspace_storage_usage_reserved_objects_non_negative")),
        sa.CheckConstraint("unverified_objects >= 0", name=op.f("ck_workspace_storage_usage_unverified_objects_non_negative")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_storage_usage_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name=op.f("pk_workspace_storage_usage")),
    )
    op.create_table(
        "storage_object_allocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("owner_type", sa.String(length=48), nullable=False),
        sa.Column("owner_id", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=160), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'reserved'"), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("size_verified", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_storage_object_allocations_size_bytes_non_negative")),
        sa.CheckConstraint("delete_attempts >= 0", name=op.f("ck_storage_object_allocations_delete_attempts_non_negative")),
        sa.CheckConstraint(
            "status IN ('reserved', 'active', 'delete_pending', 'missing', "
            "'integrity_error', 'deleted', 'abandoned')",
            name=op.f("ck_storage_object_allocations_status"),
        ),
        sa.CheckConstraint(
            "status != 'reserved' OR "
            "(storage_uri IS NULL AND reserved_until IS NOT NULL)",
            name=op.f("ck_storage_object_allocations_reserved_shape"),
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'abandoned') OR storage_uri IS NOT NULL",
            name=op.f("ck_storage_object_allocations_persisted_uri"),
        ),
        sa.CheckConstraint(
            "status != 'deleted' OR deleted_at IS NOT NULL",
            name=op.f("ck_storage_object_allocations_deleted_timestamp"),
        ),
        sa.CheckConstraint(
            "checksum IS NULL OR length(checksum) = 64",
            name=op.f("ck_storage_object_allocations_checksum_length"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_storage_object_allocations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_object_allocations")),
        sa.UniqueConstraint("storage_uri", name="uq_storage_allocation_uri"),
    )
    op.create_index(
        op.f("ix_storage_object_allocations_workspace_id"),
        "storage_object_allocations",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_storage_object_allocations_owner_type"),
        "storage_object_allocations",
        ["owner_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_storage_object_allocations_owner_id"),
        "storage_object_allocations",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_storage_object_allocations_category"),
        "storage_object_allocations",
        ["category"],
        unique=False,
    )
    op.create_index(
        op.f("ix_storage_object_allocations_status"),
        "storage_object_allocations",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_storage_object_allocations_checksum"),
        "storage_object_allocations",
        ["checksum"],
        unique=False,
    )
    op.create_index(
        op.f("ix_storage_object_allocations_reserved_until"),
        "storage_object_allocations",
        ["reserved_until"],
        unique=False,
    )
    op.create_index(
        "ix_storage_allocations_workspace_status_updated_page",
        "storage_object_allocations",
        ["workspace_id", "status", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_storage_allocations_workspace_owner",
        "storage_object_allocations",
        ["workspace_id", "owner_type", "owner_id"],
        unique=False,
    )

    connection = op.get_bind()
    metadata = sa.MetaData()
    allocations = sa.Table("storage_object_allocations", metadata, autoload_with=connection)
    usage = sa.Table("workspace_storage_usage", metadata, autoload_with=connection)
    workspaces = sa.Table("workspaces", metadata, autoload_with=connection)
    knowledge = sa.Table("knowledge_documents", metadata, autoload_with=connection)
    assets = sa.Table("assets", metadata, autoload_with=connection)
    evidence = sa.Table("publish_evidence_items", metadata, autoload_with=connection)
    publish_jobs = sa.Table("publish_jobs", metadata, autoload_with=connection)
    now = datetime.now(timezone.utc)

    _backfill_query(
        connection,
        allocations,
        sa.select(
            knowledge.c.id,
            knowledge.c.workspace_id,
            knowledge.c.storage_uri,
            knowledge.c.checksum,
            knowledge.c.metadata_json,
        ).where(knowledge.c.storage_uri.is_not(None)),
        lambda row: _allocation_row(
            workspace_id=row["workspace_id"],
            owner_type="knowledge_document",
            owner_id=row["id"],
            category="knowledge",
            uri=row["storage_uri"],
            size_value=(row["metadata_json"] or {}).get("size_bytes")
            if isinstance(row["metadata_json"], dict)
            else None,
            checksum_value=row["checksum"],
            mime_type=(row["metadata_json"] or {}).get("mime_type")
            if isinstance(row["metadata_json"], dict)
            else None,
            now=now,
        ),
    )
    _backfill_query(
        connection,
        allocations,
        sa.select(
            assets.c.id,
            assets.c.workspace_id,
            assets.c.storage_uri,
            assets.c.size_bytes,
            assets.c.mime_type,
            assets.c.metadata_json,
        ).where(assets.c.storage_uri.is_not(None)),
        lambda row: _allocation_row(
            workspace_id=row["workspace_id"],
            owner_type="asset",
            owner_id=row["id"],
            category="assets",
            uri=row["storage_uri"],
            size_value=row["size_bytes"],
            checksum_value=(row["metadata_json"] or {}).get("checksum")
            if isinstance(row["metadata_json"], dict)
            else None,
            mime_type=row["mime_type"],
            now=now,
        ),
    )
    _backfill_query(
        connection,
        allocations,
        sa.select(
            evidence.c.id,
            evidence.c.workspace_id,
            evidence.c.storage_uri,
            evidence.c.size_bytes,
            evidence.c.object_sha256,
            evidence.c.mime_type,
        ),
        lambda row: _allocation_row(
            workspace_id=row["workspace_id"],
            owner_type="publish_evidence",
            owner_id=row["id"],
            category="publish-evidence",
            uri=row["storage_uri"],
            size_value=row["size_bytes"],
            checksum_value=row["object_sha256"],
            mime_type=row["mime_type"],
            now=now,
        ),
    )

    def publish_row(row):
        response = row["response_json"] if isinstance(row["response_json"], dict) else {}
        package_uri = response.get("package_uri")
        export_uri = response.get("storage_uri")
        uri = package_uri or export_uri
        if not uri and isinstance(row["external_url"], str) and row[
            "external_url"
        ].startswith(("file://", "s3://")):
            uri = row["external_url"]
        return _allocation_row(
            workspace_id=row["workspace_id"],
            owner_type="publish_job",
            owner_id=row["id"],
            category="script-publish" if package_uri else "exports",
            uri=uri,
            size_value=response.get("size_bytes"),
            checksum_value=response.get("package_sha256"),
            mime_type="application/zip",
            now=now,
        )

    _backfill_query(
        connection,
        allocations,
        sa.select(
            publish_jobs.c.id,
            publish_jobs.c.workspace_id,
            publish_jobs.c.external_url,
            publish_jobs.c.response_json,
        ),
        publish_row,
    )

    legacy_references = sa.union_all(
        sa.select(knowledge.c.storage_uri.label("storage_uri")).where(
            knowledge.c.storage_uri.is_not(None)
        ),
        sa.select(assets.c.storage_uri.label("storage_uri")).where(
            assets.c.storage_uri.is_not(None)
        ),
        sa.select(evidence.c.storage_uri.label("storage_uri")).where(
            evidence.c.storage_uri.is_not(None)
        ),
    ).subquery()
    shared_legacy_uris = (
        sa.select(legacy_references.c.storage_uri)
        .group_by(legacy_references.c.storage_uri)
        .having(sa.func.count() > 1)
    )
    connection.execute(
        allocations.update()
        .where(allocations.c.storage_uri.in_(shared_legacy_uris))
        .values(
            owner_type="shared_legacy",
            owner_id="multiple",
            status="integrity_error",
            last_error=(
                "multiple legacy database rows reference this object; "
                "automatic deletion is disabled"
            ),
            updated_at=now,
        )
    )

    connection.execute(
        usage.insert().from_select(
            [
                "workspace_id",
                "used_bytes",
                "used_objects",
                "reserved_bytes",
                "reserved_objects",
                "unverified_objects",
                "last_reconciled_at",
                "created_at",
                "updated_at",
            ],
            sa.select(
                workspaces.c.id,
                sa.literal(0),
                sa.literal(0),
                sa.literal(0),
                sa.literal(0),
                sa.literal(0),
                sa.null(),
                sa.literal(now),
                sa.literal(now),
            ),
        )
    )
    summaries = connection.execute(
        sa.select(
            allocations.c.workspace_id,
            sa.func.coalesce(sa.func.sum(allocations.c.size_bytes), 0),
            sa.func.count(allocations.c.id),
            sa.func.sum(sa.case((allocations.c.size_verified.is_(False), 1), else_=0)),
        ).group_by(allocations.c.workspace_id)
    )
    for workspace_id, used_bytes, used_objects, unverified_objects in summaries:
        connection.execute(
            usage.update()
            .where(usage.c.workspace_id == workspace_id)
            .values(
                used_bytes=int(used_bytes or 0),
                used_objects=int(used_objects or 0),
                unverified_objects=int(unverified_objects or 0),
                updated_at=now,
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_storage_allocations_workspace_owner",
        table_name="storage_object_allocations",
    )
    op.drop_index(
        "ix_storage_allocations_workspace_status_updated_page",
        table_name="storage_object_allocations",
    )
    op.drop_index(
        op.f("ix_storage_object_allocations_reserved_until"),
        table_name="storage_object_allocations",
    )
    op.drop_index(
        op.f("ix_storage_object_allocations_checksum"),
        table_name="storage_object_allocations",
    )
    op.drop_index(
        op.f("ix_storage_object_allocations_status"),
        table_name="storage_object_allocations",
    )
    op.drop_index(
        op.f("ix_storage_object_allocations_category"),
        table_name="storage_object_allocations",
    )
    op.drop_index(
        op.f("ix_storage_object_allocations_owner_id"),
        table_name="storage_object_allocations",
    )
    op.drop_index(
        op.f("ix_storage_object_allocations_owner_type"),
        table_name="storage_object_allocations",
    )
    op.drop_index(
        op.f("ix_storage_object_allocations_workspace_id"),
        table_name="storage_object_allocations",
    )
    op.drop_table("storage_object_allocations")
    op.drop_table("workspace_storage_usage")
