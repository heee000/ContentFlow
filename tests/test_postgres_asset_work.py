"""Asset result acceptance with real PostgreSQL transactions and lock waits."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from contentflow.asset_work import AssetWork
from contentflow.entities import Asset, ContentItem, Job, StorageObjectAllocation, WorkspaceStorageUsage
from contentflow.media_providers import MockMediaProvider
from contentflow.object_storage import build_object_storage
from contentflow.routers.contents import update_content
from contentflow.schemas import ContentUpdate
from contentflow.storage_ledger import create_workspace_storage_usage
from contentflow.worker import Worker, handle_asset_generate
import test_postgres_integration as pg
from test_postgres_execution_fence import wait_for_database_lock
from test_worker_storage_transactions import StorageProbe


postgres_harness = pg.postgres_harness
pytestmark = pytest.mark.skipif(not pg.TEST_DATABASE_URL, reason="Dedicated PostgreSQL test URL is required")


def seed(h):
    fixture = pg._create_publish_fixture(h, status="cancelled", external_id=None)
    with h.sessions() as session:
        asset = session.scalar(select(Asset).where(Asset.workspace_id == fixture["workspace_id"]))
        asset.status, asset.provider, asset.storage_uri = "queued", "mock", None
        create_workspace_storage_usage(session, fixture["workspace_id"])
        job = Job(workspace_id=asset.workspace_id, job_type="asset.generate", status="queued",
            payload_json={"asset_id": asset.id}, idempotency_key=f"test-asset-work:{asset.id}",
            run_at=datetime.now(timezone.utc) - timedelta(seconds=30))
        session.add(job)
        session.commit()
        return SimpleNamespace(**fixture, asset_id=asset.id, content_id=asset.content_item_id, job_id=job.id)


def edit(h, fixture, session):
    return update_content(fixture.content_id,
        ContentUpdate(expected_version=1, body="TEST-ONLY changed while provider/storage was running"),
        SimpleNamespace(workspace_id=fixture.workspace_id, user_id=fixture.user_id), session, h.settings)


@pytest.mark.parametrize("phase", ["provider", "storage"])
def test_postgres_actual_worker_discards_changed_content_without_untracked_object(postgres_harness, phase):
    h = postgres_harness
    fixture = seed(h)
    generated = MockMediaProvider().generate(kind="image", prompt="test", metadata={}, idempotency_key="test")

    def change():
        with h.sessions() as session:
            edit(h, fixture, session)
            session.commit()

    def concurrent_edit():
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(change).result(timeout=10)

    def generate(**kwargs):
        if phase == "provider":
            concurrent_edit()
        return generated

    probe = StorageProbe(build_object_storage(h.settings), concurrent_edit)
    with patch.object(MockMediaProvider, "generate", side_effect=generate), \
         patch("contentflow.storage_ledger.build_object_storage", return_value=probe):
        with Worker(settings=h.settings, session_factory=h.sessions, worker_id="test-pg-asset") as worker:
            assert worker.run_once()
    with h.sessions() as session:
        asset = session.get(Asset, fixture.asset_id)
        job = session.get(Job, fixture.job_id)
        assert asset.status == "stale" and asset.storage_uri is None
        assert job.status == "succeeded" and job.result_json["outcome"] == "superseded"
        allocation = session.scalar(select(StorageObjectAllocation).where(
            StorageObjectAllocation.owner_id == asset.id))
        usage = session.get(WorkspaceStorageUsage, fixture.workspace_id)
        if phase == "provider":
            assert allocation is None and probe.stored is None
            assert usage.used_objects == 0
        else:
            assert allocation.status == "staging" and allocation.storage_uri == probe.stored.uri
            assert (usage.used_objects, usage.used_bytes) == (1, probe.stored.size_bytes)
            assert probe.delegate.read(probe.stored.uri)


def test_postgres_final_result_rechecks_after_waiting_for_content_edit(postgres_harness):
    h = postgres_harness
    fixture = seed(h)
    provider_started, release_provider, locking = Event(), Event(), Event()
    observed = {}
    generated = MockMediaProvider().generate(kind="image", prompt="test", metadata={}, idempotency_key="test")
    original_check = AssetWork.require_current

    def generate(**kwargs):
        provider_started.set()
        assert release_provider.wait(timeout=15)
        return generated

    def check(work, session, *, lock=False):
        if lock:
            locking.set()
        return original_check(work, session, lock=lock)

    def handler(session, payload, settings):
        # Test instrumentation is not a domain mutation and must not take the
        # execution write fence before the provider/storage calls.
        observed["pid"] = session.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        session.connection().exec_driver_sql("SET LOCAL statement_timeout = '15s'")
        return handle_asset_generate(session, payload, settings)

    def run():
        with Worker(settings=h.settings, session_factory=h.sessions, worker_id="test-pg-wait",
                handlers={"asset.generate": handler}) as worker:
            return worker.run_once()

    with patch.object(MockMediaProvider, "generate", side_effect=generate), \
         patch.object(AssetWork, "require_current", check), ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        try:
            assert provider_started.wait(timeout=10)
            with h.sessions() as editor:
                edit(h, fixture, editor)
                editor.flush()
                release_provider.set()
                assert locking.wait(timeout=10)
                wait_for_database_lock(h, observed["pid"])
                editor.commit()
            assert future.result(timeout=15)
        finally:
            release_provider.set()
    with h.sessions() as session:
        asset = session.get(Asset, fixture.asset_id)
        assert asset.status == "stale" and asset.storage_uri is None
        assert session.get(ContentItem, fixture.content_id).version == 2
        assert session.get(Job, fixture.job_id).result_json["outcome"] == "superseded"
        allocation = session.scalar(select(StorageObjectAllocation).where(
            StorageObjectAllocation.owner_id == fixture.asset_id))
        assert allocation.status == "staging"
