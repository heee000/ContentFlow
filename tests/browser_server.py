"""Disposable real API for browser acceptance. Never starts a Worker.

Reuses only synthetic integration fixtures; no test-only production endpoint,
existing .env, real account, platform credential or real database is needed.
"""

from io import BytesIO

from isolation import isolate_test_settings

from PIL import Image
from sqlalchemy import select
import uvicorn

def main():
    isolate_test_settings()
    from contentflow import db
    from contentflow.api import create_app
    from contentflow.entities import Asset, Campaign, ChannelConnection, ContentItem, Membership, User, Workspace
    from contentflow.object_storage import build_object_storage
    from contentflow.review_evidence import capture_review, local_review, resolve_brief
    from test_worker_v2 import WorkerIntegrationTest

    fixture = WorkerIntegrationTest()
    fixture.setUp()
    try:
        for name in [
            "gating",
            "lost-receipt",
            "stale",
            "storage-denied",
            "asset-failure",
            "scope",
        ]:
            identifiers = fixture._create_publish_fixture(status="cancelled")
            image = BytesIO()
            Image.new("RGB", (32, 32), "#247a62").save(image, format="PNG")
            image.seek(0)
            stored = build_object_storage(fixture.settings).put(
                workspace_id=fixture.workspace_id,
                category="assets",
                filename=f"{name}.png",
                stream=image,
                content_type="image/png",
            )
            with db.SessionLocal() as session:
                content = session.get(ContentItem, identifiers["content_id"])
                content.title = f"TEST-ONLY {name}"
                content.body = (
                    f"TEST-ONLY {name}: exact saved text.\nA & B <not executable>."
                )
                session.get(
                    ChannelConnection, identifiers["channel_id"]
                ).display_name = f"TEST-ONLY {name}"
                asset = session.scalar(
                    select(Asset).where(Asset.content_item_id == content.id)
                )
                asset.storage_uri, asset.size_bytes = stored.uri, stored.size_bytes
                asset.metadata_json = {
                    **asset.metadata_json,
                    "checksum": stored.checksum,
                }
                session.commit()
        for name in ["dirty", "save", "failed-save", "conflict", "navigation", "legacy",
            "busy", "lost-save-receipt", "workspace", "clean-approval", "invalid-layout"]:
            identifiers = fixture._create_publish_fixture(status="cancelled")
            with db.SessionLocal() as session:
                content = session.get(ContentItem, identifiers["content_id"])
                campaign = session.get(Campaign, content.campaign_id)
                campaign.name = f"TEST-ONLY review-{name} project"
                content.title = f"TEST-ONLY review-{name}"
                content.body = f"{campaign.product_name}，仅供内部测试。查看详情。"
                content.call_to_action = "查看详情"
                content.status = "needs_review"
                content.approved_at = content.approved_by = None
                campaign.brief = {"call_to_action": "查看详情", "must_include": ["仅供内部测试"]}
                content.review_json = {"model_review": {"passed": True, "risk_level": "low"}, "quality_score": 9}
                if name != "legacy":
                    content.review_json = local_review(content, resolve_brief(session, content), generated_model=content.review_json)
                    capture_review(session, content, "generated")
                # No browser acceptance path may enqueue a paid media call.
                asset = session.scalar(select(Asset).where(Asset.content_item_id == content.id))
                asset.provider = "manual"
                session.commit()
        with db.SessionLocal() as session:
            user = session.scalar(select(User).where(User.email == "worker@example.com"))
            workspace = Workspace(name="TEST-ONLY empty review workspace", slug="review-empty", created_by=user.id)
            session.add(workspace)
            session.flush()
            session.add(Membership(workspace_id=workspace.id, user_id=user.id, role="admin"))
            session.commit()
        settings = fixture.settings.model_copy(
            update={"cors_origins": ["http://127.0.0.1:18766"]}
        )
        uvicorn.run(
            create_app(settings), host="127.0.0.1", port=18765, log_level="warning"
        )
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    main()
