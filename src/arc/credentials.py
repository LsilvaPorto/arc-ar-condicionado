"""Credenciais do hOn guardadas no Keychain do macOS (via keyring)."""

from __future__ import annotations

import keyring

SERVICE = "arc-hon"
EMAIL_KEY = "__email__"


def save(email: str, password: str) -> None:
    keyring.set_password(SERVICE, EMAIL_KEY, email)
    keyring.set_password(SERVICE, email, password)


def load() -> tuple[str, str] | None:
    email = keyring.get_password(SERVICE, EMAIL_KEY)
    if not email:
        return None
    password = keyring.get_password(SERVICE, email)
    return (email, password) if password else None


def clear() -> None:
    creds = load()
    for key in [EMAIL_KEY, creds[0] if creds else None]:
        if key:
            try:
                keyring.delete_password(SERVICE, key)
            except keyring.errors.PasswordDeleteError:
                pass

