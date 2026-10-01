"""Bulk import of accounts and sites (CSV or JSON) so hundreds can be added at once."""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from . import models as m
from .publishers import REGISTRY
from .publishers.fields import missing_fields

ACCOUNT_META = {"label", "kind", "tag", "min_hours_between", "daily_limit", "notes", "enabled"}


def _rows(text: str) -> list[dict[str, Any]]:
    text = text.strip().lstrip("﻿")
    if not text:
        return []
    if text[0] in "[{":
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


def _list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v or "").replace("،", ",").replace("|", ",").split(",") if x.strip()]


def build_account(row: dict[str, Any]) -> m.Account:
    """One row → Account. Meta columns are account settings; every other column is a credential.

    JSON rows may also carry credentials under a nested "creds" object.
    """
    row = {str(k).strip(): v for k, v in row.items() if k is not None}
    kind = str(row.get("kind", "")).strip().lower()
    if kind not in REGISTRY:
        raise ValueError(f"unknown kind '{kind}'")
    creds = dict(row.get("creds") or {})
    creds.update({k: v for k, v in row.items() if k not in ACCOUNT_META | {"creds"} and v not in (None, "")})
    missing = missing_fields(kind, creds)
    if missing:
        raise ValueError(f"{kind}: missing {', '.join(missing)}")
    acc = m.Account(
        label=str(row.get("label") or f"{kind}-{creds.get('handle') or creds.get('username') or ''}").strip("-"),
        kind=kind,
        category=REGISTRY[kind].category,
        tag=str(row.get("tag") or "").strip(),
        notes=str(row.get("notes") or ""),
    )
    if str(row.get("min_hours_between", "")).strip():
        acc.min_hours_between = float(row["min_hours_between"])
    if str(row.get("daily_limit", "")).strip():
        acc.daily_limit = int(row["daily_limit"])
    if str(row.get("enabled", "")).strip().lower() in ("0", "false", "no"):
        acc.enabled = False
    acc.creds = creds
    return acc


def import_accounts(text: str, default_tag: str = "") -> tuple[int, list[str]]:
    """Returns (imported_count, errors)."""
    ok, errors = 0, []
    with m.session() as s:
        for i, row in enumerate(_rows(text), start=1):
            try:
                acc = build_account(row)
                if default_tag and not acc.tag:
                    acc.tag = default_tag
                s.add(acc)
                ok += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"row {i}: {e}")
        s.commit()
    return ok, errors


def build_site(row: dict[str, Any]) -> m.Site:
    row = {str(k).strip(): v for k, v in row.items() if k is not None}
    if not row.get("url") or not row.get("name"):
        raise ValueError("name and url are required")
    pages = row.get("pages") or []
    pages = [p if isinstance(p, dict) else {"url": p} for p in (pages if isinstance(pages, list) else _list(pages))]
    return m.Site(
        name=str(row["name"]).strip(), url=str(row["url"]).strip(),
        language=str(row.get("language") or "fa").strip(), niche=str(row.get("niche") or ""),
        description=str(row.get("description") or ""), keywords=_list(row.get("keywords")),
        anchors=_list(row.get("anchors")), pages=pages, image_url=str(row.get("image_url") or ""),
        style=str(row.get("style") or ""),
    )


def import_sites(text: str) -> tuple[int, list[str]]:
    ok, errors = 0, []
    with m.session() as s:
        for i, row in enumerate(_rows(text), start=1):
            try:
                s.add(build_site(row))
                ok += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"row {i}: {e}")
        s.commit()
    return ok, errors
