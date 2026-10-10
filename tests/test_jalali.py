import datetime as dt

from app import jalali


def test_round_trip_and_known_dates():
    assert jalali.fmt_date(dt.date(2026, 10, 10)) == "1405/07/18"
    assert jalali.fmt_date(dt.date(2025, 3, 21)) == "1404/01/01"
    assert jalali.parse("۱۴۰۵/۰۷/۱۸") == dt.date(2026, 10, 10)
    assert jalali.parse("2026-10-10") == dt.date(2026, 10, 10) and jalali.parse("") is None
    d = dt.date(2020, 1, 1)
    while d < dt.date(2031, 1, 1):
        assert jalali.parse(jalali.fmt_date(d)) == d
        d += dt.timedelta(days=1)
    assert jalali.fmt(dt.datetime(2026, 10, 10, 16, 43)) == "1405/07/18 16:43"
