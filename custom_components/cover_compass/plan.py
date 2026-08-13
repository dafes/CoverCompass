"""Validation models for plans exported by the CoverCompass planner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PLAN_FORMAT: Final = "cover-compass-plan"
PLAN_VERSION: Final = 1
MAX_PLAN_JSON_BYTES: Final = 256_000
MAX_OUTLINE_POINTS: Final = 100
MAX_SHUTTERS: Final = 128


class PlanValidationError(ValueError):
    """A planner export cannot be imported."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PlanPoint:
    """One geographic point in the house outline."""

    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class PlanHouse:
    """House metadata selected in the planner."""

    name: str
    latitude: float
    longitude: float
    time_zone: str
    rotation: float


@dataclass(frozen=True, slots=True)
class PlannedShutter:
    """One shutter placed on an outline segment."""

    id: str
    name: str
    facade_azimuth: float
    segment_index: int
    segment_position: float


@dataclass(frozen=True, slots=True)
class CoverPlan:
    """Validated visual plan ready for Home Assistant entity assignment."""

    house: PlanHouse
    outline: tuple[PlanPoint, ...]
    shutters: tuple[PlannedShutter, ...]


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanValidationError("invalid_plan", f"{label} must be an object")
    return value


def _string(value: object, label: str, *, maximum: int = 100) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanValidationError("invalid_plan", f"{label} must not be empty")
    result = value.strip()
    if len(result) > maximum:
        raise PlanValidationError("invalid_plan", f"{label} is too long")
    return result


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PlanValidationError("invalid_plan", f"{label} must be a number")
    return float(value)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanValidationError("invalid_plan", f"{label} must be an integer")
    return value


def _coordinate(value: object, label: str, minimum: float, maximum: float) -> float:
    result = _number(value, label)
    if not minimum <= result <= maximum:
        raise PlanValidationError("invalid_plan", f"{label} is outside its range")
    return result


def _parse_house(value: object) -> PlanHouse:
    house = _mapping(value, "house")
    time_zone = _string(house.get("time_zone"), "house.time_zone")
    try:
        ZoneInfo(time_zone)
    except ZoneInfoNotFoundError as err:
        raise PlanValidationError(
            "invalid_plan", "house.time_zone is not a valid IANA time zone"
        ) from err
    rotation = _number(house.get("rotation", 0), "house.rotation")
    if not 0 <= rotation < 360:
        raise PlanValidationError("invalid_plan", "house.rotation must be in [0, 360)")
    return PlanHouse(
        name=_string(house.get("name"), "house.name"),
        latitude=_coordinate(house.get("latitude"), "house.latitude", -90, 90),
        longitude=_coordinate(house.get("longitude"), "house.longitude", -180, 180),
        time_zone=time_zone,
        rotation=rotation,
    )


def _parse_outline(value: object) -> tuple[PlanPoint, ...]:
    if not isinstance(value, list) or not 3 <= len(value) <= MAX_OUTLINE_POINTS:
        raise PlanValidationError(
            "invalid_plan",
            f"outline must contain between 3 and {MAX_OUTLINE_POINTS} points",
        )
    points = tuple(
        PlanPoint(
            latitude=_coordinate(
                _mapping(item, f"outline[{index}]").get("latitude"),
                f"outline[{index}].latitude",
                -90,
                90,
            ),
            longitude=_coordinate(
                _mapping(item, f"outline[{index}]").get("longitude"),
                f"outline[{index}].longitude",
                -180,
                180,
            ),
        )
        for index, item in enumerate(value)
    )
    if len({(point.latitude, point.longitude) for point in points}) < 3:
        raise PlanValidationError(
            "invalid_plan", "outline must contain three distinct points"
        )
    return points


def _parse_shutters(value: object, outline_length: int) -> tuple[PlannedShutter, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SHUTTERS:
        raise PlanValidationError(
            "invalid_plan",
            f"shutters must contain between 1 and {MAX_SHUTTERS} entries",
        )
    shutters: list[PlannedShutter] = []
    for index, item in enumerate(value):
        shutter = _mapping(item, f"shutters[{index}]")
        azimuth = _number(
            shutter.get("facade_azimuth"), f"shutters[{index}].facade_azimuth"
        )
        if not 0 <= azimuth < 360:
            raise PlanValidationError(
                "invalid_plan", f"shutters[{index}].facade_azimuth must be in [0, 360)"
            )
        segment_index = _integer(
            shutter.get("segment_index"), f"shutters[{index}].segment_index"
        )
        if not 0 <= segment_index < outline_length:
            raise PlanValidationError(
                "invalid_plan",
                f"shutters[{index}].segment_index is outside the outline",
            )
        segment_position = _number(
            shutter.get("segment_position"),
            f"shutters[{index}].segment_position",
        )
        if not 0 <= segment_position <= 1:
            raise PlanValidationError(
                "invalid_plan",
                f"shutters[{index}].segment_position must be in [0, 1]",
            )
        shutters.append(
            PlannedShutter(
                id=_string(shutter.get("id"), f"shutters[{index}].id", maximum=128),
                name=_string(shutter.get("name"), f"shutters[{index}].name"),
                facade_azimuth=azimuth,
                segment_index=segment_index,
                segment_position=segment_position,
            )
        )
    ids = [shutter.id for shutter in shutters]
    if len(ids) != len(set(ids)):
        raise PlanValidationError("invalid_plan", "shutter ids must be unique")
    return tuple(shutters)


def parse_cover_plan(raw: str) -> CoverPlan:
    """Parse and validate a versioned JSON planner export."""
    if len(raw.encode()) > MAX_PLAN_JSON_BYTES:
        raise PlanValidationError("plan_too_large", "plan JSON is too large")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as err:
        raise PlanValidationError(
            "invalid_plan_json", "plan is not valid JSON"
        ) from err
    root = _mapping(value, "plan")
    if root.get("format") != PLAN_FORMAT:
        raise PlanValidationError(
            "invalid_plan", f"plan format must be {PLAN_FORMAT!r}"
        )
    version = root.get("version")
    if isinstance(version, bool) or version != PLAN_VERSION:
        raise PlanValidationError(
            "unsupported_plan_version",
            f"plan version {version!r} is not supported",
        )
    outline = _parse_outline(root.get("outline"))
    return CoverPlan(
        house=_parse_house(root.get("house")),
        outline=outline,
        shutters=_parse_shutters(root.get("shutters"), len(outline)),
    )
