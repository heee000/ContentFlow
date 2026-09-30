"""Review covers the text that can leave in an API or a publishing package."""

import pytest
from sqlalchemy import select

from contentflow import db
from contentflow.entities import ContentItem, ChannelConnection, User
from contentflow.models import CampaignBrief
from contentflow.review import RuleReviewer
from contentflow.publish_manifest import PublishManifestConflict, load_release_inputs
from contentflow.review_evidence import RULESET_VERSION, content_fingerprint
import test_review_consistency as review_tests

application = review_tests.application


@pytest.mark.parametrize("extra", [
    {"hashtags": ["绝对安全"]},
    {"call_to_action": "立即购买，绝对安全"},
    {"layout": {"shots": [{"narration": "绝对安全"}]}},
    {"layout": {"sections": [{"heading": "绝对安全"}]}},
    {"layout": {"绝对安全": "普通文字"}},
])
def test_forbidden_phrase_in_other_published_fields_is_not_missed(extra):
    brief = CampaignBrief.from_dict({"campaign_name": "TEST", "product_name": "测试产品",
        "goal": "TEST", "audience": "TEST", "platforms": ["wechat"],
        "call_to_action": "查看详情", "forbidden_phrases": ["绝对安全"]})
    result = RuleReviewer().review("wechat", {
        "title": "测试标题", "body": "测试产品。查看详情。", **extra}, brief)
    assert not result.passed
    assert result.checks["avoids_forbidden_phrases"] is False


@pytest.mark.parametrize("updates", [
    {"hashtags": ["绝对安全"]}, {"call_to_action": "绝对安全"},
    {"layout_json": {"shots": [{"narration": "绝对安全"}]}},
])
def test_save_and_human_approval_show_new_field_failure(application, updates):
    endpoint = f"/api/v1/contents/{application.content_id}"
    response = application.client.patch(endpoint, headers=application.headers,
        json={"expected_version": 1, **updates})
    assert response.status_code == 200, response.text
    review = response.json()["review_json"]
    assert review["ruleset_version"] == RULESET_VERSION
    assert review["rule_review"]["checks"]["avoids_forbidden_phrases"] is False
    denied = application.client.post(endpoint + "/review", headers=application.headers,
        json={"expected_version": 2, "decision": "approve"})
    assert denied.status_code == 409
    accepted = application.client.post(endpoint + "/review", headers=application.headers,
        json={"expected_version": 2, "decision": "approve", "acknowledge_review_warnings": True,
            "reason": "TEST-ONLY 人工已经核对这些具体字段与例外原因"})
    assert accepted.status_code == 200, accepted.text
    with db.SessionLocal() as session:
        from contentflow.review_evidence import require_publication_field_review
        item = session.get(ContentItem, application.content_id)
        require_publication_field_review(session, item)
        item.review_json = {**item.review_json, "ruleset_version": "deterministic-v1"}
        with pytest.raises(PublishManifestConflict):
            require_publication_field_review(session, item)


def test_legacy_approval_cannot_release_newly_detected_forbidden_tags(application):
    with db.SessionLocal() as session:
        reviewer_id = session.scalar(select(User.id))
        item = session.get(ContentItem, application.content_id)
        item.hashtags = ["绝对安全"]
        item.status = "approved"
        item.approved_by = reviewer_id
        item.review_json = {"ruleset_version": "deterministic-v1", "human_decision": "approve",
            "human_acknowledged_warnings": True, "human_reason": "TEST-ONLY old review",
            "human_content_version": item.version, "human_content_sha256": content_fingerprint(item),
            "human_reviewer_id": item.approved_by}
        channel_id = session.scalar(select(ChannelConnection.id))
        session.commit()
        with pytest.raises(PublishManifestConflict) as error:
            load_release_inputs(session, workspace_id=application.workspace_id,
                content_id=item.id, channel_id=channel_id, settings=application.settings)
        assert error.value.code == "publication_review_required"
