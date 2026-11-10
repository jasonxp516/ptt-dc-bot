"""
Unified Asia/Taipei timezone utilities and leap-year safe PTT MM/DD date parsing.
"""

import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

_MMDD_REGEX = re.compile(r"^\s*(\d{1,2})/(\d{1,2})\s*$")


def now_tpe() -> datetime:
    """Return current timezone-aware datetime in Asia/Taipei (UTC+8)."""
    return datetime.now(TAIPEI_TZ)


def today_tpe_str() -> str:
    """Return YYYY-MM-DD in Asia/Taipei."""
    return now_tpe().strftime("%Y-%m-%d")


def now_tpe_str() -> str:
    """Return YYYY-MM-DD HH:MM:SS in Asia/Taipei."""
    return now_tpe().strftime("%Y-%m-%d %H:%M:%S")


def parse_ptt_month_day(
    date_str: str,
    ref_dt: Optional[datetime] = None,
) -> Optional[datetime]:
    """
    Parse PTT list date string 'MM/DD' into a timezone-aware Asia/Taipei datetime.
    Safely handles:
    - Cross-year rollback (e.g. '12/31' observed on '01/02' resolves to previous year).
    - Leap-year '02/29' observed in a non-leap year without raising ValueError.
    """
    if not date_str:
        return None
    m = _MMDD_REGEX.match(str(date_str).strip())
    if not m:
        return None

    month, day = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None

    if ref_dt is None:
        ref = now_tpe()
    elif ref_dt.tzinfo is None:
        ref = ref_dt.replace(tzinfo=TAIPEI_TZ)
    else:
        ref = ref_dt.astimezone(TAIPEI_TZ)

    # Search up to 8 years backwards for a valid calendar date <= ref + 1 day
    cutoff = ref + timedelta(days=1)
    for offset in range(0, 9):
        candidate_year = ref.year - offset
        try:
            cand = datetime(candidate_year, month, day, tzinfo=TAIPEI_TZ)
        except ValueError:
            # e.g. Feb 29 on a non-leap year -> try earlier years
            continue
        if cand <= cutoff:
            return cand

    return None
