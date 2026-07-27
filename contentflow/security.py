from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

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
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
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
    subject: str,
    workspace_id: str,
    role: str,
    secret_key: str,
    expires_minutes: int,
) -> str:
    now = datetime.now(timezone.utc)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": subject,
        "wid": workspace_id,
        "role": role,
        "iat": int(now.timestamp()),
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


def decode_access_token(token: str, secret_key: str) -> dict[str, Any]:
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
        if header.get("alg") != "HS256":
            raise ValueError("不支持的令牌算法")
        if int(payload["exp"]) <= int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("令牌已过期")
        return payload
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        raise ValueError(f"无效访问令牌: {error}") from error


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
