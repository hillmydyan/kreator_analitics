"""
Data Extraction Engine (Tahap 3 & 4 — PRD Section 2.4 #5, 3.2, 3.3, 3.4 & 7.3).

Parses raw UI strings for views, upload dates, period filtering, and product statuses.
Never invents or estimates missing data (PRD Section 7.3 & 13).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

_VIEWS_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(k|m|b|rb|ribu|jt|juta)?\s*(?:views|tayangan)?\s*$",
    re.IGNORECASE,
)

_REL_DAYS_RE = re.compile(
    r"^\s*(\d+)\s*(s|sec|detik|d|day|days|hari|h|hr|hour|hours|jam|w|wk|week|weeks|minggu|m|min|menit)\s*(?:ago|lalu)?\s*$",
    re.IGNORECASE,
)


def parse_views_count(raw_views: Optional[str]) -> Optional[int]:
    """
    Parse TikTok views string (e.g. '250.5K', '1.2M', '15,4rb', '9800') into an integer.
    Returns None if missing or unparseable (PRD Section 7.3: never fill with guessed values).
    """
    if raw_views is None:
        return None
    text = raw_views.strip()
    if not text:
        return None

    # Pure integer with thousand separators like "1.000.000" or "1,000,000"
    if re.match(r"^\d{1,3}(?:[.,]\d{3})+$", text):
        digits_only = re.sub(r"[.,]", "", text)
        return int(digits_only)

    match = _VIEWS_RE.match(text)
    if not match:
        return None

    num_str, suffix = match.group(1), (match.group(2) or "").lower()
    # Normalize decimal separator
    normalized_num = num_str.replace(",", ".")
    try:
        val = float(normalized_num)
    except ValueError:
        return None

    multipliers = {
        "": 1,
        "k": 1_000,
        "rb": 1_000,
        "ribu": 1_000,
        "m": 1_000_000,
        "jt": 1_000_000,
        "juta": 1_000_000,
        "b": 1_000_000_000,
    }
    mult = multipliers.get(suffix)
    if mult is None:
        return None
    return int(round(val * mult))


def parse_upload_date(
    raw_date: Optional[str], reference_date: Optional[date] = None
) -> Optional[date]:
    """
    Parse TikTok upload date (ISO 'YYYY-MM-DD', 'MM-DD', or relative '3d ago', '2 hari lalu').
    Returns None if unverifiable (PRD Section 10 #6 & 7.3).
    """
    if raw_date is None:
        return None
    text = raw_date.strip().lstrip("·").strip()
    if not text:
        return None

    ref = reference_date or date.today()

    # 1. Full ISO date: YYYY-MM-DD or YYYY/MM/DD
    iso_match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", text)
    if iso_match:
        try:
            return date(
                int(iso_match.group(1)),
                int(iso_match.group(2)),
                int(iso_match.group(3)),
            )
        except ValueError:
            return None

    # 2. Short month-day within current year: MM-DD (TikTok format for current year)
    md_match = re.match(r"^(\d{1,2})-(\d{1,2})$", text)
    if md_match:
        try:
            candidate = date(ref.year, int(md_match.group(1)), int(md_match.group(2)))
            if candidate > ref:
                candidate = date(ref.year - 1, int(md_match.group(1)), int(md_match.group(2)))
            return candidate
        except ValueError:
            return None

    # 3. Relative TikTok date: '2d ago', '5 hari lalu', '12h ago', '1w ago'
    rel_match = _REL_DAYS_RE.match(text)
    if rel_match:
        amount = int(rel_match.group(1))
        unit = rel_match.group(2).lower()
        if unit in ("s", "sec", "detik", "h", "hr", "hour", "hours", "jam", "m", "min", "menit"):
            return ref
        if unit in ("d", "day", "days", "hari"):
            return ref - timedelta(days=amount)
        if unit in ("w", "wk", "week", "weeks", "minggu"):
            return ref - timedelta(days=amount * 7)

    if text.lower() in ("hari ini", "today", "baru saja", "just now"):
        return ref
    if text.lower() in ("kemarin", "yesterday"):
        return ref - timedelta(days=1)

    return None


def is_within_period(
    upload_dt: Optional[date],
    period_days: int,
    reference_date: Optional[date] = None,
) -> Tuple[bool, date, date]:
    """
    Check if `upload_dt` falls within the last `period_days` [start_date, end_date]
    (PRD Section 3.2: 7 or 30 days).
    If `upload_dt` is None, returns False so unverified dates do not corrupt period filtering.
    """
    if period_days not in (7, 30):
        raise ValueError("Periode analisis hanya mendukung 7 atau 30 hari (PRD Section 3.2)")

    end_dt = reference_date or date.today()
    start_dt = end_dt - timedelta(days=period_days)
    if upload_dt is None:
        return False, start_dt, end_dt

    return (start_dt <= upload_dt <= end_dt), start_dt, end_dt
