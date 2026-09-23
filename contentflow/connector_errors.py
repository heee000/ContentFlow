"""Safe connector diagnostics. Never persist upstream messages or request URLs."""

from contextvars import ContextVar
import logging

import httpx


CONNECTOR_JOB_TYPES = frozenset(
    {"connector.test", "publish.dispatch", "publish.reconcile", "metrics.pull"}
)
STAGES = frozenset(
    {
        "authenticate",
        "validate_assets",
        "read_assets",
        "upload_media",
        "create_draft",
        "submit_publish",
        "query_publish",
        "test_connection",
        "create_video",
        "pull_metrics",
        "platform_operation",
    }
)
CODES = frozenset(
    {
        "operation_failed",
        "http_error",
        "network_error",
        "invalid_response",
        "platform_rejected",
        "missing_credentials",
        "assets_unavailable",
    }
)
_sensitive_http = ContextVar("contentflow_sensitive_connector_http", default=False)


class _SensitiveHTTPFilter(logging.Filter):
    def filter(self, record):
        # httpx's INFO request log includes the full WeChat/Douyin token URL.
        # Suppress only this synchronous sensitive call, not unrelated traffic.
        return not _sensitive_http.get()


logging.getLogger("httpx").addFilter(_SensitiveHTTPFilter())


class ConnectorPublishError(RuntimeError):
    """Bounded metadata and an explicit external-write recovery boundary.

    The legacy message argument is deliberately not trusted. A caller cannot
    make an upstream body, token or exception text safe merely by wrapping it.
    """

    def __init__(
        self,
        message: str = "",
        *,
        stage: str,
        retry_safe: bool,
        invalidate_channel: bool = False,
        code: str = "operation_failed",
        http_status: int | None = None,
        platform_code: int | None = None,
    ):
        self.stage = (
            stage
            if isinstance(stage, str) and stage in STAGES
            else "platform_operation"
        )
        self.code = (
            code if isinstance(code, str) and code in CODES else "operation_failed"
        )
        self.retry_safe = retry_safe is True
        self.invalidate_channel = invalidate_channel is True
        self.http_status = (
            http_status
            if type(http_status) is int and 100 <= http_status <= 599
            else None
        )
        self.platform_code = (
            platform_code
            if type(platform_code) is int and abs(platform_code) < 10**10
            else None
        )
        fields = [f"stage={self.stage}", f"code={self.code}"]
        if self.http_status is not None:
            fields.append(f"HTTP={self.http_status}")
        if self.platform_code is not None:
            fields.append(f"platform_code={self.platform_code}")
        message = "平台操作失败（" + ", ".join(fields) + "）"
        if self.platform_code == 40164:
            message += "；请检查公众号服务器出口 IP 白名单"
        super().__init__(message)


def connector_request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    stage: str,
    retry_safe: bool = False,
    invalidate_channel: bool = False,
    **kwargs,
) -> dict:
    def failure(code, **metadata):
        return ConnectorPublishError(
            stage=stage,
            retry_safe=retry_safe,
            invalidate_channel=invalidate_channel,
            code=code,
            **metadata,
        )

    guard = _sensitive_http.set(True)
    try:
        try:
            response = client.request(method, url, follow_redirects=False, **kwargs)
        except httpx.HTTPError:
            raise failure("network_error") from None
        if not 200 <= response.status_code < 300:
            raise failure("http_error", http_status=response.status_code)
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            raise failure("invalid_response") from None
        if not isinstance(body, dict):
            raise failure("invalid_response")
        # Both platforms report machine codes separately from unsafe messages.
        codes = [body.get("errcode", 0)]
        for name in ("data", "extra"):
            part = body.get(name)
            if isinstance(part, dict):
                codes.append(part.get("error_code", 0))
        for code in codes:
            if type(code) is not int:
                raise failure("invalid_response")
            if code:
                raise failure("platform_rejected", platform_code=code)
        return body
    finally:
        _sensitive_http.reset(guard)


def safe_connector_failure(error: Exception) -> str:
    if type(error) is ConnectorPublishError:
        # Reconstruct from bounded fields; even modified .args cannot leak.
        return str(
            ConnectorPublishError(
                stage=error.stage,
                retry_safe=error.retry_safe,
                code=error.code,
                http_status=error.http_status,
                platform_code=error.platform_code,
            )
        )
    return "平台操作失败（code=unexpected_error）；请使用任务编号定位，原始异常已隔离"


def connector_diagnostic(error: Exception) -> dict:
    if type(error) is not ConnectorPublishError:
        return {}
    return {
        "stage": error.stage,
        "code": error.code,
        "http_status": error.http_status,
        "platform_code": error.platform_code,
    }


def public_connector_error(
    *, retry_safe: bool = False, uncertain: bool = False, diagnostic: object = None
) -> str:
    if retry_safe:
        message = "外部写入前失败，可在修复渠道或素材后安全重试"
    elif uncertain:
        message = "平台操作结果不确定，禁止自动重试；请先到平台核对并完成人工对账"
    else:
        message = "平台任务失败；请根据任务状态处理，不要直接重复发布"
    if (
        isinstance(diagnostic, dict)
        and isinstance(diagnostic.get("stage"), str)
        and diagnostic["stage"] in STAGES
        and isinstance(diagnostic.get("code"), str)
        and diagnostic["code"] in CODES
    ):
        message += "；" + str(
            ConnectorPublishError(
                stage=diagnostic["stage"],
                code=diagnostic["code"],
                retry_safe=retry_safe,
                http_status=diagnostic.get("http_status"),
                platform_code=diagnostic.get("platform_code"),
            )
        )
    return message
