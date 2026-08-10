from __future__ import annotations

import unittest

from contentflow.audit import redact
from contentflow.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decrypt_credentials,
    decrypt_credentials_with_keys,
    encrypt_credentials,
    hash_password,
    hash_rate_limit_key,
    hash_refresh_token,
    parse_refresh_token,
    verify_password,
)
from contentflow.settings import Settings


def production_settings(**overrides) -> Settings:
    values = {
        "environment": "production",
        "secret_key": "s" * 32,
        "credential_encryption_key": "c" * 32,
        "storage_backend": "s3",
        "s3_endpoint_url": "https://objects.example.com",
        "s3_access_key": "access",
        "s3_secret_key": "secret",
        "allow_mock_providers": True,
        "require_governed_prompts": True,
        "metrics_enabled": True,
        "metrics_bearer_token": "m" * 32,
    }
    values.update(overrides)
    return Settings(**values)


class SecurityTest(unittest.TestCase):
    def test_password_hash_and_verify(self):
        encoded = hash_password("a-secure-password")
        self.assertTrue(verify_password("a-secure-password", encoded))
        self.assertFalse(verify_password("wrong-password", encoded))
        self.assertNotIn("a-secure-password", encoded)

    def test_signed_token_rejects_tampering(self):
        session_id = "11111111-1111-1111-1111-111111111111"
        token = create_access_token(
            subject="user-1",
            workspace_id="workspace-1",
            role="admin",
            session_id=session_id,
            secret_key="test-secret",
            expires_minutes=5,
        )
        payload = decode_access_token(token, "test-secret")
        self.assertEqual(payload["sub"], "user-1")
        self.assertEqual(payload["sid"], session_id)
        self.assertEqual(payload["iss"], "contentflow")
        self.assertEqual(payload["aud"], "contentflow-api")
        self.assertTrue(payload["jti"])
        with self.assertRaises(ValueError):
            decode_access_token(f"{token[:-1]}x", "test-secret")
        with self.assertRaises(ValueError):
            decode_access_token(token, "test-secret", issuer="another-service")
        with self.assertRaises(ValueError):
            decode_access_token(token, "test-secret", audience="another-api")

    def test_refresh_token_round_trip_and_hashing(self):
        session_id = "22222222-2222-2222-2222-222222222222"
        token = create_refresh_token(session_id)
        self.assertEqual(parse_refresh_token(token), session_id)
        digest = hash_refresh_token(token, "test-secret")
        self.assertEqual(digest, hash_refresh_token(token, "test-secret"))
        self.assertNotIn(token, digest)
        with self.assertRaises(ValueError):
            parse_refresh_token(f"{session_id}.short")

    def test_rate_limit_keys_are_scoped_hmac_digests(self):
        email_key = hash_rate_limit_key(
            "login-account",
            "Owner@Example.com",
            "test-secret",
        )
        self.assertEqual(len(email_key), 64)
        self.assertNotIn("owner@example.com", email_key)
        self.assertEqual(
            email_key,
            hash_rate_limit_key(
                "login-account",
                "owner@example.com",
                "test-secret",
            ),
        )
        self.assertNotEqual(
            email_key,
            hash_rate_limit_key("login-ip", "owner@example.com", "test-secret"),
        )

    def test_credentials_are_encrypted(self):
        credentials = {"access_token": "secret-token", "refresh_token": "refresh"}
        ciphertext = encrypt_credentials(credentials, "test-secret")
        self.assertNotIn("secret-token", ciphertext)
        self.assertEqual(decrypt_credentials(ciphertext, "test-secret"), credentials)

    def test_password_hash_rejects_short_new_password(self):
        with self.assertRaisesRegex(ValueError, "12"):
            hash_password("short-pass")

    def test_credential_decryption_supports_key_rotation(self):
        credentials = {"app_secret": "secret-value"}
        ciphertext = encrypt_credentials(credentials, "old-encryption-key")
        self.assertEqual(
            decrypt_credentials_with_keys(
                ciphertext,
                ("new-encryption-key", "old-encryption-key"),
            ),
            credentials,
        )

    def test_audit_redacts_sensitive_key_variants(self):
        metadata = redact(
            {
                "app_secret": "secret",
                "modelApiKey": "key",
                "authorization_header": "Bearer token",
                "nested": {
                    "password_hash": "hash",
                    "refreshToken": "refresh",
                    "idempotency_key": "safe-key",
                    "token_count": 42,
                },
            }
        )
        self.assertEqual(metadata["app_secret"], "***")
        self.assertEqual(metadata["modelApiKey"], "***")
        self.assertEqual(metadata["authorization_header"], "***")
        self.assertEqual(metadata["nested"]["password_hash"], "***")
        self.assertEqual(metadata["nested"]["refreshToken"], "***")
        self.assertEqual(metadata["nested"]["idempotency_key"], "safe-key")
        self.assertEqual(metadata["nested"]["token_count"], 42)


class RuntimeSettingsTest(unittest.TestCase):
    def test_production_requires_long_secret(self):
        settings = Settings(
            environment="production",
            secret_key="too-short",
        )
        with self.assertRaisesRegex(ValueError, "at least 32"):
            settings.validate_runtime()

    def test_production_requires_postgresql(self):
        settings = Settings(
            environment="production",
            secret_key="x" * 32,
            database_url="sqlite:///contentflow.db",
        )
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            settings.validate_runtime()

    def test_production_rejects_wildcard_cors(self):
        settings = Settings(
            environment="production",
            secret_key="x" * 32,
            cors_origins=["*"],
        )
        with self.assertRaisesRegex(ValueError, "wildcard CORS"):
            settings.validate_runtime()

    def test_development_can_explicitly_use_sqlite(self):
        settings = Settings(database_url="sqlite:///contentflow-test.db")
        settings.validate_runtime()

    def test_production_requires_auth_rate_limiting(self):
        settings = production_settings(auth_rate_limit_enabled=False)
        with self.assertRaisesRegex(ValueError, "rate limiting"):
            settings.validate_runtime()

    def test_production_requires_s3_storage(self):
        settings = production_settings(storage_backend="local")
        with self.assertRaisesRegex(ValueError, "S3-compatible"):
            settings.validate_runtime()

    def test_production_requires_governed_prompts(self):
        settings = production_settings(require_governed_prompts=False)
        with self.assertRaisesRegex(ValueError, "REQUIRE_GOVERNED_PROMPTS"):
            settings.validate_runtime()

    def test_production_requires_metrics(self):
        settings = production_settings(metrics_enabled=False)
        with self.assertRaisesRegex(ValueError, "METRICS_ENABLED"):
            settings.validate_runtime()

    def test_enabled_metrics_require_a_separate_long_token(self):
        missing = production_settings(metrics_bearer_token=None)
        with self.assertRaisesRegex(ValueError, "METRICS_BEARER_TOKEN"):
            missing.validate_runtime()
        reused = production_settings(metrics_bearer_token="s" * 32)
        with self.assertRaisesRegex(ValueError, "must be separate"):
            reused.validate_runtime()

    def test_production_requires_separate_credential_key(self):
        settings = production_settings(credential_encryption_key=None)
        with self.assertRaisesRegex(ValueError, "CREDENTIAL_ENCRYPTION_KEY"):
            settings.validate_runtime()

    def test_production_rejects_implicit_mock_providers(self):
        settings = production_settings(allow_mock_providers=False)
        with self.assertRaisesRegex(ValueError, "ALLOW_MOCK_PROVIDERS"):
            settings.validate_runtime()

    def test_real_provider_configuration_is_validated_at_startup(self):
        settings = production_settings(text_provider="openai-compatible")
        with self.assertRaisesRegex(ValueError, "MODEL_API_BASE"):
            settings.validate_runtime()

    def test_production_accepts_explicit_offline_validation_mode(self):
        production_settings().validate_runtime()

    def test_unknown_provider_is_rejected(self):
        settings = Settings(
            database_url="sqlite:///contentflow-test.db",
            text_provider="unknown",
        )
        with self.assertRaisesRegex(ValueError, "Unsupported providers"):
            settings.validate_runtime()

    def test_worker_stale_threshold_must_exceed_two_heartbeat_intervals(self):
        with self.assertRaisesRegex(
            ValueError,
            "worker_stale_seconds",
        ):
            Settings(worker_heartbeat_seconds=10, worker_stale_seconds=20)


if __name__ == "__main__":
    unittest.main()
