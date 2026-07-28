from pathlib import Path

import pytest

from streetclip.accounts import Accounts
from streetclip.provider_keys import KeyEncryptionNotConfigured, ProviderKeyVault


def test_provider_keys_are_encrypted_and_round_trip(tmp_path: Path):
    accounts = Accounts(tmp_path / "db.sqlite")
    user_id = accounts.create_user("user@x.com", "hash")
    vault = ProviderKeyVault(accounts, "installation-secret")

    vault.set(user_id, "groq-secret", "anthropic-secret")

    row = accounts.get_user(user_id)
    assert "groq-secret" not in row["groq_key_encrypted"]
    assert "anthropic-secret" not in row["anthropic_key_encrypted"]
    assert vault.get(row) == ("groq-secret", "anthropic-secret")


def test_provider_keys_refuse_plaintext_storage_without_a_secret(tmp_path: Path):
    accounts = Accounts(tmp_path / "db.sqlite")
    user_id = accounts.create_user("user@x.com", "hash")

    with pytest.raises(KeyEncryptionNotConfigured):
        ProviderKeyVault(accounts, "").set(user_id, "groq", "anthropic")
