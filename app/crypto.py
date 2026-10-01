"""Encrypt account credentials at rest (Fernet / AES-128-CBC + HMAC)."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet

from .config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    raw = settings.secret_key
    if not raw:
        f = settings.data_path / "secret.key"
        if not f.exists():
            f.write_text(secrets.token_urlsafe(32))
            f.chmod(0o600)
        raw = f.read_text().strip()
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def encrypt(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()


def decrypt(token: str) -> dict[str, Any]:
    if not token:
        return {}
    return json.loads(_fernet().decrypt(token.encode()))
