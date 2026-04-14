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
) -> datetime:
    """Compute a random next-scan time (UTC) within the next 24h.

    If start_hour/end_hour are both set and form a valid window, the scan time
    falls within that window. Otherwise scans are distributed across the full
    24-hour day.
    """
    # Use local system timezone for window semantics
    if now_local is None:
        now_local = datetime.now().astimezone()

    # No window or invalid: pick anywhere in next 24h
    no_window = (
        start_hour is None or end_hour is None or start_hour == end_hour
    )

    if no_window:
        offset_seconds = random.randint(60, 24 * 3600)
        target_local = now_local + timedelta(seconds=offset_seconds)
        return target_local.astimezone(timezone.utc).replace(tzinfo=None)

    # Constrain to window. Find the next window opening (today or tomorrow).
    width_hours = _window_width_hours(start_hour, end_hour)
    width_seconds = width_hours * 3600

    # Determine today's window start in local time
    today = now_local.date()
    start_today = datetime.combine(today, time(hour=start_hour), tzinfo=now_local.tzinfo)
    end_today = start_today + timedelta(seconds=width_seconds)

    if end_today <= now_local:
        # Today's window has already ended; use tomorrow
        start_next = start_today + timedelta(days=1)
    else:
        # Today's window is open or upcoming
        start_next = start_today

    # Random offset within the window
    # If now is past start_next (we're inside the window), pick a time in the remainder
    window_base = max(start_next, now_local + timedelta(seconds=60))
    remaining = (start_next + timedelta(seconds=width_seconds)) - window_base
    if remaining.total_seconds() < 60:
        # Window is closing; schedule tomorrow's full window
        start_next = start_today + timedelta(days=1) if start_next == start_today else start_next + timedelta(days=1)
        window_base = start_next
        remaining = timedelta(seconds=width_seconds)

    offset = random.uniform(0, remaining.total_seconds())
    target_local = window_base + timedelta(seconds=offset)
    return target_local.astimezone(timezone.utc).replace(tzinfo=None)
