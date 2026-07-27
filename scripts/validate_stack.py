from __future__ import annotations

import argparse
import json
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Callable

import httpx


def wait_for(
    description: str,
    fetch: Callable[[], Any],
    predicate: Callable[[Any], bool],
    timeout: float,
) -> Any:
    deadline = time.monotonic() + timeout
    last_value: Any = None
    while time.monotonic() < deadline:
        last_value = fetch()
        if predicate(last_value):
            return last_value
        time.sleep(0.5)
    raise RuntimeError(
        f"Timed out waiting for {description}. Last value: {last_value!r}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a running ContentFlow deployment end to end."
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="ContentFlow API origin without /api/v1.",
    )
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()

    api = f"{args.base_url.rstrip('/')}/api/v1"
    unique = uuid.uuid4().hex[:10]

    with httpx.Client(timeout=20, trust_env=False) as client:
        ready = client.get(f"{args.base_url.rstrip('/')}/health/ready")
        ready.raise_for_status()

        register = client.post(
            f"{api}/auth/register",
            json={
                "email": f"stack-{unique}@example.com",
                "password": f"validation-{unique}",
                "display_name": "Stack Validator",
                "workspace_name": f"Validation {unique}",
            },
        )
        register.raise_for_status()
        headers = {
            "Authorization": f"Bearer {register.json()['access_token']}"
        }
        primary_workspace_id = register.json()["workspace_id"]

        secondary = client.post(
            f"{api}/auth/register",
            json={
                "email": f"member-{unique}@example.com",
                "password": f"validation-member-{unique}",
                "display_name": "Validation Member",
                "workspace_name": f"Member Workspace {unique}",
            },
        )
        secondary.raise_for_status()
        member = client.post(
            f"{api}/admin/members",
            headers=headers,
            json={
                "email": f"member-{unique}@example.com",
                "role": "viewer",
            },
        )
        member.raise_for_status()
        member_login = client.post(
            f"{api}/auth/login",
            json={
                "email": f"member-{unique}@example.com",
                "password": f"validation-member-{unique}",
                "workspace_id": primary_workspace_id,
            },
        )
        member_login.raise_for_status()
        member_headers = {
            "Authorization": f"Bearer {member_login.json()['access_token']}"
        }
        forbidden_admin = client.get(
            f"{api}/admin/members",
            headers=member_headers,
        )
        if forbidden_admin.status_code != 403:
            raise RuntimeError("Viewer unexpectedly accessed administration.")

        extra_workspace = client.post(
            f"{api}/auth/workspaces",
            headers=headers,
            json={"name": f"Secondary Validation {unique}"},
        )
        extra_workspace.raise_for_status()
        workspace_list = client.get(
            f"{api}/auth/workspaces",
            headers=headers,
        )
        workspace_list.raise_for_status()
        if len(workspace_list.json()) != 2:
            raise RuntimeError("Workspace isolation/switch list is incomplete.")

        document = client.post(
            f"{api}/knowledge/documents",
            headers=headers,
            files={
                "file": (
                    "verified-facts.md",
                    (
                        "# 已核验产品事实\n\n"
                        "ContentFlow 支持知识检索、内容审核、素材生成、"
                        "定时发布与效果回收。所有发布内容必须经过人工审核。\n"
                    ).encode("utf-8"),
                    "text/markdown",
                )
            },
        )
        document.raise_for_status()
        document_id = document.json()["id"]

        indexed = wait_for(
            "knowledge indexing",
            lambda: next(
                item
                for item in client.get(
                    f"{api}/knowledge/documents", headers=headers
                ).raise_for_status().json()
                if item["id"] == document_id
            ),
            lambda item: item["status"] in {"indexed", "failed"},
            args.timeout,
        )
        if indexed["status"] != "indexed":
            raise RuntimeError(f"Knowledge indexing failed: {indexed}")

        campaign = client.post(
            f"{api}/campaigns",
            headers=headers,
            json={
                "name": "北京周末路线内容计划",
                "product_name": "ContentFlow",
                "objective": "生成一篇事实可追溯并可审核发布的周末路线内容",
                "audience": "在北京生活的年轻用户",
                "platforms": ["xiaohongshu"],
                "tone": "清晰、具体、不过度承诺",
                "city": "北京",
                "must_include": ["人工审核", "知识检索"],
                "forbidden_phrases": ["百分之百有效"],
                "call_to_action": "审核后导出并发布内容",
                "product_facts": ["发布前必须完成人工审核"],
            },
        )
        campaign.raise_for_status()
        campaign_id = campaign.json()["id"]

        archived_campaign = client.patch(
            f"{api}/campaigns/{campaign_id}",
            headers=headers,
            json={
                "name": "北京周末路线与排版计划",
                "status": "archived",
            },
        )
        archived_campaign.raise_for_status()
        archived_run = client.post(
            f"{api}/campaigns/{campaign_id}/runs",
            headers=headers,
            json={},
        )
        if archived_run.status_code != 409:
            raise RuntimeError("Archived campaign unexpectedly accepted a run.")
        restored_campaign = client.patch(
            f"{api}/campaigns/{campaign_id}",
            headers=headers,
            json={"status": "active"},
        )
        restored_campaign.raise_for_status()

        workflow_run = client.post(
            f"{api}/campaigns/{campaign_id}/runs",
            headers=headers,
            json={},
        )
        workflow_run.raise_for_status()
        run_id = workflow_run.json()["id"]

        completed_run = wait_for(
            "content workflow",
            lambda: client.get(
                f"{api}/runs/{run_id}", headers=headers
            ).raise_for_status().json(),
            lambda item: item["status"] in {"awaiting_review", "failed"},
            args.timeout,
        )
        if completed_run["status"] != "awaiting_review":
            raise RuntimeError(f"Workflow failed: {completed_run}")

        content = next(
            item
            for item in client.get(
                f"{api}/contents", headers=headers
            ).raise_for_status().json()
            if item["run_id"] == run_id
        )
        content_id = content["id"]
        layout = content.get("layout_json") or {}
        if not layout.get("cover_title") or len(layout.get("cards") or []) < 3:
            raise RuntimeError(
                f"Structured Xiaohongshu layout was not generated: {layout}"
            )

        edited = client.patch(
            f"{api}/contents/{content_id}",
            headers=headers,
            json={
                "title": f"{content['title']}｜人工校订",
                "body": (
                    f"{content['body']}\n\n"
                    "发布说明：本文已由运营人员核对知识来源与平台格式。"
                ),
            },
        )
        edited.raise_for_status()
        if edited.json()["version"] != 2:
            raise RuntimeError("Content revision was not recorded.")

        revisions = wait_for(
            "content revision commit",
            lambda: client.get(
                f"{api}/contents/{content_id}/revisions", headers=headers
            ).raise_for_status().json(),
            lambda items: any(item["version"] == 2 for item in items),
            args.timeout,
        )
        if {item["version"] for item in revisions} != {1, 2}:
            raise RuntimeError(f"Unexpected content revisions: {revisions}")
        if not all((item.get("layout_json") or {}).get("cover_title") for item in revisions):
            raise RuntimeError("Structured layout was not preserved in revisions.")

        approved = client.post(
            f"{api}/contents/{content_id}/review",
            headers=headers,
            json={
                "decision": "approve",
                "reason": "已核对知识来源、文案与平台格式",
            },
        )
        approved.raise_for_status()

        assets = wait_for(
            "asset generation",
            lambda: client.get(
                f"{api}/assets",
                headers=headers,
                params={"content_item_id": content_id},
            ).raise_for_status().json(),
            lambda items: any(item["status"] == "ready" for item in items),
            args.timeout,
        )
        ready_asset = next(item for item in assets if item["status"] == "ready")
        asset_download = client.get(
            f"{api}/assets/{ready_asset['id']}/download", headers=headers
        )
        asset_download.raise_for_status()
        if not asset_download.content:
            raise RuntimeError("Generated asset is empty.")

        channel = client.post(
            f"{api}/channels",
            headers=headers,
            json={
                "platform": "xiaohongshu",
                "display_name": "小红书审核后导出",
                "credentials": {},
                "config": {"export_format": "zip"},
            },
        )
        channel.raise_for_status()

        cancellable = client.post(
            f"{api}/publishing/jobs",
            headers=headers,
            json={
                "content_item_id": content_id,
                "channel_id": channel.json()["id"],
                "scheduled_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
            },
        )
        cancellable.raise_for_status()
        cancelled = client.post(
            f"{api}/publishing/jobs/{cancellable.json()['id']}/cancel",
            headers=headers,
        )
        cancelled.raise_for_status()
        if cancelled.json()["status"] != "cancelled":
            raise RuntimeError("Publish schedule cancellation was not persisted.")

        scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        publish_job = client.post(
            f"{api}/publishing/jobs",
            headers=headers,
            json={
                "content_item_id": content_id,
                "channel_id": channel.json()["id"],
                "scheduled_at": scheduled_at.isoformat(),
            },
        )
        publish_job.raise_for_status()
        publish_job_id = publish_job.json()["id"]

        exported = wait_for(
            "scheduled export",
            lambda: next(
                item
                for item in client.get(
                    f"{api}/publishing/jobs", headers=headers
                ).raise_for_status().json()
                if item["id"] == publish_job_id
            ),
            lambda item: item["status"] in {"exported", "failed"},
            args.timeout,
        )
        if exported["status"] != "exported":
            raise RuntimeError(f"Publishing failed: {exported}")

        artifact = client.get(
            f"{api}/publishing/jobs/{publish_job_id}/artifact",
            headers=headers,
        )
        artifact.raise_for_status()
        with zipfile.ZipFile(BytesIO(artifact.content)) as archive:
            names = archive.namelist()
            required = {"content.md", "manifest.json", "layout.json"}
            if not required.issubset(names):
                raise RuntimeError(f"Export archive is incomplete: {names}")
            exported_layout = json.loads(archive.read("layout.json"))
            if exported_layout.get("cover_title") != layout["cover_title"]:
                raise RuntimeError("Exported layout differs from approved content.")
            if not any(name.startswith("assets/") for name in names):
                raise RuntimeError("Export archive contains no generated asset.")

        metric = client.post(
            f"{api}/metrics/snapshots",
            headers=headers,
            json={
                "publish_job_id": publish_job_id,
                "impressions": 1200,
                "clicks": 84,
                "likes": 126,
                "comments": 18,
                "shares": 12,
                "raw": {"source": "stack-validation"},
            },
        )
        metric.raise_for_status()
        metric_summary = client.get(
            f"{api}/metrics/summary", headers=headers
        )
        metric_summary.raise_for_status()
        if metric_summary.json()["impressions"] < 1200:
            raise RuntimeError("Metric summary did not include the snapshot.")

        dashboard = client.get(
            f"{api}/dashboard/summary", headers=headers
        )
        dashboard.raise_for_status()
        audit_logs = client.get(
            f"{api}/admin/audit-logs", headers=headers
        )
        audit_logs.raise_for_status()
        if not any(item["action"] == "member.add" for item in audit_logs.json()):
            raise RuntimeError("Administration audit event is missing.")

    print(
        "ContentFlow stack validation passed: "
        "auth -> workspaces/RBAC/audit -> campaign lifecycle -> knowledge -> "
        "RAG workflow/layout -> revision -> review -> "
        "asset -> schedule/cancel/export -> artifact -> metrics -> dashboard"
    )


if __name__ == "__main__":
    main()
