"""Encrypted storage for user-owned cloud provider credentials."""

from __future__ import annotations

import base64
import hashlib
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from streetclip.accounts import Accounts


class KeyEncryptionNotConfigured(RuntimeError):
    pass


class ProviderKeyVault:
    def __init__(
        self, accounts: Accounts, secret: str, key_path: Path | None = None
    ) -> None:
        self.accounts = accounts
        if not secret and key_path is not None:
            secret = self._installation_secret(key_path)
        self._fernet = (
            Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))
            if secret
            else None
        )

    @staticmethod
    def _installation_secret(path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return path.read_text().strip()
        secret = secrets.token_urlsafe(48)
        path.write_text(secret)
        path.chmod(0o600)
        return secret

    def _cipher(self) -> Fernet:
        if self._fernet is None:
            raise KeyEncryptionNotConfigured(
                "STREETCLIP_KEY_ENCRYPTION_SECRET is required to store personal API keys"
            )
        return self._fernet

    def set(self, user_id: int, groq: str, anthropic: str) -> None:
        cipher = self._cipher()
        self.accounts.set_provider_keys(
            user_id,
            cipher.encrypt(groq.strip().encode()).decode(),
            cipher.encrypt(anthropic.strip().encode()).decode(),
        )

    def configured(self, user: dict) -> bool:
        return bool(user.get("groq_key_encrypted") and user.get("anthropic_key_encrypted"))

    def get(self, user: dict) -> tuple[str, str]:
        if not self.configured(user):
            raise ValueError("personal Groq and Anthropic keys are required")
        try:
            cipher = self._cipher()
            return (
                cipher.decrypt(user["groq_key_encrypted"].encode()).decode(),
                cipher.decrypt(user["anthropic_key_encrypted"].encode()).decode(),
            )
        except InvalidToken as exc:
            raise ValueError("stored provider keys cannot be decrypted") from exc
