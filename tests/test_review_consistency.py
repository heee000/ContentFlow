"""Saved content, machine evidence and human decisions must describe one version."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from contentflow import db
from contentflow.entities import (
    Asset,
    AuditLog,
    Campaign,
    ContentItem,
    ContentReviewEvidence,
    ContentRevision,
    Job,
)
from contentflow.review_evidence import (
    capture_review,
    digest,
    local_review,
    resolve_brief,
)
import test_worker_v2 as worker_tests


@pytest.fixture
def application():
    fixture = worker_tests.WorkerIntegrationTest()
    fixture.setUp()
    try:
        identifiers = fixture._create_publish_fixture(status="cancelled")
        fixture.content_id = identifiers["content_id"]
        with db.SessionLocal() as session:
            content = session.get(ContentItem, fixture.content_id)
            content.status = "needs_review"
            content.approved_by = content.approved_at = None
            content.body = "测试产品，仅供内部测试。查看详情。"
            content.call_to_action = "查看详情"
            content.review_json = {
                "model_review": {"passed": True, "risk_level": "low"},
                "quality_score": 9,
            }
            campaign = session.get(Campaign, content.campaign_id)
            campaign.brief = {
                "call_to_action": "查看详情",
                "must_include": ["仅供内部测试"],
                "forbidden_phrases": ["绝对安全"],
            }
            session.commit()
        yield fixture
    finally:
        fixture.tearDown()


def content(application):
    response = application.client.get(
        f"/api/v1/contents/{application.content_id}", headers=application.headers
    )
    assert response.status_code == 200
    return response.json()


def test_legacy_unversioned_score_is_not_presented_as_current(application):
    result = content(application)
    assert result["review_json"]["model_review_status"] == "unversioned"
    assert result["review_json"]["model_review_current"] is False
    # Presentation does not mutate the original evidence in the database.
    with db.SessionLocal() as session:
        assert (
            session.get(ContentItem, application.content_id).review_json[
                "quality_score"
            ]
            == 9
        )


def test_edit_invalidates_ai_and_archives_original_evidence_without_model_call(
    application,
):
    previous = content(application)
    with patch(
        "contentflow.text_generation.build_text_provider",
        side_effect=AssertionError("No paid review on save"),
    ):
        edited = application.client.patch(
            f"/api/v1/contents/{application.content_id}",
            headers=application.headers,
            json={
                "expected_version": 1,
                "body": "测试产品，仅供内部测试。查看详情。绝对安全。",
            },
        )
    assert edited.status_code == 200, edited.text
    review = edited.json()["review_json"]
    assert review["content_version"] == 2
    assert review["model_review_status"] == "stale"
    assert review.get("quality_score") is None
    assert review.get("model_review") in (None, {})
    assert review["rule_review"]["checks"]["avoids_forbidden_phrases"] is False
    history = application.client.get(
        f"/api/v1/contents/{application.content_id}/review-evidence",
        headers=application.headers,
    )
    assert history.status_code == 200, history.text
    archived = next(row for row in history.json() if row["event"] == "superseded")
    assert archived["content_version"] == 1
    assert (
        archived["snapshot_json"]["quality_score"]
        == previous["review_json"]["quality_score"]
    )
    assert (
        archived["snapshot_json"]["model_review"]
        == previous["review_json"]["model_review"]
    )


@pytest.mark.parametrize(
    "ack,reason",
    [(False, "已核对当前稿件的所有事实依据"), (True, ""), (True, "短原因")],
)
def test_approval_of_unverified_evidence_requires_explicit_reason(
    application, ack, reason
):
    before = content(application)
    response = application.client.post(
        f"/api/v1/contents/{application.content_id}/review",
        headers=application.headers,
        json={
            "expected_version": 1,
            "decision": "approve",
            "reason": reason,
            "acknowledge_review_warnings": ack,
        },
    )
    assert response.status_code == 409, response.text
    after = content(application)
    assert after["status"] == before["status"]
    assert after["approved_by"] is None
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(Job.id))) == 0


def test_acknowledged_approval_rechecks_rules_and_pins_human_decision(application):
    with db.SessionLocal() as session:
        item = session.get(ContentItem, application.content_id)
        item.body += "绝对安全。"
        session.commit()
    response = application.client.post(
        f"/api/v1/contents/{application.content_id}/review",
        headers=application.headers,
        json={
            "expected_version": 1,
            "decision": "approve",
            "reason": "仅为隔离合成测试，明确记录人工承担的规则例外",
            "acknowledge_review_warnings": True,
        },
    )
    assert response.status_code == 200, response.text
    review = response.json()["review_json"]
    assert review["rule_review"]["passed"] is False
    assert review["human_content_version"] == 1
    assert review["human_content_sha256"] == review["content_sha256"]
    assert review["human_acknowledged_warnings"] is True
    evidence = application.client.get(
        f"/api/v1/contents/{application.content_id}/review-evidence",
        headers=application.headers,
    ).json()
    assert evidence[0]["event"] == "human_approve"


@pytest.mark.parametrize(
    "field", ["title", "body", "hashtags", "call_to_action", "layout_json"]
)
def test_explicit_null_patch_cannot_break_saved_review_state(application, field):
    before = deepcopy(content(application))
    response = application.client.patch(
        f"/api/v1/contents/{application.content_id}",
        headers=application.headers,
        json={"expected_version": 1, field: None},
    )
    assert response.status_code == 422, response.text
    assert content(application) == before


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_first_legacy_human_decision_preserves_original_evidence(application, decision):
    before = content(application)
    response = application.client.post(
        f"/api/v1/contents/{application.content_id}/review",
        headers=application.headers,
        json={
            "expected_version": 1,
            "decision": decision,
            "reason": "已逐项核对本版本并记录这次人工决定",
            "acknowledge_review_warnings": True,
        },
    )
    assert response.status_code == 200, response.text
    history = application.client.get(
        f"/api/v1/contents/{application.content_id}/review-evidence",
        headers=application.headers,
    ).json()
    original = next(row for row in history if row["event"] == "before_human_review")
    assert original["model_binding"] == "unversioned"
    assert original["snapshot_json"]["quality_score"] == 9
    assert (
        original["snapshot_json"]["model_review"]
        == before["review_json"]["model_review"]
    )
    assert "human_decision" not in original["snapshot_json"]


def test_no_change_save_does_not_revoke_approval_or_rebuild_assets(application):
    with db.SessionLocal() as session:
        item = session.get(ContentItem, application.content_id)
        item.status = "approved"
        session.commit()
    before = content(application)
    with db.SessionLocal() as session:
        counts = [
            session.scalar(select(func.count(entity.id)))
            for entity in (Asset, ContentRevision, ContentReviewEvidence)
        ]
    response = application.client.patch(
        f"/api/v1/contents/{application.content_id}",
        headers=application.headers,
        json={
            "expected_version": 1,
            **{
                key: before[key]
                for key in (
                    "title",
                    "body",
                    "hashtags",
                    "call_to_action",
                    "layout_json",
                )
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == before
    with db.SessionLocal() as session:
        assert counts == [
            session.scalar(select(func.count(entity.id)))
            for entity in (Asset, ContentRevision, ContentReviewEvidence)
        ]


@pytest.mark.parametrize(
    "field",
    ["name", "product_name", "objective", "audience", "platforms", "tone", "status"],
)
def test_campaign_explicit_null_is_rejected_before_database_write(application, field):
    campaign_id = content(application)["campaign_id"]
    before = application.client.get(
        f"/api/v1/campaigns/{campaign_id}", headers=application.headers
    ).json()
    response = application.client.patch(
        f"/api/v1/campaigns/{campaign_id}",
        headers=application.headers,
        json={field: None},
    )
    assert response.status_code == 422, response.text
    assert (
        application.client.get(
            f"/api/v1/campaigns/{campaign_id}", headers=application.headers
        ).json()
        == before
    )


def test_current_generated_review_allows_normal_approval_without_override(application):
    with db.SessionLocal() as session:
        item = session.get(ContentItem, application.content_id)
        item.review_json = local_review(
            item, resolve_brief(session, item), generated_model=item.review_json
        )
        capture_review(session, item, "generated")
        session.commit()
    response = application.client.post(
        f"/api/v1/contents/{application.content_id}/review",
        headers=application.headers,
        json={"expected_version": 1, "decision": "approve"},
    )
    assert response.status_code == 200, response.text
    review = response.json()["review_json"]
    assert review["rule_review"]["passed"] is True
    assert review["model_review_current"] is True
    assert review["human_warnings"] == []
    assert review["human_content_sha256"] == review["model_content_sha256"]
    with db.SessionLocal() as session:
        for row in session.scalars(select(ContentReviewEvidence)):
            assert row.snapshot_sha256 == digest(row.snapshot_json)
            assert row.content_version == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "新标题"),
        ("body", "不同正文"),
        ("hashtags", ["changed"]),
        ("call_to_action", "不同引导"),
        ("layout_json", {"changed": True}),
    ],
)
def test_model_hash_is_bound_to_every_editable_field(application, field, value):
    with db.SessionLocal() as session:
        item = session.get(ContentItem, application.content_id)
        item.review_json = local_review(
            item, resolve_brief(session, item), generated_model=item.review_json
        )
        setattr(
            item, field, value
        )  # Simulated out-of-band mutation without a version bump.
        session.commit()
    review = content(application)["review_json"]
    assert review["model_review_status"] == "stale"
    assert review["model_review_current"] is False


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_edit_and_review_versions_are_strict_integers(application, version):
    assert (
        application.client.patch(
            f"/api/v1/contents/{application.content_id}",
            headers=application.headers,
            json={"expected_version": version, "body": "不应保存"},
        ).status_code
        == 422
    )
    assert (
        application.client.post(
            f"/api/v1/contents/{application.content_id}/review",
            headers=application.headers,
            json={"expected_version": version, "decision": "reject"},
        ).status_code
        == 422
    )


def test_review_evidence_cannot_be_read_from_another_workspace(application):
    response = application.client.patch(
        f"/api/v1/contents/{application.content_id}",
        headers=application.headers,
        json={"expected_version": 1, "title": "建立可检索历史"},
    )
    assert response.status_code == 200, response.text
    switched = application.client.post(
        "/api/v1/auth/workspaces",
        headers=application.headers,
        json={"name": "Other evidence workspace"},
    )
    assert switched.status_code == 201, switched.text
    other = {"Authorization": f"Bearer {switched.json()['access_token']}"}
    history = application.client.get(
        f"/api/v1/contents/{application.content_id}/review-evidence", headers=other
    )
    assert history.status_code == 404


@pytest.mark.parametrize("action", ["edit", "approve"])
def test_review_and_edit_rollback_evidence_and_assets_together(application, action):
    with db.SessionLocal() as session:
        asset = session.scalar(
            select(Asset).where(Asset.content_item_id == application.content_id)
        )
        asset.status, asset.provider = "planned", "mock"
        session.commit()
        asset_id = asset.id
    before = content(application)
    entities = (Asset, ContentReviewEvidence, ContentRevision, Job, AuditLog)
    with db.SessionLocal() as session:
        counts = [session.scalar(select(func.count(entity.id))) for entity in entities]
    with patch(
        "contentflow.routers.contents.record_audit",
        side_effect=HTTPException(
            status_code=503, detail="TEST-ONLY audit unavailable"
        ),
    ):
        if action == "edit":
            response = application.client.patch(
                f"/api/v1/contents/{application.content_id}",
                headers=application.headers,
                json={"expected_version": 1, "title": "不能留下部分更新"},
            )
        else:
            response = application.client.post(
                f"/api/v1/contents/{application.content_id}/review",
                headers=application.headers,
                json={
                    "expected_version": 1,
                    "decision": "approve",
                    "reason": "隔离测试明确核验当前内容版本",
                    "acknowledge_review_warnings": True,
                },
            )
    assert response.status_code == 503, response.text
    assert content(application) == before
    with db.SessionLocal() as session:
        assert counts == [
            session.scalar(select(func.count(entity.id))) for entity in entities
        ]
        assert session.get(Asset, asset_id).status == "planned"


def test_failed_asset_admission_does_not_archive_a_human_decision(application):
    with db.SessionLocal() as session:
        asset = session.scalar(
            select(Asset).where(Asset.content_item_id == application.content_id)
        )
        asset.status, asset.provider = "failed", "mock"
        session.add(
            Job(
                workspace_id=application.workspace_id,
                job_type="asset.generate",
                payload_json={"asset_id": asset.id},
                status="manual_review",
                idempotency_key="review-unknown-asset",
            )
        )
        session.commit()
    before = content(application)
    response = application.client.post(
        f"/api/v1/contents/{application.content_id}/review",
        headers=application.headers,
        json={
            "expected_version": 1,
            "decision": "approve",
            "reason": "不能绕过未核实的素材生成结果",
            "acknowledge_review_warnings": True,
        },
    )
    assert response.status_code == 409, response.text
    assert content(application) == before
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(ContentReviewEvidence.id))) == 0
        assert session.scalar(select(Job.status)) == "manual_review"


def test_frozen_brief_and_history_pagination_survive_later_campaign_changes(
    application,
):
    for version in (1, 2, 3):
        response = application.client.patch(
            f"/api/v1/contents/{application.content_id}",
            headers=application.headers,
            json={
                "expected_version": version,
                "title": f"保留生成时的事实约束 {version}",
            },
        )
        assert response.status_code == 200, response.text
        if version == 1:
            with db.SessionLocal() as session:
                item = session.get(ContentItem, application.content_id)
                campaign = session.get(Campaign, item.campaign_id)
                campaign.brief = {
                    **campaign.brief,
                    "must_include": ["后改活动不能静默替换旧稿依据"],
                }
                session.commit()
        assert response.json()["review_json"]["brief_snapshot"]["must_include"] == [
            "仅供内部测试"
        ]
    path = f"/api/v1/contents/{application.content_id}/review-evidence"
    expected = application.client.get(path, headers=application.headers).json()
    actual = []
    cursor = None
    for _ in range(len(expected) + 1):
        page = application.client.get(
            path,
            headers=application.headers,
            params={"limit": 1, **({"cursor": cursor} if cursor else {})},
        )
        assert page.status_code == 200, page.text
        actual.extend(page.json())
        cursor = page.headers.get("X-ContentFlow-Next-Cursor")
        if cursor is None:
            break
    assert len(expected) == 6
    assert actual == expected
    assert len({row["id"] for row in actual}) == len(expected)


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_review_evidence_does_not_grant_decision_permission(application, role):
    response = application.client.post(
        "/api/v1/auth/register",
        json={
            "email": f"review-{role}@example.com",
            "password": "review-test-password",
            "display_name": role,
            "workspace_name": "Personal review test",
        },
    )
    assert response.status_code == 201, response.text
    added = application.client.post(
        "/api/v1/admin/members",
        headers=application.headers,
        json={"email": f"review-{role}@example.com", "role": role},
    )
    assert added.status_code == 201, added.text
    login = application.client.post(
        "/api/v1/auth/login",
        json={
            "email": f"review-{role}@example.com",
            "password": "review-test-password",
            "workspace_id": application.workspace_id,
        },
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    path = f"/api/v1/contents/{application.content_id}"
    assert (
        application.client.get(f"{path}/review-evidence", headers=headers).status_code
        == 200
    )
    before = content(application)
    decision = application.client.post(
        f"{path}/review",
        headers=headers,
        json={
            "expected_version": 1,
            "decision": "approve",
            "reason": "权限不足不能接受风险并通过内容",
            "acknowledge_review_warnings": True,
        },
    )
    assert decision.status_code == 403
    assert content(application) == before
