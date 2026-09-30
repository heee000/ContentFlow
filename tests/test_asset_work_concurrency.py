"""Real Worker/API interleavings with isolated providers and object storage."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from sqlalchemy import select

from contentflow import db
from contentflow.entities import Asset, ContentItem, Job, StorageObjectAllocation, WorkspaceStorageUsage
from contentflow.media_providers import MediaGeneration, MediaProviderError, MockMediaProvider, media_provider_profile_fingerprint
from contentflow.object_storage import build_object_storage
import test_worker_storage_transactions as storage_tests


application = storage_tests.application
queued_asset = storage_tests.queued_asset
StorageProbe = storage_tests.StorageProbe


def edit_in_another_thread(application, asset_id):
    def edit():
        with db.SessionLocal() as session:
            asset = session.get(Asset, asset_id)
            content_id = asset.content_item_id
        response = application.client.patch(
            f"/api/v1/contents/{content_id}", headers=application.headers,
            json={"expected_version": 1, "body": "TEST-ONLY edited while old media was in flight"},
        )
        assert response.status_code == 200, response.text
        with db.SessionLocal() as session:
            assert session.get(Asset, asset_id).status == "stale"
    # API requests must not inherit the Worker execution ContextVar.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(edit).result(timeout=10)


def prepare(application, operation):
    asset_id, job_id = queued_asset(application)
    with db.SessionLocal() as session:
        asset = session.get(Asset, asset_id)
        job = session.get(Job, job_id)
        job.job_type = f"asset.{operation}"
        if operation == "poll":
            asset.status = "processing"
            asset.external_task_id = "test-async-task"
            asset.metadata_json = {"media_provider_profile_fingerprint":
                media_provider_profile_fingerprint(application.settings, "image")}
        if operation in {"search", "download"}:
            asset.provider = "openverse"
            asset.metadata_json = {
                "search_query": "TEST-ONLY image",
                "search_candidates": [{"id": "candidate", "download_url": "https://images.example/test.png"}],
                "pending_candidate_selection": {"candidate_id": "candidate"},
            }
            job.payload_json = {"asset_id": asset_id, "candidate_id": "candidate", "content_version": 1}
        session.commit()
    return asset_id, job_id


@pytest.mark.parametrize("operation,outcome", [
    ("generate", "ready"), ("generate", "processing"), ("generate", "error"),
    ("poll", "ready"), ("poll", "processing"), ("poll", "error"),
    ("search", "ready"), ("search", "error"), ("download", "ready"),
])
def test_edit_during_remote_operation_never_revives_old_asset(application, operation, outcome):
    asset_id, job_id = prepare(application, operation)
    generated = MockMediaProvider().generate(kind="image", prompt="test", metadata={}, idempotency_key="test")

    def remote_result():
        edit_in_another_thread(application, asset_id)
        if outcome == "error":
            raise MediaProviderError("TEST-ONLY remote outcome unknown", retryable=False)
        if outcome == "processing":
            return MediaGeneration(status="processing", external_task_id="test-late-task")
        return generated

    class Provider:
        provider_name = "openverse"

        def generate(self, **kwargs):
            return remote_result()

        def poll(self, task_id):
            assert task_id == "test-async-task"
            return remote_result()

        def search(self, **kwargs):
            remote_result()
            return [{"id": "test-late-candidate"}]

    with ExitStack() as stack:
        stack.enter_context(patch("contentflow.worker.build_media_provider", return_value=Provider()))
        stack.enter_context(patch("contentflow.worker.build_image_search_provider", return_value=Provider()))
        stack.enter_context(patch("contentflow.worker.validate_media_download_url"))
        stack.enter_context(patch("contentflow.worker.download_generated_media", side_effect=lambda *a, **k: (
            remote_result().content if operation == "download" else generated.content)))
        assert application.worker.run_once()
    with db.SessionLocal() as session:
        old = session.get(Asset, asset_id)
        assert old.status == "stale", "late success/failure must preserve the edit's stale state"
        assert old.storage_uri is None
        content = session.get(ContentItem, old.content_item_id)
        assert (content.version, content.status) == (2, "needs_review")
        new_assets = list(session.scalars(select(Asset).where(
            Asset.content_item_id == content.id, Asset.content_version == 2)))
        assert len(new_assets) == 1 and new_assets[0].status == "planned"
        assert new_assets[0].storage_uri is None
        assert not list(session.scalars(select(StorageObjectAllocation)))
        job = session.get(Job, job_id)
        if outcome == "error":
            # Unknown provider execution must not be reclassified as safe success.
            assert job.status in {"failed", "manual_review"}
        else:
            assert job.status == "succeeded"
            assert job.result_json["outcome"] == "superseded"
        assert not list(session.scalars(select(Job).where(Job.id != job_id, Job.job_type == "asset.poll")))


@pytest.mark.parametrize("operation", ["generate", "poll", "download"])
def test_edit_during_object_put_keeps_charged_staging_not_attached(application, operation):
    asset_id, job_id = prepare(application, operation)
    generated = MockMediaProvider().generate(kind="image", prompt="test", metadata={}, idempotency_key="test")
    probe = StorageProbe(build_object_storage(application.settings),
        lambda: edit_in_another_thread(application, asset_id))
    with ExitStack() as stack:
        stack.enter_context(patch("contentflow.storage_ledger.build_object_storage", return_value=probe))
        if operation == "poll":
            stack.enter_context(patch.object(MockMediaProvider, "poll", return_value=generated))
        if operation == "download":
            stack.enter_context(patch("contentflow.worker.validate_media_download_url"))
            stack.enter_context(patch("contentflow.worker.download_generated_media", return_value=generated.content))
        assert application.worker.run_once()
    with db.SessionLocal() as session:
        old = session.get(Asset, asset_id)
        assert old.status == "stale" and old.storage_uri is None
        allocation = session.scalar(select(StorageObjectAllocation).where(
            StorageObjectAllocation.owner_id == asset_id))
        assert allocation is not None and allocation.status == "staging"
        assert allocation.storage_uri == probe.stored.uri
        usage = session.get(WorkspaceStorageUsage, application.workspace_id)
        assert (usage.used_objects, usage.used_bytes) == (1, probe.stored.size_bytes)
        job = session.get(Job, job_id)
        assert job.status == "succeeded" and job.result_json["outcome"] == "superseded"
    assert probe.delegate.read(probe.stored.uri), "discarding stale output must not silently erase evidence"


@pytest.mark.parametrize("operation", ["generate", "poll", "search", "download"])
def test_stale_work_does_not_start_another_external_operation(application, operation):
    asset_id, job_id = prepare(application, operation)
    edit_in_another_thread(application, asset_id)
    with patch("contentflow.worker.build_media_provider") as media, \
         patch("contentflow.worker.build_image_search_provider") as search, \
         patch("contentflow.worker.download_generated_media") as download:
        assert application.worker.run_once()
    media.assert_not_called()
    search.assert_not_called()
    download.assert_not_called()
    with db.SessionLocal() as session:
        assert session.get(Asset, asset_id).status == "stale"
        assert session.get(Job, job_id).status == "succeeded"


@pytest.mark.parametrize("change", ["source", "task", "approval", "selection"])
def test_same_version_changes_are_rechecked_without_losing_selection(application, change):
    asset_id, job_id = prepare(application, "poll")
    generated = MockMediaProvider().generate(kind="image", prompt="test", metadata={}, idempotency_key="test")

    def mutate():
        with db.SessionLocal() as session:
            asset = session.get(Asset, asset_id)
            if change == "source":
                asset.provider = "manual-upload"
                asset.status = "ready"
                asset.storage_uri = "test-only:replacement"
            elif change == "task":
                asset.external_task_id = "new-task"
            elif change == "approval":
                session.get(ContentItem, asset.content_item_id).status = "needs_review"
            else:
                asset.metadata_json = {**asset.metadata_json, "selected": False}
            session.commit()

    def poll(_task):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(mutate).result(timeout=10)
        return generated

    with patch.object(MockMediaProvider, "poll", side_effect=poll):
        assert application.worker.run_once()
    with db.SessionLocal() as session:
        asset = session.get(Asset, asset_id)
        result = session.get(Job, job_id).result_json
        if change == "selection":
            assert asset.status == "ready" and asset.metadata_json["selected"] is False
            assert result["status"] == "ready"
        else:
            assert result["outcome"] == "superseded"
            assert not list(session.scalars(select(StorageObjectAllocation)))
            if change == "source":
                assert asset.provider == "manual-upload" and asset.storage_uri == "test-only:replacement"
            elif change == "task":
                assert asset.external_task_id == "new-task" and asset.status == "processing"
            else:
                assert session.get(ContentItem, asset.content_item_id).status == "needs_review"


def test_failure_after_replacement_preserves_the_new_asset_and_unknown_job(application):
    asset_id, job_id = prepare(application, "generate")

    def generate(**kwargs):
        def replace():
            with db.SessionLocal() as session:
                asset = session.get(Asset, asset_id)
                asset.status, asset.provider = "ready", "manual-upload"
                asset.storage_uri = "test-only:new-owner"
                session.commit()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(replace).result(timeout=10)
        raise MediaProviderError("TEST-ONLY unknown execution", retryable=False)

    with patch.object(MockMediaProvider, "generate", side_effect=generate):
        assert application.worker.run_once()
    with db.SessionLocal() as session:
        asset = session.get(Asset, asset_id)
        assert (asset.status, asset.storage_uri) == ("ready", "test-only:new-owner")
        assert session.get(Job, job_id).status in {"failed", "manual_review"}
