from __future__ import annotations

import unittest

from contentflow.security import (
    create_access_token,
    decode_access_token,
    decrypt_credentials,
    encrypt_credentials,
    hash_password,
    verify_password,
)


class SecurityTest(unittest.TestCase):
    def test_password_hash_and_verify(self):
        encoded = hash_password("a-secure-password")
        self.assertTrue(verify_password("a-secure-password", encoded))
        self.assertFalse(verify_password("wrong-password", encoded))
        self.assertNotIn("a-secure-password", encoded)

    def test_signed_token_rejects_tampering(self):
        token = create_access_token(
            subject="user-1",
            workspace_id="workspace-1",
            role="admin",
            secret_key="test-secret",
            expires_minutes=5,
        )
        payload = decode_access_token(token, "test-secret")
        self.assertEqual(payload["sub"], "user-1")
        with self.assertRaises(ValueError):
            decode_access_token(f"{token[:-1]}x", "test-secret")

    def test_credentials_are_encrypted(self):
        credentials = {"access_token": "secret-token", "refresh_token": "refresh"}
        ciphertext = encrypt_credentials(credentials, "test-secret")
        self.assertNotIn("secret-token", ciphertext)
        self.assertEqual(
            decrypt_credentials(ciphertext, "test-secret"), credentials
        )


if __name__ == "__main__":
    unittest.main()

