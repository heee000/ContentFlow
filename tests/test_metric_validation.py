from datetime import datetime, timezone
import json
from unittest.mock import Mock, patch

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from contentflow import db
from contentflow.entities import ContentItem, Job, MetricSnapshot
from contentflow.job_queue import enqueue_job
import test_worker_v2 as worker_tests


FIELDS = ("impressions", "clicks", "likes", "comments", "shares")


@pytest.fixture
def application():
    fixture = worker_tests.WorkerIntegrationTest()
    fixture.setUp()
    try:
        yield fixture, fixture._create_publish_fixture(status="published")
    finally:
        fixture.tearDown()


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", ["Infinity", "NaN", "100", True, -1, 1e100])
def test_invalid_counters_are_rejected_without_poisoning_summary(
    application, field, value
):
    fixture, identifiers = application
    response = fixture.client.post(
        "/api/v1/metrics/snapshots",
        headers=fixture.headers,
        json={"publish_job_id": identifiers["publish_job_id"], field: value},
    )
    assert response.status_code == 422
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(MetricSnapshot.id))) == 0
    assert (
        fixture.client.get(
            "/api/v1/metrics/summary", headers=fixture.headers
        ).status_code
        == 200
    )


def test_valid_counters_keep_their_values(application):
    fixture, identifiers = application
    captured = datetime.now(timezone.utc).isoformat()
    response = fixture.client.post(
        "/api/v1/metrics/snapshots",
        headers=fixture.headers,
        json={
            "publish_job_id": identifiers["publish_job_id"],
            "captured_at": captured,
            "impressions": 100,
            "clicks": 5,
            "likes": 4,
            "comments": 2,
            "shares": 1,
        },
    )
    assert response.status_code == 201
    summary = fixture.client.get(
        "/api/v1/metrics/summary", headers=fixture.headers
    ).json()
    assert summary["impressions"] == 100
    assert summary["engagements"] == 7
    assert summary["click_through_rate"] == 0.05


@pytest.mark.parametrize("raw_value", [float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("location", ["impressions", "raw"])
def test_nonstandard_json_nonfinite_values_are_safe_422(
    application, raw_value, location
):
    fixture, identifiers = application
    payload = {"publish_job_id": identifiers["publish_job_id"]}
    payload[location] = {"nested": [raw_value]} if location == "raw" else raw_value
    response = fixture.client.post(
        "/api/v1/metrics/snapshots",
        headers={**fixture.headers, "Content-Type": "application/json"},
        content=json.dumps(payload),
    )
    assert response.status_code == 422
    assert "Infinity" not in response.text
    assert "NaN" not in response.text


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), 1e100])
def test_database_rejects_invalid_nonquarantined_counters(application, field, value):
    fixture, identifiers = application
    with db.SessionLocal() as session:
        session.add(
            MetricSnapshot(
                workspace_id=fixture.workspace_id,
                publish_job_id=identifiers["publish_job_id"],
                **{field: value},
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        assert session.scalar(select(func.count(MetricSnapshot.id))) == 0


@pytest.mark.parametrize("value", [float("inf"), float("nan"), True, "100", -1, 1e100])
def test_collector_rejects_invalid_metrics_before_writing(application, value):
    fixture, identifiers = application
    with db.SessionLocal() as session:
        job = enqueue_job(
            session,
            job_type="metrics.pull",
            workspace_id=fixture.workspace_id,
            payload={"publish_job_id": identifiers["publish_job_id"]},
            idempotency_key="TEST-ONLY-metrics",
        )
        session.commit()
        job_id = job.id
    connector = Mock()
    connector.pull_metrics.return_value = {"impressions": value}
    with patch("contentflow.worker.build_connector", return_value=connector):
        assert fixture.worker.run_once() is True
    with db.SessionLocal() as session:
        job = session.get(Job, job_id)
        assert job.status != "succeeded"
        assert job.last_error
        assert session.scalar(select(func.count(MetricSnapshot.id))) == 0


def test_quarantine_summary_is_scoped_and_does_not_invent_advice(application):
    fixture, identifiers = application
    other = fixture._create_publish_fixture(status="published")
    with db.SessionLocal() as session:
        campaign_id = session.get(ContentItem, identifiers["content_id"]).campaign_id
        session.add_all(
            [
                MetricSnapshot(
                    workspace_id=fixture.workspace_id,
                    publish_job_id=identifiers["publish_job_id"],
                    impressions=100,
                ),
                MetricSnapshot(
                    workspace_id=fixture.workspace_id,
                    publish_job_id=other["publish_job_id"],
                    impressions=float("inf"),
                    validation_status="quarantined",
                ),
            ]
        )
        session.commit()
    summary = fixture.client.get(
        "/api/v1/metrics/summary", headers=fixture.headers
    ).json()
    assert summary["impressions"] == 100
    assert summary["excluded_snapshot_count"] == 1
    assert summary["data_complete"] is False
    assert summary["recommendations"] == []
    filtered = fixture.client.get(
        f"/api/v1/metrics/summary?campaign_id={campaign_id}", headers=fixture.headers
    ).json()
    assert filtered["excluded_snapshot_count"] == 0
    assert filtered["data_complete"] is True
    assert filtered["impressions"] == 100
    other_workspace = fixture.client.post(
        "/api/v1/auth/register",
        json={
            "email": "metrics-isolation@example.com",
            "password": "a-secure-password",
            "display_name": "Isolated",
            "workspace_name": "Other metrics workspace",
        },
    )
    assert other_workspace.status_code == 201
    isolated = fixture.client.get(
        "/api/v1/metrics/summary",
        headers={"Authorization": f"Bearer {other_workspace.json()['access_token']}"},
    ).json()
    assert isolated["excluded_snapshot_count"] == 0
    assert isolated["sample_count"] == 0


def test_legacy_upgrade_quarantines_without_changing_values_and_downgrades(application):
    fixture, identifiers = application
    with db.SessionLocal() as session:
        snapshot = MetricSnapshot(
            workspace_id=fixture.workspace_id,
            publish_job_id=identifiers["publish_job_id"],
            impressions=100,
            raw_json={"source": "TEST-ONLY-legacy", "retained": "original"},
        )
        session.add(snapshot)
        session.commit()
        snapshot_id = snapshot.id
    config = Config("alembic.ini")

    def migrate(operation, revision):
        with db.engine.begin() as connection:
            config.attributes["connection"] = connection
            operation(config, revision)

    migrate(command.downgrade, "b6c7d8e9f0a1")
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE metric_snapshots SET impressions = :value WHERE id = :id"),
            {"value": float("inf"), "id": snapshot_id},
        )
        original = dict(
            connection.execute(
                text("SELECT * FROM metric_snapshots WHERE id = :id"),
                {"id": snapshot_id},
            )
            .mappings()
            .one()
        )
    migrate(command.upgrade, "head")
    with db.SessionLocal() as session:
        preserved = session.get(MetricSnapshot, snapshot_id)
        assert preserved.impressions == float("inf")
        assert preserved.validation_status == "quarantined"
        assert preserved.raw_json == {
            "source": "TEST-ONLY-legacy",
            "retained": "original",
        }
    summary = fixture.client.get("/api/v1/metrics/summary", headers=fixture.headers)
    assert summary.status_code == 200
    assert summary.json()["excluded_snapshot_count"] == 1
    assert summary.json()["data_complete"] is False
    migrate(command.downgrade, "b6c7d8e9f0a1")
    with db.engine.connect() as connection:
        after = dict(
            connection.execute(
                text("SELECT * FROM metric_snapshots WHERE id = :id"),
                {"id": snapshot_id},
            )
            .mappings()
            .one()
        )
    assert after == original
