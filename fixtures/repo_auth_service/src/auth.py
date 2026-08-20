"""
Authentication Service Microservice.
Provides JWT token issuance and user identity verification.
"""

from typing import Optional, Dict
import hashlib
import time


class AuthService:
    """Handles password hashing, token validation, and session lifecycle."""

    def __init__(self, secret_key: str = "secret-jwt-key"):
        self.secret_key = secret_key
        self.user_database: Dict[str, Dict] = {
            "alice": {
                "id": "usr_101",
                "password_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "role": "admin",
            }
        }

    def verify_credentials(self, username: str, password_raw: str) -> bool:
        """Verifies candidate password against stored SHA-256 hash."""
        user = self.user_database.get(username)
        if not user:
            return False
        hashed = hashlib.sha256(password_raw.encode("utf-8")).hexdigest()
        return user["password_hash"] == hashed

    def generate_jwt_token(self, user_id: str, role: str) -> str:
        """Generates mock signed JWT token with expiry."""
        payload = f"{user_id}:{role}:{time.time() + 3600}"
        signature = hashlib.sha256(
            f"{payload}:{self.secret_key}".encode("utf-8")
        ).hexdigest()[:16]
        return f"mockjwt.{payload}.{signature}"

    def decode_and_validate_token(self, token: str) -> Optional[Dict[str, str]]:
        """Decodes token and validates signature."""
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "mockjwt":
            return None
        payload, signature = parts[1], parts[2]
        expected_sig = hashlib.sha256(
            f"{payload}:{self.secret_key}".encode("utf-8")
        ).hexdigest()[:16]
        if signature != expected_sig:
            return None
        user_id, role, exp = payload.split(":")
        if float(exp) < time.time():
            return None
        return {"user_id": user_id, "role": role}
