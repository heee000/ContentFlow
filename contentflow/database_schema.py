"""Read-only runtime schema admission, separate from migration ownership.

An exact revision is necessary, not sufficient: verify required tables/columns,
execution identity fields and the PostgreSQL-only vector storage as well. This
does not adopt, stamp, create, repair or migrate a database.
"""

from sqlalchemy import String, column, inspect, select, table, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from . import db
from .schema_contract import HEAD_REVISION


class SchemaCompatibilityError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(
            f"Database schema check failed ({reason}); stop incompatible runtimes and "
            "verify the approved migration before starting ContentFlow. No schema repair was attempted."
        )


def _verify_connection(connection: Connection) -> None:
    from . import entities  # noqa: F401 - populate declarative metadata without I/O

    if connection.dialect.name == "postgresql":
        # These checks use their own transaction. Limits disappear on rollback;
        # no connection setting leaks into a later business transaction.
        connection.exec_driver_sql("SET LOCAL statement_timeout = '3000ms'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '1500ms'")
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "alembic_version" not in tables:
        raise SchemaCompatibilityError("unversioned_database")
    version = table("alembic_version", column("version_num"))
    revisions = list(connection.scalars(select(version.c.version_num).limit(2)))
    if revisions != [HEAD_REVISION]:
        raise SchemaCompatibilityError("revision_mismatch")
    required = set(db.Base.metadata.tables)
    if not required <= tables:
        raise SchemaCompatibilityError("missing_tables")
    reflected = inspector.get_multi_columns(filter_names=sorted(required))
    columns = {
        name: {item["name"]: item for item in reflected.get((None, name), [])}
        for name in required
    }
    for name in required:
        if not set(db.Base.metadata.tables[name].columns.keys()) <= columns[name].keys():
            raise SchemaCompatibilityError("missing_columns")
    for name, field, length in (
        ("jobs", "lease_token", 32),
        ("storage_object_allocations", "write_job_id", 36),
        ("storage_object_allocations", "write_lease_token", 32),
    ):
        found = columns[name][field]
        if not isinstance(found["type"], String) or found["type"].length != length or not found["nullable"]:
            raise SchemaCompatibilityError("execution_identity_shape")
    checks = {item["name"] for item in inspector.get_check_constraints("storage_object_allocations")}
    if "ck_storage_object_allocations_staging_identity" not in checks:
        raise SchemaCompatibilityError("missing_storage_write_constraint")
    if connection.dialect.name == "postgresql":
        if "knowledge_vectors" not in tables:
            raise SchemaCompatibilityError("missing_vector_storage")
        vector_type = connection.execute(text(
            "SELECT format_type(atttypid, atttypmod), attnotnull FROM pg_attribute "
            "WHERE attrelid = to_regclass('knowledge_vectors') "
            "AND attname = 'embedding' AND NOT attisdropped"
        )).one_or_none()
        if vector_type is None or tuple(vector_type) != ("vector(1024)", True):
            raise SchemaCompatibilityError("vector_shape")
        # Exercise the runtime's unqualified vector type and cosine operator,
        # and SELECT permission, without loading user vectors or writing rows.
        connection.execute(text(
            "SELECT chunk_id, embedding <=> CAST('[0]' AS vector) "
            "FROM knowledge_vectors WHERE false"
        ))


def verify_database_schema(session_factory) -> None:
    """Fresh inspection on startup/readiness/claim; never trust a process cache."""
    try:
        with session_factory() as session:
            _verify_connection(session.connection())
            # Session context closes/rolls back catalog reads and SET LOCAL.
    except SQLAlchemyError:
        # DB exception chains can contain credential-bearing DSNs/parameters.
        raise SchemaCompatibilityError("database_unavailable") from None
