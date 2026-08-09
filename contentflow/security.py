from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from cryptography.fernet import Fernet, InvalidToken


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    decoded = base64.urlsafe_b64decode(f"{data}{padding}")
    if _b64url_encode(decoded) != data:
        raise ValueError("非规范 Base64URL 编码")
    return decoded


def hash_password(password: str, iterations: int = 600_000) -> str:
    if len(password) < 12:
        raise ValueError("密码至少需要 12 个字符")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64url_encode(salt)}${_b64url_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        expected = _b64url_decode(digest_raw)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _b64url_decode(salt_raw),
            int(iterations_raw),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(
    *,
    subject: str,
    workspace_id: str,
    role: str,
    session_id: str,
    secret_key: str,
    expires_minutes: int,
    issuer: str = "contentflow",
    audience: str = "contentflow-api",
) -> str:
    now = datetime.now(timezone.utc)
    now_epoch = int(now.timestamp())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": subject,
        "wid": workspace_id,
        "role": role,
        "sid": session_id,
        "jti": uuid.uuid4().hex,
        "iss": issuer,
        "aud": audience,
        "iat": now_epoch,
        "nbf": now_epoch,
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url_encode(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    signature = hmac.new(
        secret_key.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(
    token: str,
    secret_key: str,
    *,
    issuer: str = "contentflow",
    audience: str = "contentflow-api",
    leeway_seconds: int = 30,
) -> dict[str, Any]:
    try:
        header_raw, payload_raw, signature_raw = token.split(".", 2)
        signing_input = f"{header_raw}.{payload_raw}"
        expected = hmac.new(
            secret_key.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature_raw)):
            raise ValueError("令牌签名无效")
        header = json.loads(_b64url_decode(header_raw))
        payload = json.loads(_b64url_decode(payload_raw))
        if header.get("alg") != "HS256" or header.get("typ") != "JWT":
            raise ValueError("不支持的令牌头")
        required = {"sub", "wid", "sid", "jti", "iss", "aud", "iat", "nbf", "exp"}
        if not required.issubset(payload):
            raise ValueError("令牌缺少必要声明")
        if payload["iss"] != issuer or payload["aud"] != audience:
            raise ValueError("令牌签发方或受众无效")
        if not all(
            isinstance(payload[name], str) and payload[name]
            for name in ("sub", "wid", "sid", "jti")
        ):
            raise ValueError("令牌身份声明无效")
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        issued_at = int(payload["iat"])
        not_before = int(payload["nbf"])
        expires_at = int(payload["exp"])
        if issued_at > now_epoch + leeway_seconds:
            raise ValueError("令牌签发时间无效")
        if not_before > now_epoch + leeway_seconds:
            raise ValueError("令牌尚未生效")
        if expires_at <= now_epoch:
            raise ValueError("令牌已过期")
        return payload
    except (
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as error:
        raise ValueError(f"无效访问令牌: {error}") from error


def create_refresh_token(session_id: str) -> str:
    canonical = str(uuid.UUID(session_id))
    return f"{canonical}.{secrets.token_urlsafe(48)}"


def parse_refresh_token(token: str) -> str:
    try:
        session_id, secret = token.split(".", 1)
        canonical = str(uuid.UUID(session_id))
        if canonical != session_id or len(secret) < 43:
            raise ValueError("刷新令牌格式错误")
        return canonical
    except (ValueError, AttributeError) as error:
        raise ValueError("无效刷新令牌") from error


def hash_refresh_token(token: str, secret_key: str) -> str:
    return hmac.new(
        secret_key.encode("utf-8"),
        f"contentflow:refresh:{token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def hash_client_fingerprint(value: str | None, secret_key: str) -> str | None:
    if not value:
        return None
    return hmac.new(
        secret_key.encode("utf-8"),
        f"contentflow:client:{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def hash_rate_limit_key(scope: str, value: str, secret_key: str) -> str:
    normalized = value.strip().lower()
    return hmac.new(
        secret_key.encode("utf-8"),
        f"contentflow:rate-limit:{scope}:{normalized}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _fernet(secret_key: str) -> Fernet:
    material = hashlib.sha256(f"contentflow:{secret_key}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def encrypt_credentials(credentials: dict[str, Any], secret_key: str) -> str:
    plaintext = json.dumps(credentials, ensure_ascii=False).encode("utf-8")
    return _fernet(secret_key).encrypt(plaintext).decode("ascii")


def decrypt_credentials(ciphertext: str, secret_key: str) -> dict[str, Any]:
    try:
        plaintext = _fernet(secret_key).decrypt(ciphertext.encode("ascii"))
        decoded = json.loads(plaintext.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("凭据结构错误")
        return decoded
    except (InvalidToken, json.JSONDecodeError, ValueError) as error:
        raise ValueError("凭据解密失败") from error


def decrypt_credentials_with_keys(
    ciphertext: str,
    secret_keys: Iterable[str],
) -> dict[str, Any]:
    attempted = False
    for secret_key in secret_keys:
        attempted = True
        try:
            return decrypt_credentials(ciphertext, secret_key)
        except ValueError:
            continue
    if not attempted:
        raise ValueError("No credential decryption keys are configured")
