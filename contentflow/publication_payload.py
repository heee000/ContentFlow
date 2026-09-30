"""Pure renderers shared by the confirmation preview and delivery adapters."""

from html import escape

from .entities import ChannelConnection, ContentItem
from .channel_config import validate_channel_config


def wechat_article(content: ContentItem, channel: ChannelConnection) -> dict:
    config = validate_channel_config(
        "wechat", channel.config_json if channel.config_json is not None else {}
    )
    return {
        "title": content.title,
        "author": config.author,
        "digest": content.body[:120],
        # ContentFlow stores plain text, not trusted HTML. Preview and delivery
        # must agree on escaping; arbitrary model/user HTML must not execute.
        "content": "<p>" + escape(content.body).replace("\n", "</p><p>") + "</p>",
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }


def douyin_text(content: ContentItem) -> str:
    return "\n".join(
        [
            content.title,
            content.body,
            " ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags),
        ]
    )[:2200]


def export_markdown(content: ContentItem) -> str:
    return "\n".join(
        [
            f"# {content.title}",
            "",
            content.body,
            "",
            " ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags),
            "",
            content.call_to_action,
        ]
    )


def script_markdown(content: ContentItem) -> str:
    tags = " ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags)
    sections = [f"# {content.title}", "", content.body]
    if tags:
        sections.extend(["", tags])
    if content.call_to_action:
        sections.extend(["", content.call_to_action])
    return "\n".join(sections).rstrip() + "\n"


def preview_document(
    content: ContentItem, channel: ChannelConnection, mode: str
) -> dict:
    if mode == "script":
        return {
            "format": "markdown",
            "text": script_markdown(content),
            "behavior": "只生成本机脚本包，平台最终提交仍需人工点击",
            "fields": {},
        }
    if mode == "manual_export":
        return {
            "format": "markdown",
            "text": export_markdown(content),
            "behavior": "只生成投放包，不向平台自动发布",
            "fields": {},
        }
    if channel.platform == "wechat":
        config = validate_channel_config(
            "wechat", channel.config_json if channel.config_json is not None else {}
        )
        article = wechat_article(content, channel)
        return {
            "format": "wechat_plain_text",
            "text": content.body,
            "behavior": "创建草稿并提交公开发布"
            if config.auto_publish is True
            else "只创建公众号草稿，不公开发布",
            "fields": article,
        }
    if channel.platform == "douyin":
        return {
            "format": "plain_text",
            "text": douyin_text(content),
            "behavior": "上传视频并创建平台作品；文案按适配器上限截取为最多 2200 字符",
            "fields": {},
        }
    raise ValueError("Unsupported publication preview")
