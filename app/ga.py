"""Google Analytics 4: send clicks (Measurement Protocol), create the "nexa_click" key event
automatically (Admin API) and read the campaign report (Data API) with a service account.
All requests go through v2ray / the publish proxy (Google is not reachable from Iran)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
import uuid
from typing import Any

import httpx
import jwt

from . import v2ray
from .config import settings

log = logging.getLogger(__name__)
EVENT = "nexa_click"
SCOPES = "https://www.googleapis.com/auth/analytics.edit https://www.googleapis.com/auth/analytics.readonly"
_token: dict[str, Any] = {"value": "", "exp": 0.0, "key": ""}


class GAError(RuntimeError):
    pass


def _client() -> httpx.Client:
    return httpx.Client(timeout=30, proxy=v2ray.publish_proxy() or None)


# ---------------------------------------------------------------- clicks → GA4 (Measurement Protocol)


def send_click(params: dict[str, Any]) -> None:
    """Fire-and-forget: never slows down the visitor's redirect."""
    if not (settings.ga_measurement_id and settings.ga_api_secret):
        return

    def go() -> None:
        try:
            with _client() as c:
                c.post("https://www.google-analytics.com/mp/collect",
                       params={"measurement_id": settings.ga_measurement_id, "api_secret": settings.ga_api_secret},
                       json={"client_id": str(uuid.uuid4()), "events": [{"name": EVENT, "params": params}]})
        except httpx.HTTPError as e:
            log.warning("GA click not sent: %s", e)
    threading.Thread(target=go, daemon=True).start()


# ---------------------------------------------------------------- service account → access token


def _service_account() -> dict[str, Any]:
    try:
        sa = json.loads(settings.ga_service_account)
        return {"email": sa["client_email"], "key": sa["private_key"], "token_uri": sa.get("token_uri")
                or "https://oauth2.googleapis.com/token"}
    except (ValueError, KeyError, TypeError) as e:
        raise GAError("فایل JSON سرویس‌اکانت گوگل نامعتبر است") from e


def access_token() -> str:
    sa = _service_account()
    if _token["value"] and _token["key"] == sa["email"] and _token["exp"] > time.time() + 60:
        return _token["value"]
    now = int(time.time())
    assertion = jwt.encode({"iss": sa["email"], "scope": SCOPES, "aud": sa["token_uri"], "iat": now, "exp": now + 3600},
                           sa["key"], algorithm="RS256")
    with _client() as c:
        r = c.post(sa["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                          "assertion": assertion})
    if r.status_code != 200:
        raise GAError(f"ورود سرویس‌اکانت به گوگل نشد: {r.text[:200]}")
    _token.update(value=r.json()["access_token"], exp=now + 3500, key=sa["email"])
    return _token["value"]


def _api(method: str, url: str, **kw: Any) -> httpx.Response:
    with _client() as c:
        return c.request(method, url, headers={"Authorization": f"Bearer {access_token()}"}, **kw)


# ---------------------------------------------------------------- the goal (key event) and the report


def ensure_key_event() -> str:
    """Create the "nexa_click" key event (= GA4 goal) on the property. Safe to call repeatedly."""
    pid = settings.ga_property_id.strip().removeprefix("properties/")
    if not pid:
        raise GAError("شناسه‌ی Property گوگل آنالیتیکس خالی است")
    r = _api("POST", f"https://analyticsadmin.googleapis.com/v1beta/properties/{pid}/keyEvents",
             json={"eventName": EVENT, "countingMethod": "ONCE_PER_EVENT"})
    if r.status_code in (200, 201):
        return "هدف «nexa_click» در گوگل آنالیتیکس ساخته شد ✓"
    if r.status_code == 409 or "ALREADY_EXISTS" in r.text:
        return "هدف «nexa_click» از قبل در گوگل آنالیتیکس هست ✓"
    if r.status_code == 403:
        raise GAError("سرویس‌اکانت به این Property دسترسی ندارد: در GA4 ← Admin ← Property access management "
                      "ایمیل سرویس‌اکانت را با نقش Editor اضافه کن")
    raise GAError(f"ساخت هدف نشد: HTTP {r.status_code} {r.text[:200]}")


def campaign_report(days: int = 30) -> list[dict[str, Any]]:
    """Sessions / users / key events per campaign and source for the last `days` days."""
    pid = settings.ga_property_id.strip().removeprefix("properties/")
    start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    r = _api("POST", f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport", json={
        "dateRanges": [{"startDate": start, "endDate": "today"}],
        "dimensions": [{"name": "sessionCampaignName"}, {"name": "sessionSource"}, {"name": "sessionMedium"}],
        "metrics": [{"name": "sessions"}, {"name": "totalUsers"}, {"name": "keyEvents"}],
        "dimensionFilter": {"filter": {"fieldName": "sessionMedium", "inListFilter": {"values": ["social", "article"]}}},
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}], "limit": 200,
    })
    if r.status_code != 200:
        raise GAError(f"گزارش گوگل آنالیتیکس گرفته نشد: HTTP {r.status_code} {r.text[:200]}")
    out = []
    for row in r.json().get("rows", []):
        d, mv = [x["value"] for x in row["dimensionValues"]], [x["value"] for x in row["metricValues"]]
        out.append({"campaign": d[0], "source": d[1], "medium": d[2],
                    "sessions": int(mv[0]), "users": int(mv[1]), "key_events": int(float(mv[2]))})
    return out


def configured() -> bool:
    return bool(settings.ga_property_id and settings.ga_service_account)
