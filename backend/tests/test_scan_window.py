from datetime import datetime, timedelta, timezone

import pytest

from app.schemas import SettingsUpdate
from app.utils.scan_window import compute_next_scan_at

NOW = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
NOW_NAIVE_UTC = NOW.replace(tzinfo=None)


def _hours_ahead(result: datetime) -> float:
    return (result - NOW_NAIVE_UTC).total_seconds() / 3600


class TestNoWindow:
    @pytest.mark.parametrize(
        "interval,low,high",
        [(1, 1, 2), (12, 12, 24), (24, 24, 36)],
    )
    def test_bounds(self, interval, low, high):
        draws = [
            _hours_ahead(compute_next_scan_at(now_local=NOW, min_offset_hours=interval))
            for _ in range(500)
        ]
        assert min(draws) >= low
        assert max(draws) <= high
        # Randomized, not a fixed offset
        assert max(draws) - min(draws) > (high - low) * 0.5

    @pytest.mark.parametrize("interval", [0, -5])
    def test_bad_interval_floors_at_one_hour(self, interval):
        draws = [
            _hours_ahead(compute_next_scan_at(now_local=NOW, min_offset_hours=interval))
            for _ in range(200)
        ]
        assert min(draws) >= 1
        assert max(draws) <= 2


class TestWindowed:
    def test_lands_inside_window(self):
        for _ in range(200):
            result = compute_next_scan_at(
                start_hour=22, end_hour=8, now_local=NOW, min_offset_hours=1
            )
            local = result.replace(tzinfo=timezone.utc)
            assert local >= NOW + timedelta(hours=1)
            assert local.hour >= 22 or local.hour < 8


def test_settings_update_ignores_legacy_cron():
    body = SettingsUpdate(global_schedule_cron="0 3 * * *", default_quality="best")
    assert "global_schedule_cron" not in body.model_dump(exclude_unset=True)
