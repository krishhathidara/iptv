"""Subscriber playlist tokens and UTC calendar-month entitlements."""

import calendar
import hashlib
import secrets
from datetime import datetime, timezone


def add_months(start: datetime, months: int) -> datetime:
    """Add calendar months, clamping month-end days (e.g. January 31 -> February 28)."""
    index = start.year * 12 + start.month - 1 + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))


def utc_datetime(value: datetime) -> datetime:
    """SQLite stores UTC timestamps without a zone; PostgreSQL retains the zone."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()