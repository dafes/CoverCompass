"""Tests for local, cross-midnight, weekday and solar-relative windows."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.cover_compass.model import (
    EndpointType,
    TimeEndpoint,
    TimeWindow,
)
from custom_components.cover_compass.time_window import (
    is_time_window_active,
    next_time_boundary,
    resolve_endpoint,
)

TZ = ZoneInfo("Europe/Berlin")


def test_normal_range_exact_boundaries() -> None:
    window = TimeWindow(
        start=TimeEndpoint(value=time(7)),
        end=TimeEndpoint(value=time(12)),
    )
    assert is_time_window_active(window, datetime(2026, 8, 10, 7, tzinfo=TZ))
    assert is_time_window_active(window, datetime(2026, 8, 10, 11, 59, tzinfo=TZ))
    assert not is_time_window_active(window, datetime(2026, 8, 10, 12, tzinfo=TZ))


def test_cross_midnight_uses_start_weekday() -> None:
    monday_only = TimeWindow(
        start=TimeEndpoint(value=time(22)),
        end=TimeEndpoint(value=time(6)),
        weekdays=frozenset({0}),
    )
    assert is_time_window_active(monday_only, datetime(2026, 8, 10, 23, tzinfo=TZ))
    assert is_time_window_active(monday_only, datetime(2026, 8, 11, 2, tzinfo=TZ))
    assert not is_time_window_active(
        monday_only, datetime(2026, 8, 11, 22, 30, tzinfo=TZ)
    )


def test_sunrise_relative_window_and_next_boundary() -> None:
    def events(kind: EndpointType, day: date) -> datetime:
        hour = 6 if kind is EndpointType.SUNRISE else 20
        return datetime.combine(day, time(hour), tzinfo=TZ)

    window = TimeWindow(
        start=TimeEndpoint(kind=EndpointType.SUNRISE, offset_minutes=30),
        end=TimeEndpoint(value=time(12)),
    )
    now = datetime(2026, 8, 10, 6, 45, tzinfo=TZ)
    assert is_time_window_active(window, now, events)
    assert next_time_boundary([window], now, events) == datetime(
        2026, 8, 10, 12, tzinfo=TZ
    )


def test_dst_uses_local_timezone_naturally() -> None:
    window = TimeWindow(
        start=TimeEndpoint(value=time(1, 30)),
        end=TimeEndpoint(value=time(4)),
    )
    assert is_time_window_active(window, datetime(2026, 3, 29, 3, 30, tzinfo=TZ))


def test_invalid_time_inputs_and_empty_boundaries() -> None:
    window = TimeWindow(
        start=TimeEndpoint(value=time(7)), end=TimeEndpoint(value=time(8))
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        is_time_window_active(window, datetime(2026, 8, 10, 7, 30))
    with pytest.raises(ValueError, match="solar event resolver"):
        resolve_endpoint(
            TimeEndpoint(kind=EndpointType.SUNRISE), date(2026, 8, 10), TZ, None
        )
    assert next_time_boundary([], datetime(2026, 8, 10, 7, tzinfo=TZ)) is None
