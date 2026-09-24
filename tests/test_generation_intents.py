"""Generation intent acceptance: real API/DB, no provider or worker calls."""

from datetime import timedelta
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import func, select

from contentflow import db
from contentflow.entities import AuditLog, Campaign, GenerationIntent, Job, WorkflowRun
import test_api_v2 as api_tests


@pytest.fixture
def application():
    fixture = api_tests.ApiV2Test()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def campaign(application, name="TEST-ONLY generation"):
    response = application.client.post(
        "/api/v1/campaigns",
        headers=application.headers,
        json={
            "name": name,
            "product_name": "TEST",
            "objective": "Verify generation intent",
            "audience": "Tests",
            "platforms": ["wechat"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def submit(application, item, key, **changes):
    return application.client.post(
        f"/api/v1/campaigns/{item['id']}/runs",
        headers={**application.headers, "Idempotency-Key": key},
        json={"expected_campaign_updated_at": item["updated_at"], **changes},
    )


def test_exact_replay_returns_one_run_job_and_audit(application):
    item = campaign(application)
    key = str(uuid.uuid4())
    first = submit(application, item, key)
    second = submit(application, item, key)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    receipt = application.client.get(
        f"/api/v1/generation-intents/{key}", headers=application.headers
    )
    assert receipt.status_code == 200
    assert receipt.json()["id"] == first.json()["id"]
    assert (
        application.client.get(f"/api/v1/generation-intents/{key}").status_code == 401
    )
    with db.SessionLocal() as session:
        for model in (WorkflowRun, Job):
            assert session.scalar(select(func.count()).select_from(model)) == 1
        assert (
            session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "workflow.enqueue"
                )
            )
            == 1
        )


def test_receipt_survives_campaign_edit_archive_and_governance_changes(application):
    item = campaign(application)
    key = str(uuid.uuid4())
    first = submit(application, item, key)
    assert first.status_code == 202
    assert (
        application.client.patch(
            f"/api/v1/campaigns/{item['id']}",
            headers=application.headers,
            json={"status": "archived", "objective": "Changed after acceptance"},
        ).status_code
        == 200
    )
    with patch(
        "contentflow.routers.runs.resolve_active_prompt_set",
        side_effect=AssertionError("Replay must not revalidate mutable governance"),
    ):
        replay = submit(application, item, key)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["request_json"] == first.json()["request_json"]


@pytest.mark.parametrize(
    "change", [{"provider": "mock"}, {"regenerate_platforms": ["wechat"]}]
)
def test_key_cannot_be_reused_for_different_payload(application, change):
    item = campaign(application)
    key = str(uuid.uuid4())
    assert submit(application, item, key).status_code == 202
    assert submit(application, item, key, **change).status_code == 409
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(WorkflowRun.id))) == 1


def test_key_cannot_be_reused_for_another_campaign(application):
    first, second = campaign(application), campaign(application, "TEST-ONLY other")
    key = str(uuid.uuid4())
    assert submit(application, first, key).status_code == 202
    assert submit(application, second, key).status_code == 409


def test_new_key_is_an_explicit_new_generation(application):
    item = campaign(application)
    first = submit(application, item, str(uuid.uuid4()))
    second = submit(application, item, str(uuid.uuid4()))
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] != second.json()["id"]


def test_unaccepted_old_brief_is_not_silently_generated(application):
    item = campaign(application)
    with db.SessionLocal() as session:
        row = session.get(Campaign, item["id"])
        row.updated_at += timedelta(seconds=1)
        row.objective = "Changed before first acceptance"
        session.commit()
    response = submit(application, item, str(uuid.uuid4()))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "generation_precondition_failed"
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(WorkflowRun.id))) == 0
        assert session.scalar(select(func.count(Job.id))) == 0
        assert session.scalar(select(func.count()).select_from(GenerationIntent)) == 0


@pytest.mark.parametrize("key", [None, "", "short", "bad key with spaces", "x" * 129])
def test_missing_or_invalid_operation_key_cannot_create_work(application, key):
    item = campaign(application)
    headers = dict(application.headers)
    if key is not None:
        headers["Idempotency-Key"] = key
    response = application.client.post(
        f"/api/v1/campaigns/{item['id']}/runs",
        headers=headers,
        json={"expected_campaign_updated_at": item["updated_at"]},
    )
    assert response.status_code == 422, response.text
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(WorkflowRun.id))) == 0


def test_audit_failure_rolls_back_run_and_receipt_atomically(application, caplog):
    item = campaign(application)
    key = str(uuid.uuid4())
    with patch(
        "contentflow.routers.runs.record_audit",
        side_effect=RuntimeError("TEST-ONLY audit failure"),
    ):
        response = submit(application, item, key)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    assert "TEST-ONLY audit failure" not in response.text + caplog.text
    assert "RuntimeError" in caplog.text
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(WorkflowRun.id))) == 0
        assert session.scalar(select(func.count(Job.id))) == 0
        assert session.scalar(select(func.count()).select_from(GenerationIntent)) == 0
        assert session.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "workflow.enqueue")) == 0
    assert submit(application, item, key).status_code == 202
    with db.SessionLocal() as session:
        for model in (WorkflowRun, Job, GenerationIntent):
            assert session.scalar(select(func.count()).select_from(model)) == 1
        assert session.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "workflow.enqueue")) == 1


@pytest.mark.parametrize("targets", [["douyin"], ["wechat", "wechat"]])
def test_invalid_target_platforms_are_rejected_before_planning(application, targets):
    item = campaign(application)
    response = submit(
        application, item, str(uuid.uuid4()), regenerate_platforms=targets
    )
    assert response.status_code == 422, response.text
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(Job.id))) == 0


@pytest.mark.parametrize("body", [{}, {"expected_campaign_updated_at": None}])
def test_brief_precondition_is_required(application, body):
    item = campaign(application)
    response = application.client.post(
        f"/api/v1/campaigns/{item['id']}/runs",
        headers={**application.headers, "Idempotency-Key": str(uuid.uuid4())},
        json=body,
    )
    assert response.status_code == 422


def test_receipt_key_is_workspace_scoped(application):
    item = campaign(application)
    key = str(uuid.uuid4())
    first = submit(application, item, key)
    registered = application.client.post(
        "/api/v1/auth/register",
        json={
            "email": "another-test@example.com",
            "password": "test-password",
            "display_name": "TEST",
            "workspace_name": "Another TEST workspace",
        },
    )
    assert registered.status_code == 201
    application.headers = {
        "Authorization": f"Bearer {registered.json()['access_token']}"
    }
    assert (
        application.client.get(
            f"/api/v1/generation-intents/{key}", headers=application.headers
        ).status_code
        == 404
    )
    assert submit(application, item, key).status_code == 404
    another = campaign(application)
    second = submit(application, another, key)
    assert second.status_code == 202
    assert second.json()["id"] != first.json()["id"]
    assert (
        application.client.get(
            f"/api/v1/generation-intents/{key}", headers=application.headers
        ).json()["id"]
        == second.json()["id"]
    )


def test_worker_rejects_legacy_empty_intersection_before_provider_calls(application):
    from contentflow.workflow_service import execute_workflow_run
    from contentflow.settings import get_settings

    item = campaign(application)
    response = submit(application, item, str(uuid.uuid4()))
    with db.SessionLocal() as session:
        run = session.get(WorkflowRun, response.json()["id"])
        run.request_json = {**run.request_json, "regenerate_platforms": ["douyin"]}
        session.commit()
        with patch(
            "contentflow.workflow_service.build_text_provider",
            side_effect=AssertionError("No provider before target validation"),
        ):
            with pytest.raises(ValueError, match="平台子集"):
                execute_workflow_run(
                    session,
                    run,
                    application.client.app.dependency_overrides[get_settings](),
                )


def test_receipt_insert_conflict_discards_only_the_unaccepted_run(application):
    from contentflow.routers.runs import accepted_run

    item = campaign(application)
    key = str(uuid.uuid4())
    first = submit(application, item, key)
    assert first.status_code == 202
    # Deterministically exercise an initial lookup miss followed by an actual
    # SQLite unique violation; PostgreSQL concurrency is tested separately.
    calls = 0

    def miss_twice(*args):
        nonlocal calls
        calls += 1
        return None if calls <= 2 else accepted_run(*args)

    with patch("contentflow.routers.runs.accepted_run", side_effect=miss_twice):
        replay = submit(application, item, key)
    assert replay.status_code == 202
    assert replay.json()["id"] == first.json()["id"]
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(WorkflowRun.id))) == 1
        assert session.scalar(select(func.count(Job.id))) == 1
        assert session.scalar(select(func.count()).select_from(GenerationIntent)) == 1
