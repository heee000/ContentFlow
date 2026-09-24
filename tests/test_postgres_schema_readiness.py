"""Runtime compatibility must include PostgreSQL-only storage and read-only access."""

import pytest
from sqlalchemy.orm import sessionmaker

from contentflow.database_schema import SchemaCompatibilityError, verify_database_schema
import test_postgres_integration as pg


postgres_harness = pg.postgres_harness
pytestmark = pytest.mark.skipif(not pg.TEST_DATABASE_URL, reason="Dedicated PostgreSQL test URL is required")


def test_postgres_schema_verification_is_read_only_and_does_not_leak_timeouts(postgres_harness):
    h = postgres_harness
    with h.engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            verify_database_schema(sessionmaker(bind=connection))
    with h.engine.connect() as connection:
        before = (connection.exec_driver_sql("SHOW statement_timeout").scalar_one(),
            connection.exec_driver_sql("SHOW lock_timeout").scalar_one())
    verify_database_schema(h.sessions)
    with h.engine.connect() as connection:
        after = (connection.exec_driver_sql("SHOW statement_timeout").scalar_one(),
            connection.exec_driver_sql("SHOW lock_timeout").scalar_one())
    assert after == before


@pytest.mark.parametrize("statement,reason", [
    ("DROP TABLE knowledge_vectors", "missing_vector_storage"),
    ("ALTER TABLE knowledge_vectors ALTER COLUMN embedding TYPE vector(768)", "vector_shape"),
    ("ALTER TABLE jobs DROP COLUMN lease_token", "missing_columns"),
    ("ALTER TABLE jobs ALTER COLUMN lease_token TYPE text", "execution_identity_shape"),
    ("ALTER TABLE storage_object_allocations DROP CONSTRAINT ck_storage_object_allocations_staging_identity", "missing_storage_write_constraint"),
])
def test_postgres_schema_stamp_cannot_hide_structural_drift(postgres_harness, statement, reason):
    h = postgres_harness
    verify_database_schema(h.sessions)
    # The harness creates a fresh, disposable database per test, never the
    # supplied database itself or a real application database.
    with h.engine.begin() as connection:
        connection.exec_driver_sql(statement)
    with pytest.raises(SchemaCompatibilityError) as captured:
        verify_database_schema(h.sessions)
    assert captured.value.reason == reason
