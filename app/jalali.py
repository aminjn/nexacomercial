"""Shamsi (Jalali) dates for everything shown in the dashboard; stored dates stay Gregorian."""
from __future__ import annotations

import datetime as dt
import re

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
EN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def to_jalali(d: dt.date) -> tuple[int, int, int]:
    gy, gm, gd = d.year, d.month, d.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def to_gregorian(jy: int, jm: int, jd: int) -> dt.date:
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd
    days += (jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    months = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 12 and gd > months[gm]:
        gd -= months[gm]
        gm += 1
    return dt.date(gy, gm + 1, gd)


def fmt_date(d: dt.date | None) -> str:
    if not d:
        return ""
    y, m, dd = to_jalali(d)
    return f"{y:04d}/{m:02d}/{dd:02d}"


def fmt(t: dt.datetime) -> str:
    """1405/07/18 16:43"""
    return f"{fmt_date(t.date())} {t:%H:%M}"


def parse(v: str) -> dt.date | None:
    """A date typed in the dashboard: Shamsi (1405/07/18, Persian digits fine) or Gregorian (2026-10-10)."""
    v = (v or "").strip().translate(EN_DIGITS)
    if not v:
        return None
    m = re.fullmatch(r"(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})", v)
    if not m:
        raise ValueError(f"تاریخ نامعتبر: {v} (مثل ۱۴۰۵/۰۷/۱۸)")
    y, mo, d = map(int, m.groups())
    if y < 1700:
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            raise ValueError(f"تاریخ نامعتبر: {v}")
        return to_gregorian(y, mo, d)
    return dt.date(y, mo, d)
