from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Iterable

from .entities import Asset, ChannelConnection, ContentItem, PublishJob
from .filenames import safe_filename
from .object_storage import ObjectStorage, StoredObject


SCRIPT_PACKAGE_SCHEMA_VERSION = 1
PLAYWRIGHT_VERSION = "1.62.0"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
PLATFORM_PORTALS = {
    "xiaohongshu": "https://creator.xiaohongshu.com/publish/publish",
    "douyin": "https://creator.douyin.com/creator-micro/content/upload",
    "wechat": "https://mp.weixin.qq.com/",
}


class ScriptPackageError(RuntimeError):
    """A script-assisted publish package could not be built safely."""


@dataclass(frozen=True, slots=True)
class ScriptPackage:
    data: bytes
    checksum: str
    manifest: dict[str, object]


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _markdown(content: ContentItem) -> bytes:
    tags = " ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags)
    sections = [f"# {content.title}", "", content.body]
    if tags:
        sections.extend(["", tags])
    if content.call_to_action:
        sections.extend(["", content.call_to_action])
    return ("\n".join(sections).rstrip() + "\n").encode("utf-8")


def _runner_source() -> bytes:
    source = r'''from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_PORTALS = {
    "xiaohongshu": "https://creator.xiaohongshu.com/publish/publish",
    "douyin": "https://creator.douyin.com/creator-micro/content/upload",
    "wechat": "https://mp.weixin.qq.com/",
}
SAFE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


def confined_package_path(relative: str) -> Path:
    target = (ROOT / relative).resolve()
    if ROOT not in target.parents or not target.is_file():
        raise RuntimeError(f"任务包文件缺失或路径越界: {relative}")
    return target


def verify_package() -> None:
    lines = (ROOT / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    seen: set[str] = set()
    for line in lines:
        expected, relative = line.split("  ", 1)
        if (
            len(expected) != 64
            or any(character not in "0123456789abcdef" for character in expected)
            or relative in seen
        ):
            raise RuntimeError("SHA256SUMS 格式无效或包含重复路径")
        seen.add(relative)
        target = confined_package_path(relative)
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"任务包完整性校验失败: {relative}")


def try_fill(page, selectors: list[str], value: str) -> bool:
    if not value:
        return True
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.fill(value)
                return True
        except Exception:
            continue
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ContentFlow 本机脚本辅助发布（不会自动点击最终发布按钮）"
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        help="独立浏览器配置目录；默认 ~/.contentflow/browser-profiles/<platform>/<channel_id>",
    )
    args = parser.parse_args()
    verify_package()
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    content = json.loads((ROOT / "content.json").read_text(encoding="utf-8"))
    platform = str(manifest.get("platform") or "")
    portal_url = EXPECTED_PORTALS.get(platform)
    if portal_url is None or manifest.get("portal_url") != portal_url:
        raise RuntimeError("任务包平台或官方入口不受支持")
    channel_id = str(manifest.get("channel_id") or "")
    if not 1 <= len(channel_id) <= 128 or any(
        character not in SAFE_ID_CHARS for character in channel_id
    ):
        raise RuntimeError("任务包渠道标识无效")
    profile_dir = (
        args.profile_dir
        or Path.home() / ".contentflow" / "browser-profiles" / platform / channel_id
    ).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SystemExit(
            "缺少 Playwright。请先执行: pip install -r requirements.txt && "
            "playwright install chromium"
        ) from error

    asset_paths = [
        str(confined_package_path(str(item["path"]))) for item in manifest["assets"]
    ]
    print("任务包已通过 SHA-256 校验。浏览器会使用独立的本机登录目录：")
    print(profile_dir)
    print("ContentFlow 不读取、导出或上传该目录中的 Cookie。")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir), headless=False
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(portal_url, wait_until="domcontentloaded")
        input("请在官方页面完成登录并进入发布编辑页，然后按 Enter 继续填充...")

        uploaded = False
        if asset_paths:
            for locator in (page.locator('input[type="file"]'),):
                try:
                    if locator.count():
                        locator.first.set_input_files(asset_paths)
                        uploaded = True
                        break
                except Exception:
                    continue

        title_ok = try_fill(
            page,
            [
                'input[placeholder*="标题"]',
                'textarea[placeholder*="标题"]',
                'input[aria-label*="标题"]',
            ],
            content["title"],
        )
        body_text = content["body"]
        if content["hashtags"]:
            body_text += "\n\n" + " ".join(
                f"#{tag.lstrip('#')}" for tag in content["hashtags"]
            )
        if content["call_to_action"]:
            body_text += "\n\n" + content["call_to_action"]
        body_ok = try_fill(
            page,
            [
                'textarea[placeholder*="正文"]',
                'textarea[placeholder*="内容"]',
                '[contenteditable="true"]',
                'textarea',
            ],
            body_text,
        )

        print(f"素材填充: {'成功' if uploaded or not asset_paths else '需人工上传'}")
        print(f"标题填充: {'成功' if title_ok else '需人工复制 content.json'}")
        print(f"正文填充: {'成功' if body_ok else '需人工复制 content.md'}")
        print("请逐项核对账号、标题、正文、素材、声明、可见范围和发布时间。")
        print("安全门禁：脚本不会查找或点击最终发布/提交按钮。")
        input("请在页面中自行决定发布或退出；完成后按 Enter 关闭辅助会话...")
        context.close()
    print("浏览器会话已关闭。请回到 ContentFlow 登记平台核对结果。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    return source.encode("utf-8")


def _read_assets(
    assets: Iterable[Asset],
    *,
    storage: ObjectStorage,
    max_total_bytes: int,
) -> tuple[list[dict[str, object]], dict[str, bytes]]:
    manifest_assets: list[dict[str, object]] = []
    files: dict[str, bytes] = {}
    total = 0
    for index, asset in enumerate(assets, start=1):
        if asset.status != "ready" or not asset.storage_uri:
            raise ScriptPackageError(f"素材未就绪或缺少存储地址: {asset.id}")
        remaining = max_total_bytes - total
        if remaining <= 0:
            raise ScriptPackageError("脚本发布包素材总大小超过允许上限")
        try:
            data = storage.read(asset.storage_uri, max_bytes=remaining)
        except (OSError, ValueError) as error:
            raise ScriptPackageError(f"无法安全读取素材 {asset.id}: {error}") from error
        total += len(data)
        original = asset.storage_uri.rsplit("/", 1)[-1] or f"asset-{index}"
        filename = f"assets/{index:03d}-{safe_filename(original)}"
        digest = hashlib.sha256(data).hexdigest()
        files[filename] = data
        manifest_assets.append(
            {
                "asset_id": asset.id,
                "kind": asset.kind,
                "mime_type": asset.mime_type or "application/octet-stream",
                "path": filename,
                "sha256": digest,
                "size_bytes": len(data),
            }
        )
    return manifest_assets, files


def build_script_package(
    *,
    publish_job: PublishJob,
    content: ContentItem,
    channel: ChannelConnection,
    assets: list[Asset],
    storage: ObjectStorage,
    max_total_bytes: int,
) -> ScriptPackage:
    if channel.platform not in PLATFORM_PORTALS:
        raise ScriptPackageError(f"脚本发布不支持平台: {channel.platform}")
    if content.status != "approved":
        raise ScriptPackageError("脚本发布只允许使用已人工审核内容")
    expected_version = int((publish_job.request_json or {}).get("content_version") or 0)
    if content.version != expected_version:
        raise ScriptPackageError("内容版本已变化，请重新审核并创建发布任务")
    if not assets:
        raise ScriptPackageError("当前内容版本没有可发布素材")

    manifest_assets, files = _read_assets(
        assets,
        storage=storage,
        max_total_bytes=max_total_bytes,
    )
    content_document = {
        "title": content.title,
        "body": content.body,
        "hashtags": list(content.hashtags),
        "call_to_action": content.call_to_action,
        "layout": dict(content.layout_json or {}),
    }
    manifest: dict[str, object] = {
        "schema_version": SCRIPT_PACKAGE_SCHEMA_VERSION,
        "publish_job_id": publish_job.id,
        "content_item_id": content.id,
        "content_version": content.version,
        "channel_id": channel.id,
        "channel_display_name": channel.display_name,
        "platform": channel.platform,
        "portal_url": PLATFORM_PORTALS[channel.platform],
        "scheduled_at": publish_job.scheduled_at.isoformat(),
        "delivery_mode": "script",
        "human_approved": True,
        "final_submission_requires_human": True,
        "assets": manifest_assets,
    }
    files.update(
        {
            "content.json": _json_bytes(content_document),
            "content.md": _markdown(content),
            "layout.json": _json_bytes(content.layout_json or {}),
            "manifest.json": _json_bytes(manifest),
            "publish_assistant.py": _runner_source(),
            "requirements.txt": f"playwright=={PLAYWRIGHT_VERSION}\n".encode("ascii"),
            "README.md": (
                "# ContentFlow 脚本辅助发布包\n\n"
                "1. 先核对 `manifest.json` 的内容版本、平台和账号名称。\n"
                "2. 创建独立 Python 环境，执行 `pip install -r requirements.txt` "
                "和 `playwright install chromium`。\n"
                "3. 执行 `python publish_assistant.py`。首次运行请在官方页面人工登录。\n"
                "4. 脚本只做完整性校验、打开官方入口和尽力填充，不会点击最终发布按钮。\n"
                "5. 人工核对并自行决定是否发布，然后回到 ContentFlow 登记结果。\n\n"
                "不要把浏览器 profile 或 Cookie 放回任务包，也不要把解压目录发给无关人员。\n"
            ).encode("utf-8"),
        }
    )
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(files.items())
    )
    files["SHA256SUMS"] = sums.encode("ascii")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    data = output.getvalue()
    return ScriptPackage(
        data=data,
        checksum=hashlib.sha256(data).hexdigest(),
        manifest=manifest,
    )


def store_script_package(
    *,
    package: ScriptPackage,
    publish_job: PublishJob,
    storage: ObjectStorage,
) -> StoredObject:
    return storage.put(
        workspace_id=publish_job.workspace_id,
        category="script-publish",
        filename=f"script-publish-{publish_job.id}.zip",
        stream=io.BytesIO(package.data),
        content_type="application/zip",
    )
