"""Pure local and solar-relative time-window evaluation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

from .model import EndpointType, TimeEndpoint, TimeWindow

SolarEventResolver = Callable[[EndpointType, date], datetime]


def resolve_endpoint(
    endpoint: TimeEndpoint,
    anchor_date: date,
    tzinfo: object,
    solar_event_resolver: SolarEventResolver | None,
) -> datetime:
    """Resolve one endpoint in the window's local timezone."""
    if endpoint.kind is EndpointType.FIXED:
        resolved = datetime.combine(anchor_date, endpoint.value, tzinfo=tzinfo)  # type: ignore[arg-type]
    else:
        if solar_event_resolver is None:
            msg = "A solar event resolver is required for sunrise/sunset endpoints"
            raise ValueError(msg)
        resolved = solar_event_resolver(endpoint.kind, anchor_date)
    return resolved + timedelta(minutes=endpoint.offset_minutes)


def resolve_window_for_date(
    window: TimeWindow,
    anchor_date: date,
    tzinfo: object,
    solar_event_resolver: SolarEventResolver | None = None,
) -> tuple[datetime, datetime]:
    """Resolve a time window anchored to its start date."""
    start = resolve_endpoint(window.start, anchor_date, tzinfo, solar_event_resolver)
    end = resolve_endpoint(window.end, anchor_date, tzinfo, solar_event_resolver)
    if end <= start:
        end = resolve_endpoint(
            window.end,
            anchor_date + timedelta(days=1),
            tzinfo,
            solar_event_resolver,
        )
    return start, end


def is_time_window_active(
    window: TimeWindow,
    now: datetime,
    solar_event_resolver: SolarEventResolver | None = None,
) -> bool:
    """Return whether now lies in a start-inclusive, end-exclusive window."""
    if now.tzinfo is None:
        msg = "now must be timezone-aware"
        raise ValueError(msg)
    for offset in (0, -1):
        anchor = now.date() + timedelta(days=offset)
        if anchor.weekday() not in window.weekdays:
            continue
        start, end = resolve_window_for_date(
            window, anchor, now.tzinfo, solar_event_resolver
        )
        if start <= now < end:
            return True
    return False


def any_time_window_active(
    windows: Iterable[TimeWindow],
    now: datetime,
    solar_event_resolver: SolarEventResolver | None = None,
) -> bool:
    """Return whether any configured time window is active."""
    return any(
        is_time_window_active(window, now, solar_event_resolver) for window in windows
    )


def next_time_boundary(
    windows: Iterable[TimeWindow],
    now: datetime,
    solar_event_resolver: SolarEventResolver | None = None,
) -> datetime | None:
    """Return the next start or end boundary across the next eight days."""
    candidates: list[datetime] = []
    for day_offset in range(8):
        anchor = now.date() + timedelta(days=day_offset)
        for window in windows:
            if anchor.weekday() not in window.weekdays:
                continue
            start, end = resolve_window_for_date(
                window, anchor, now.tzinfo, solar_event_resolver
            )
            if start > now:
                candidates.append(start)
            if end > now:
                candidates.append(end)
    return min(candidates, default=None)
