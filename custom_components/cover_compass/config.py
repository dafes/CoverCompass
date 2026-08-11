"""Config-entry serialization and validation helpers."""

from __future__ import annotations

from datetime import time
from typing import Any
from zoneinfo import ZoneInfo

from .const import (
    CONF_COVERS,
    CONF_DRY_RUN,
    CONF_GLOBAL_ENABLED,
    CONF_HOUSE_NAME,
    CONF_HOUSE_ROTATION,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RECONCILE_INTERVAL,
    CONF_TIME_ZONE,
    DEFAULT_RECONCILE_INTERVAL,
)
from .model import (
    AdvancedMatch,
    AutomationMode,
    ConditionKey,
    CoverConfig,
    EndpointType,
    EnvironmentConfig,
    HouseConfig,
    IntegrationConfig,
    ManualOverrideMode,
    MaximumThresholdConfig,
    SafetyPolicy,
    ThresholdConfig,
    TimeEndpoint,
    TimeWindow,
    WindConfig,
    model_to_dict,
)


def _time(value: str | time | None, default: time) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        return time.fromisoformat(value)
    return default


def _threshold(value: dict[str, Any] | None) -> ThresholdConfig | None:
    if not value or not value.get("entity_id"):
        return None
    return ThresholdConfig(
        entity_id=str(value["entity_id"]),
        activate_at=float(value["activate_at"]),
        clear_at=float(value["clear_at"]),
    )


def _maximum_threshold(
    value: dict[str, Any] | None,
) -> MaximumThresholdConfig | None:
    if not value or not value.get("entity_id"):
        return None
    return MaximumThresholdConfig(
        entity_id=str(value["entity_id"]),
        activate_at_or_below=float(value["activate_at_or_below"]),
        clear_at_or_above=float(value["clear_at_or_above"]),
    )


def _endpoint(value: dict[str, Any] | None, default: time) -> TimeEndpoint:
    value = value or {}
    return TimeEndpoint(
        kind=EndpointType(value.get("kind", EndpointType.FIXED)),
        value=_time(value.get("value"), default),
        offset_minutes=int(value.get("offset_minutes", 0)),
    )


def _window(value: dict[str, Any]) -> TimeWindow:
    return TimeWindow(
        start=_endpoint(value.get("start"), time(0, 0)),
        end=_endpoint(value.get("end"), time(23, 59, 59)),
        weekdays=frozenset(int(day) for day in value.get("weekdays", range(7))),
    )


def cover_from_dict(value: dict[str, Any]) -> CoverConfig:
    """Parse and validate one stored cover configuration."""
    environment_value = value.get("environment") or {}
    wind_value = value.get("wind")
    maximum_elevation = value.get("maximum_elevation")
    manual_until = value.get("manual_override_until")
    cover = CoverConfig(
        id=str(value["id"]),
        name=str(value["name"]),
        entity_id=str(value["entity_id"]),
        area_id=value.get("area_id") or None,
        enabled=bool(value.get("enabled", True)),
        dry_run=bool(value.get("dry_run")),
        facade_azimuth=float(value.get("facade_azimuth", 180.0)),
        exposure_angle=float(value.get("exposure_angle", 55.0)),
        solar_exit_margin=float(value.get("solar_exit_margin", 3.0)),
        minimum_elevation=float(value.get("minimum_elevation", 5.0)),
        maximum_elevation=(
            float(maximum_elevation) if maximum_elevation is not None else None
        ),
        elevation_exit_margin=float(value.get("elevation_exit_margin", 1.0)),
        mode=AutomationMode(value.get("mode", AutomationMode.SUN)),
        normal_position=int(value.get("normal_position", 100)),
        shading_position=int(value.get("shading_position", 25)),
        normal_tilt=(
            int(value["normal_tilt"]) if value.get("normal_tilt") is not None else None
        ),
        shading_tilt=(
            int(value["shading_tilt"])
            if value.get("shading_tilt") is not None
            else None
        ),
        time_windows=tuple(_window(item) for item in value.get("time_windows", [])),
        environment=EnvironmentConfig(
            outdoor_temperature=_threshold(
                environment_value.get("outdoor_temperature")
            ),
            indoor_temperature=_threshold(environment_value.get("indoor_temperature")),
            illuminance=_threshold(environment_value.get("illuminance")),
            cloud_cover=_maximum_threshold(environment_value.get("cloud_cover")),
            weather_entity_id=environment_value.get("weather_entity_id") or None,
            allowed_weather_states=frozenset(
                environment_value.get("allowed_weather_states", [])
            ),
        ),
        activation_delay=int(value.get("activation_delay", 300)),
        clear_delay=int(value.get("clear_delay", 600)),
        minimum_movement_interval=int(value.get("minimum_movement_interval", 300)),
        manual_override_mode=ManualOverrideMode(
            value.get("manual_override_mode", ManualOverrideMode.MINUTES_60)
        ),
        manual_override_until=(
            _time(manual_until, time(0, 0)) if manual_until is not None else None
        ),
        safety_entities=tuple(value.get("safety_entities", [])),
        safety_policy=SafetyPolicy(
            value.get("safety_policy", SafetyPolicy.BLOCK_LOWERING)
        ),
        wind=(
            WindConfig(
                entity_id=str(wind_value["entity_id"]),
                unsafe_at=float(wind_value["unsafe_at"]),
                safe_at=float(wind_value["safe_at"]),
                retract=bool(wind_value.get("retract", True)),
            )
            if wind_value and wind_value.get("entity_id")
            else None
        ),
        advanced_match=AdvancedMatch(value.get("advanced_match", AdvancedMatch.ALL)),
        advanced_conditions=frozenset(
            ConditionKey(item) for item in value.get("advanced_conditions", [])
        ),
    )
    validate_cover(cover)
    return cover


def validate_cover(cover: CoverConfig) -> None:
    """Raise ValueError when a cover policy is internally inconsistent."""
    if not cover.id or not cover.name:
        msg = "cover id and name must not be empty"
        raise ValueError(msg)
    if not cover.entity_id.startswith("cover.") or cover.entity_id == "cover.":
        msg = "entity_id must identify a cover entity"
        raise ValueError(msg)
    if not 0 <= cover.facade_azimuth < 360:
        msg = "facade_azimuth must be in [0, 360)"
        raise ValueError(msg)
    if not 0 <= cover.exposure_angle <= 180:
        msg = "exposure_angle must be in [0, 180]"
        raise ValueError(msg)
    if cover.solar_exit_margin < 0 or cover.elevation_exit_margin < 0:
        msg = "hysteresis margins must not be negative"
        raise ValueError(msg)
    if cover.maximum_elevation is not None and (
        cover.maximum_elevation < cover.minimum_elevation
    ):
        msg = "maximum_elevation must not be below minimum_elevation"
        raise ValueError(msg)
    for position in (
        cover.normal_position,
        cover.shading_position,
        cover.normal_tilt,
        cover.shading_tilt,
    ):
        if position is not None and not 0 <= position <= 100:
            msg = "positions must be in [0, 100]"
            raise ValueError(msg)
    if cover.mode is AutomationMode.ADVANCED and not cover.advanced_conditions:
        msg = "advanced mode needs at least one selected condition"
        raise ValueError(msg)
    if cover.manual_override_mode is ManualOverrideMode.UNTIL_TIME and (
        cover.manual_override_until is None
    ):
        msg = "until-time override mode needs an expiry time"
        raise ValueError(msg)
    thresholds = (
        cover.environment.outdoor_temperature,
        cover.environment.indoor_temperature,
        cover.environment.illuminance,
    )
    if any(
        item is not None and item.clear_at > item.activate_at for item in thresholds
    ):
        msg = "minimum-condition clear threshold must not exceed activation threshold"
        raise ValueError(msg)
    cloud = cover.environment.cloud_cover
    if cloud is not None and cloud.clear_at_or_above < cloud.activate_at_or_below:
        msg = "cloud clear threshold must not be below activation threshold"
        raise ValueError(msg)
    if cover.wind is not None and cover.wind.safe_at > cover.wind.unsafe_at:
        msg = "wind safe threshold must not exceed unsafe threshold"
        raise ValueError(msg)
    if (
        min(
            cover.activation_delay,
            cover.clear_delay,
            cover.minimum_movement_interval,
        )
        < 0
    ):
        msg = "movement and stability delays must not be negative"
        raise ValueError(msg)


def parse_integration_config(
    data: dict[str, Any], options: dict[str, Any]
) -> IntegrationConfig:
    """Parse config-entry data/options into immutable domain models."""
    ZoneInfo(str(data[CONF_TIME_ZONE]))
    house = HouseConfig(
        name=str(data[CONF_HOUSE_NAME]),
        latitude=float(data[CONF_LATITUDE]),
        longitude=float(data[CONF_LONGITUDE]),
        time_zone=str(data[CONF_TIME_ZONE]),
        rotation=float(data.get(CONF_HOUSE_ROTATION, 0.0)),
    )
    if not house.name:
        msg = "house name must not be empty"
        raise ValueError(msg)
    if not -90 <= house.latitude <= 90 or not -180 <= house.longitude <= 180:
        msg = "house coordinates are outside their valid ranges"
        raise ValueError(msg)
    if not 0 <= house.rotation < 360:
        msg = "house rotation must be in [0, 360)"
        raise ValueError(msg)
    covers = tuple(cover_from_dict(item) for item in options.get(CONF_COVERS, []))
    cover_ids = [cover.id for cover in covers]
    if len(cover_ids) != len(set(cover_ids)):
        msg = "cover definition ids must be unique within a house"
        raise ValueError(msg)
    entity_ids = [cover.entity_id for cover in covers]
    if len(entity_ids) != len(set(entity_ids)):
        msg = "a physical cover can only be configured once per house"
        raise ValueError(msg)
    return IntegrationConfig(
        house=house,
        covers=covers,
        globally_enabled=bool(options.get(CONF_GLOBAL_ENABLED, True)),
        dry_run=bool(options.get(CONF_DRY_RUN, True)),
        reconcile_interval=max(
            60,
            int(options.get(CONF_RECONCILE_INTERVAL, DEFAULT_RECONCILE_INTERVAL)),
        ),
    )


def cover_to_dict(cover: CoverConfig) -> dict[str, Any]:
    """Serialize one cover for ConfigEntry.options."""
    return model_to_dict(cover)
