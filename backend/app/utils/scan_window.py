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


def in_scan_window(start_hour: int | None, end_hour: int | None, now_local: datetime | None = None) -> bool:
    """True when now (local time) is inside the daily scan window, or no window is set."""
    if start_hour is None or end_hour is None or start_hour == end_hour:
        return True
    if now_local is None:
        now_local = datetime.now().astimezone()
    hour = now_local.hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour  # wraps midnight


def _window_width_hours(start_hour: int, end_hour: int) -> int:
    """Return width of window in hours, handling midnight wrap."""
    if end_hour > start_hour:
        return end_hour - start_hour
    # Wraps midnight: start to midnight + midnight to end
    return (24 - start_hour) + end_hour


END_MARGIN = timedelta(minutes=10)


def _explicit_tz(now_local: datetime):
    """The caller's own zone when it isn't the system's (tests, explicit zones), else None."""
    if now_local.utcoffset() != now_local.astimezone().utcoffset():
        return now_local.tzinfo
    return None


def _window_span(day, start_hour: int, end_hour: int, tz=None) -> tuple[datetime, datetime]:
    """(open, last pick time) of the window that opens on `day`.

    Boundaries are built per date in system local time (so DST shifts are
    respected), or in `tz` when the caller passed a different zone. Picks stop
    END_MARGIN before the close, so the last scan tick inside the window still
    sees the channel as due.
    """
    end_day = day + timedelta(days=1) if end_hour <= start_hour else day

    def at(d, hour):
        naive = datetime.combine(d, time(hour=hour))
        return naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()

    return at(day, start_hour), at(end_day, end_hour) - END_MARGIN


def usable_window(start, end) -> tuple[int | None, int | None]:
    """Coerce stored window hours; an unusable or invalid window counts as no window."""
    try:
        s = int(start) if start is not None else None
        e = int(end) if end is not None else None
        validate_scan_window(s, e)
    except (TypeError, ValueError):
        return None, None
    return s, e


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
    hammering YouTube with repeated scans of the same channel. Without a window,
    a random extra of 0 to min(12h, min_offset) is added (1h -> 1-2h, 12h -> 12-24h,
    24h -> 24-36h).
    """
    # Use local system timezone for window semantics
    if now_local is None:
        now_local = datetime.now().astimezone()
    zone = _explicit_tz(now_local)

    # Floor at 1h (the settings API minimum) so a bad stored value can't make
    # every channel due on every scan tick
    min_offset_seconds = max(3600, int(min_offset_hours * 3600))
    earliest = now_local + timedelta(seconds=min_offset_seconds)

    # No window or invalid: pick somewhere between min_offset and min_offset + extra
    no_window = (
        start_hour is None or end_hour is None or start_hour == end_hour
    )

    if no_window:
        # Spread over a band as wide as the interval, capped at 12h
        extra = random.randint(0, min(12 * 3600, min_offset_seconds))
        target_local = earliest + timedelta(seconds=extra)
        return target_local.astimezone(timezone.utc).replace(tzinfo=None)

    # Constrained to window. Find the next window opening at or after earliest.
    # Try successive days until we find a window that's at or after earliest.
    # Start one day back: an overnight window that opened yesterday evening can
    # still be open at `earliest` (e.g. 22:00-06:00 and earliest at 01:00).
    candidate_day = earliest.date() - timedelta(days=1)
    for _ in range(4):  # at most look 3 days ahead
        start_candidate, end_candidate = _window_span(candidate_day, start_hour, end_hour, zone)

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


def spread_into_window(
    count: int,
    now_local: datetime | None = None,
    start_hour: int | None = None,
    end_hour: int | None = None,
) -> list[datetime]:
    """Return `count` random scan times (naive UTC) spread across the next open window.

    Used for channels that have no next_scan_at yet (startup stagger, config
    import). With a window set, every time falls inside it: the rest of the
    current window if at least an hour is left, else the next one. Without a
    window the times spread over the next 24 hours (the old behaviour).
    """
    if count <= 0:
        return []
    if now_local is None:
        now_local = datetime.now().astimezone()

    def _utc(dt: datetime) -> datetime:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    if start_hour is None or end_hour is None or start_hour == end_hour:
        return sorted(
            _utc(now_local + timedelta(seconds=random.uniform(60, 24 * 3600)))
            for _ in range(count)
        )

    zone = _explicit_tz(now_local)
    earliest = now_local + timedelta(hours=1)
    base = end = None
    day = now_local.date() - timedelta(days=1)
    for _ in range(4):
        start_candidate, end_candidate = _window_span(day, start_hour, end_hour, zone)
        day += timedelta(days=1)
        if end_candidate <= earliest:
            continue
        # Start from now if the window is already open, else from its opening
        base = max(start_candidate, now_local + timedelta(minutes=1))
        end = end_candidate
        break
    if base is None or end is None:  # unreachable for a valid window
        return sorted(_utc(now_local + timedelta(hours=1, seconds=i)) for i in range(count))

    span = (end - base).total_seconds()
    return sorted(
        _utc(base + timedelta(seconds=random.uniform(0, span))) for _ in range(count)
    )
