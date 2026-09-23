"""Closed, non-secret channel configuration used at input and execution time."""

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
)


OFFICIAL_ORIGINS = {
    "wechat": "https://api.weixin.qq.com",
    "douyin": "https://open.douyin.com",
}


class ChannelConfigurationError(ValueError):
    def __init__(self):
        super().__init__(
            "渠道配置无效：仅允许已定义字段、官方 HTTPS 地址和严格布尔发布开关；"
            "请由管理员检查配置后重新确认，不会自动改写旧配置"
        )


class _CommonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    connection_mode: Literal["connector", "script", "manual_export"] = "connector"
    script_confirmation_required: StrictInt = Field(default=1, ge=1, le=2)


class WechatConfig(_CommonConfig):
    api_base: str = OFFICIAL_ORIGINS["wechat"]
    author: str = Field(default="", max_length=120)
    auto_publish: StrictBool = False


class DouyinConfig(_CommonConfig):
    api_base: str = OFFICIAL_ORIGINS["douyin"]
    open_id: str = Field(default="", max_length=255)


class ExportConfig(_CommonConfig):
    export_format: Literal["zip"] = "zip"


def validate_channel_config(platform: str, value: object, *, incoming: bool = False):
    """Never reflect values/unknown field names or expose a ValidationError chain.

    Legacy official origins with one trailing slash remain supported. No tenant
    endpoint override, proxy, path, redirect or test-host exception is permitted.
    Tests inject a transport while retaining the production origin.
    """
    model = {
        "wechat": WechatConfig,
        "douyin": DouyinConfig,
        "xiaohongshu": ExportConfig,
    }.get(platform)
    if model is None or not isinstance(value, dict):
        raise ChannelConfigurationError()
    if incoming and {"connection_mode", "script_confirmation_required"} & value.keys():
        raise ChannelConfigurationError()
    try:
        config = model.model_validate(value)
    except ValidationError:
        raise ChannelConfigurationError() from None
    origin = OFFICIAL_ORIGINS.get(platform)
    if origin and config.api_base not in {origin, origin + "/"}:
        raise ChannelConfigurationError()
    return config
