"""Disposable real API for browser acceptance. Never starts a Worker.

Reuses only synthetic integration fixtures; no test-only production endpoint,
existing .env, real account, platform credential or real database is needed.
"""

from io import BytesIO

from PIL import Image
from sqlalchemy import select
import uvicorn

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import Asset, ChannelConnection, ContentItem
from contentflow.object_storage import build_object_storage
from test_worker_v2 import WorkerIntegrationTest


def main():
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
