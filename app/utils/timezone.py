"""
Timezone and Business Day Utilities for Pakistan (Asia/Karachi).
Always stores timestamps in UTC.
Displays, parses, and aggregates reports in Asia/Karachi using Python's standard library zoneinfo.
"""
from datetime import datetime, timezone, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

PK_TZ = ZoneInfo("Asia/Karachi")


def utc_now() -> datetime:
    """Returns the current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_pakistan_time(utc_dt: Optional[datetime]) -> Optional[datetime]:
    """Converts a UTC datetime to Asia/Karachi timezone."""
    if utc_dt is None:
        return None
    if utc_dt.tzinfo is None:
        # Treat naive datetime as UTC
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(PK_TZ)


def format_pakistan_datetime(utc_dt: Optional[datetime], fmt: str = "%Y-%m-%d %I:%M:%S %p") -> str:
    """Formats a UTC timestamp into a human-readable Asia/Karachi string."""
    pk_dt = to_pakistan_time(utc_dt)
    if pk_dt is None:
        return ""
    return pk_dt.strftime(fmt)


def get_business_date(dt: Optional[datetime] = None, cutoff_str: str = "04:00") -> date:
    """
    Computes the institutional business date for a given datetime.
    If the transaction time is before the cutoff (e.g., 01:30 AM before 04:00 AM cutoff),
    it belongs to the PREVIOUS business calendar day.
    """
    if dt is None:
        dt = utc_now()
    pk_dt = to_pakistan_time(dt)
    if pk_dt is None:
        return date.today()

    parts = cutoff_str.strip().split(":")
    cutoff_hour = int(parts[0])
    cutoff_minute = int(parts[1]) if len(parts) > 1 else 0
    cutoff_time = time(cutoff_hour, cutoff_minute)

    if pk_dt.time() < cutoff_time:
        # Transaction falls into the previous business day
        return pk_dt.date() - timedelta(days=1)
    return pk_dt.date()


def get_fiscal_year(dt: Optional[datetime] = None, start_month_day: str = "07-01") -> str:
    """
    Computes the fiscal year code for Pakistan (default start July 1).
    Example: A date in August 2025 yields '2526'.
    Example: A date in February 2026 yields '2526'.
    """
    if dt is None:
        dt = utc_now()
    pk_dt = to_pakistan_time(dt)
    if pk_dt is None:
        pk_dt = datetime.now(PK_TZ)

    month, day = map(int, start_month_day.split("-"))
    year = pk_dt.year

    if (pk_dt.month, pk_dt.day) < (month, day):
        # We are before July 1; fiscal year started in previous calendar year
        start_yr = year - 1
        end_yr = year
    else:
        start_yr = year
        end_yr = year + 1

    return f"{str(start_yr)[2:]}{str(end_yr)[2:]}"
