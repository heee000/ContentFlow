from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contentflow.entities import Asset, ChannelConnection, ContentItem, PublishJob
from contentflow.object_storage import LocalObjectStorage
from contentflow.script_publishing import ScriptPackageError, build_script_package


def _fixture(tmp_path: Path):
    storage = LocalObjectStorage(tmp_path / "storage", max_upload_bytes=1024 * 1024)
    stored = storage.put(
        workspace_id="workspace-1",
        category="assets",
        filename="cover.png",
        stream=io.BytesIO(b"\x89PNG\r\nscript-publish-test"),
        content_type="image/png",
    )
    content = ContentItem(
        id="content-1",
        workspace_id="workspace-1",
        campaign_id="campaign-1",
        run_id="run-1",
        platform="xiaohongshu",
        title="周末路线",
        body="先核对路线，再决定发布。",
        hashtags=["北京", "周末"],
        call_to_action="收藏后出发",
        layout_json={"cards": ["路线", "提醒"]},
        status="approved",
        version=3,
    )
    channel = ChannelConnection(
        id="channel-1",
        workspace_id="workspace-1",
        platform="xiaohongshu",
        display_name="品牌官方账号",
        status="script_only",
        credential_ciphertext="must-never-enter-package",
        config_json={"connection_mode": "script"},
    )
    asset = Asset(
        id="asset-1",
        workspace_id="workspace-1",
        content_item_id=content.id,
        kind="image",
        provider="upload",
        status="ready",
        storage_uri=stored.uri,
        mime_type="image/png",
        size_bytes=stored.size_bytes,
        metadata_json={"content_version": content.version},
    )
    job = PublishJob(
        id="publish-1",
        workspace_id="workspace-1",
        content_item_id=content.id,
        channel_id=channel.id,
        status="scheduled",
        scheduled_at=datetime(2026, 8, 13, 12, tzinfo=timezone.utc),
        idempotency_key="script-publish-test",
        request_json={"content_version": content.version, "delivery_mode": "script"},
    )
    return storage, content, channel, asset, job


def test_script_package_is_reproducible_hashed_and_contains_no_credentials(tmp_path):
    storage, content, channel, asset, job = _fixture(tmp_path)

    first = build_script_package(
        publish_job=job,
        content=content,
        channel=channel,
        assets=[asset],
        storage=storage,
        max_total_bytes=1024 * 1024,
    )
    second = build_script_package(
        publish_job=job,
        content=content,
        channel=channel,
        assets=[asset],
        storage=storage,
        max_total_bytes=1024 * 1024,
    )

    assert first.data == second.data
    assert first.checksum == hashlib.sha256(first.data).hexdigest()
    assert b"must-never-enter-package" not in first.data
    with zipfile.ZipFile(io.BytesIO(first.data)) as archive:
        names = set(archive.namelist())
        assert {
            "SHA256SUMS",
            "README.md",
            "content.json",
            "content.md",
            "layout.json",
            "manifest.json",
            "publish_assistant.py",
            "requirements.txt",
        }.issubset(names)
        assert any(name.startswith("assets/") for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["content_version"] == 3
        assert manifest["final_submission_requires_human"] is True
        assert manifest["portal_url"].startswith("https://creator.xiaohongshu.com/")
        assert archive.read("requirements.txt") == b"playwright==1.62.0\n"
        runner = archive.read("publish_assistant.py").decode("utf-8")
        compile(runner, "publish_assistant.py", "exec")
        assert "from pathlib import Path" in runner
        assert "EXPECTED_PORTALS" in runner
        assert 'page.goto(portal_url' in runner
        assert "confined_package_path" in runner
        assert "final publish" not in runner.lower()
        assert ".click(" not in runner
        assert "最终发布/提交按钮" in runner
        sums = {}
        for line in archive.read("SHA256SUMS").decode("ascii").splitlines():
            digest, name = line.split("  ", 1)
            sums[name] = digest
        assert set(sums) == names - {"SHA256SUMS"}
        for name, digest in sums.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest


def test_script_package_fails_closed_on_version_change_or_asset_limit(tmp_path):
    storage, content, channel, asset, job = _fixture(tmp_path)
    content.version = 4
    with pytest.raises(ScriptPackageError, match="内容版本已变化"):
        build_script_package(
            publish_job=job,
            content=content,
            channel=channel,
            assets=[asset],
            storage=storage,
            max_total_bytes=1024 * 1024,
        )

    content.version = 3
    with pytest.raises(ScriptPackageError, match="超过允许上限|对象超过读取大小限制"):
        build_script_package(
            publish_job=job,
            content=content,
            channel=channel,
            assets=[asset],
            storage=storage,
            max_total_bytes=4,
        )


def test_script_package_rejects_unapproved_content_and_unknown_platform(tmp_path):
    storage, content, channel, asset, job = _fixture(tmp_path)
    content.status = "needs_review"
    with pytest.raises(ScriptPackageError, match="已人工审核"):
        build_script_package(
            publish_job=job,
            content=content,
            channel=channel,
            assets=[asset],
            storage=storage,
            max_total_bytes=1024 * 1024,
        )

    content.status = "approved"
    channel.platform = "unknown"
    with pytest.raises(ScriptPackageError, match="不支持平台"):
        build_script_package(
            publish_job=job,
            content=content,
            channel=channel,
            assets=[asset],
            storage=storage,
            max_total_bytes=1024 * 1024,
        )
