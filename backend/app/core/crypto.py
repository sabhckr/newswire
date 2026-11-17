"""Symmetric encryption for stored credentials (Fernet, key derived from the app secret)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet(key_material: str | None = None) -> Fernet:
    material = key_material or get_settings().secret_key
    digest = hashlib.sha256(("newswire-credentials:" + material).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def fernet_for_backups(key: str) -> Fernet:
    return _fernet("backup:" + key)
