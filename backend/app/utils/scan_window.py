"""Scan scheduling window: randomize each channel's scan time within a configured daily window."""

import logging
import random
from datetime import datetime, time, timedelta, timezone

logger = logging.getLogger(__name__)

MIN_WINDOW_HOURS = 8


def validate_scan_window(start_hour: int | None, end_hour: int | None) -> None:
    """Validate a scan window configuration.

    Raises ValueError if the window is narrower than MIN_WINDOW_HOURS.
    start_hour == end_hour is treated as 24-hour (full day, no window).
    """
    if start_hour is None and end_hour is None:
        return  # No window configured
    if start_hour is None or end_hour is None:
        raise ValueError("Both start_hour and end_hour must be set, or both unset.")
    if not (0 <= start_hour <= 23) or not (0 <= end_hour <= 23):
        raise ValueError("Hours must be between 0 and 23.")
    if start_hour == end_hour:
        return  # 24-hour window

    width = _window_width_hours(start_hour, end_hour)
    if width < MIN_WINDOW_HOURS:
        raise ValueError(
            f"Scan window must be at least {MIN_WINDOW_HOURS} hours wide. "
            f"Configured window is {width} hours."
        )


def _window_width_hours(start_hour: int, end_hour: int) -> int:
    """Return width of window in hours, handling midnight wrap."""
    if end_hour > start_hour:
        return end_hour - start_hour
    # Wraps midnight: start to midnight + midnight to end
    return (24 - start_hour) + end_hour


def compute_next_scan_at(
    start_hour: int | None = None,
    end_hour: int | None = None,
    now_local: datetime | None = None,
    min_offset_hours: int = 12,
) -> datetime:
    """Compute a random next-scan time (UTC).

    If start_hour/end_hour are both set and form a valid window, the scan time
    falls within that window. Otherwise scans are distributed across the full
    24-hour day.

    min_offset_hours (default 12) enforces a minimum gap before the next scan
    so randomization can't roll a near-future time. This is critical for avoiding
    hammering YouTube with repeated scans of the same channel.
    """
    # Use local system timezone for window semantics
    if now_local is None:
        now_local = datetime.now().astimezone()

    min_offset_seconds = max(60, int(min_offset_hours * 3600))
    earliest = now_local + timedelta(seconds=min_offset_seconds)

    # No window or invalid: pick somewhere between min_offset and min_offset + 12h
    no_window = (
        start_hour is None or end_hour is None or start_hour == end_hour
    )

    if no_window:
        # Spread across a 12h band starting at the minimum offset
        extra = random.randint(0, 12 * 3600)
        target_local = earliest + timedelta(seconds=extra)
        return target_local.astimezone(timezone.utc).replace(tzinfo=None)

    # Constrained to window. Find the next window opening at or after earliest.
    width_hours = _window_width_hours(start_hour, end_hour)
    width_seconds = width_hours * 3600

    # Try successive days until we find a window that's at or after earliest
    candidate_day = earliest.date()
    for _ in range(3):  # at most look 3 days ahead
        start_candidate = datetime.combine(
            candidate_day, time(hour=start_hour), tzinfo=now_local.tzinfo
        )
        end_candidate = start_candidate + timedelta(seconds=width_seconds)

        if end_candidate <= earliest:
            # This window already closed before our min offset
            candidate_day = candidate_day + timedelta(days=1)
            continue

        # Window overlaps [earliest, ...). Pick a time inside it no earlier than earliest.
        window_base = max(start_candidate, earliest)
        remaining = end_candidate - window_base
        if remaining.total_seconds() < 60:
            candidate_day = candidate_day + timedelta(days=1)
            continue

        offset = random.uniform(0, remaining.total_seconds())
        target_local = window_base + timedelta(seconds=offset)
        return target_local.astimezone(timezone.utc).replace(tzinfo=None)

    # Fallback: shouldn't happen, but if we can't find a window pick min_offset + 1h
    target_local = earliest + timedelta(hours=1)
    return target_local.astimezone(timezone.utc).replace(tzinfo=None)
