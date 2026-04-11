"""Ring buffer log handler for in-app log viewing.

Captures the last N log records in memory so they can be served
via the API without needing Docker console access.
"""

import logging
from collections import deque
from datetime import datetime, timezone

LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


class BufferHandler(logging.Handler):
    """Log handler that stores records in a fixed-size ring buffer."""

    def __init__(self, maxlen: int = 500):
        super().__init__()
        self._buffer: deque[dict] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.append({
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            })
        except Exception:
            pass

    def get_entries(self, level: str | None = None, limit: int = 200) -> list[dict]:
        """Get buffered log entries, optionally filtered by minimum level."""
        entries = list(self._buffer)
        if level and level in LEVEL_ORDER:
            min_level = LEVEL_ORDER[level]
            entries = [e for e in entries if LEVEL_ORDER.get(e["level"], 0) >= min_level]
        return entries[-limit:]


# Module-level singleton
log_buffer = BufferHandler()
log_buffer.setLevel(logging.DEBUG)
log_buffer.setFormatter(logging.Formatter("%(message)s"))
