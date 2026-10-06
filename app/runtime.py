"""Settings editable from the dashboard. Stored in the database and applied on top of .env values."""
from __future__ import annotations

from typing import Any

from sqlmodel import Field, SQLModel

from . import crypto
from . import models as m
from .config import settings


class AppSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""  # secrets are stored encrypted


# key -> (type, secret)
EDITABLE: dict[str, tuple[type, bool]] = {
    "llm_provider": (str, False),
    "llm_base_url": (str, False),
    "llm_api_key": (str, True),
    "llm_model": (str, False),
    "anthropic_api_key": (str, True),
    "anthropic_model": (str, False),
    "llm_timeout_sec": (float, False),
    "publish_proxy": (str, True),
    "v2ray_enabled": (bool, False),
    "v2ray_link": (str, True),
    "v2ray_for_llm": (bool, False),
    "public_url": (str, False),
    "dry_run": (bool, False),
}


def _cast(kind: type, raw: str) -> Any:
    if kind is bool:
        return raw.lower() in ("1", "true", "yes", "on")
    return kind(raw)


def apply() -> None:
    """Load overrides from the database into the live `settings` object."""
    with m.session() as s:
        rows = s.exec(m.select(AppSetting)).all()
    for row in rows:
        if row.key in EDITABLE:
            kind, secret = EDITABLE[row.key]
            raw = crypto.decrypt(row.value).get("v", "") if secret and row.value else row.value
            try:
                setattr(settings, row.key, _cast(kind, raw))
            except (TypeError, ValueError):
                pass


def save(values: dict[str, Any]) -> None:
    with m.session() as s:
        for key, val in values.items():
            if key not in EDITABLE:
                continue
            kind, secret = EDITABLE[key]
            raw = ("true" if val else "false") if kind is bool else str(val)
            row = s.get(AppSetting, key) or AppSetting(key=key)
            row.value = crypto.encrypt({"v": raw}) if secret and raw else raw
            s.add(row)
        s.commit()
    apply()
