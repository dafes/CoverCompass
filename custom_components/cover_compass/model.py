"""Pure configuration and runtime domain models for CoverCompass."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from enum import StrEnum
from typing import Any, cast
from uuid import uuid4

from .const import (
    DEFAULT_ACTIVATION_DELAY,
    DEFAULT_CLEAR_DELAY,
    DEFAULT_EXPOSURE_ANGLE,
    DEFAULT_MIN_ELEVATION,
    DEFAULT_MIN_MOVEMENT_INTERVAL,
    DEFAULT_NORMAL_POSITION,
    DEFAULT_RECONCILE_INTERVAL,
    DEFAULT_SHADE_POSITION,
)


class AutomationMode(StrEnum):
    """Supported automation strategies."""

    DISABLED = "disabled"
    SUN = "sun"
    TIME = "time"
    SUN_AND_TIME = "sun_and_time"
    SUN_OR_TIME = "sun_or_time"
    ADVANCED = "advanced"


class DecisionType(StrEnum):
    """A rule evaluator outcome."""

    SHADE = "shade"
    OPEN = "open"
    HOLD = "hold"
    BLOCKED = "blocked"


class EndpointType(StrEnum):
    """Time-window endpoint types."""

    FIXED = "fixed"
    SUNRISE = "sunrise"
    SUNSET = "sunset"


class ManualOverrideMode(StrEnum):
    """Manual override policies."""

    DISABLED = "disabled"
    MINUTES_15 = "minutes_15"
    MINUTES_30 = "minutes_30"
    MINUTES_60 = "minutes_60"
    NEXT_TRANSITION = "next_transition"
    UNTIL_TIME = "until_time"
    MANUAL = "manual"


class SafetyPolicy(StrEnum):
    """Safety interlock movement policies."""

    BLOCK_LOWERING = "block_lowering"
    BLOCK_ALL = "block_all"


class AdvancedMatch(StrEnum):
    """Advanced-rule condition combination."""

    ALL = "all"
    ANY = "any"


class ConditionKey(StrEnum):
    """Condition keys usable by the advanced rule mode."""

    SUN = "sun"
    TIME = "time"
    OUTDOOR_TEMPERATURE = "outdoor_temperature"
    INDOOR_TEMPERATURE = "indoor_temperature"
    ILLUMINANCE = "illuminance"
    CLOUD_COVER = "cloud_cover"
    WEATHER = "weather"


ORIENTATION_DEGREES: dict[str, float] = {
    "n": 0.0,
    "ne": 45.0,
    "e": 90.0,
    "se": 135.0,
    "s": 180.0,
    "sw": 225.0,
    "w": 270.0,
    "nw": 315.0,
}


@dataclass(frozen=True, slots=True)
class HouseConfig:
    """House-wide configuration."""

    name: str
    latitude: float
    longitude: float
    time_zone: str
    rotation: float = 0.0


@dataclass(frozen=True, slots=True)
class TimeEndpoint:
    """One fixed or solar-relative time endpoint."""

    kind: EndpointType = EndpointType.FIXED
    value: time = time(0, 0)
    offset_minutes: int = 0


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A local-time window associated with its start weekday."""

    start: TimeEndpoint = TimeEndpoint()
    end: TimeEndpoint = TimeEndpoint(value=time(23, 59, 59))
    weekdays: frozenset[int] = frozenset(range(7))


@dataclass(frozen=True, slots=True)
class ThresholdConfig:
    """Start/stop thresholds for a minimum-value condition."""

    entity_id: str
    activate_at: float
    clear_at: float


@dataclass(frozen=True, slots=True)
class MaximumThresholdConfig:
    """Start/stop thresholds for a maximum-value condition."""

    entity_id: str
    activate_at_or_below: float
    clear_at_or_above: float


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    """Optional entity-based environmental conditions."""

    outdoor_temperature: ThresholdConfig | None = None
    indoor_temperature: ThresholdConfig | None = None
    illuminance: ThresholdConfig | None = None
    cloud_cover: MaximumThresholdConfig | None = None
    weather_entity_id: str | None = None
    allowed_weather_states: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class WindConfig:
    """Optional wind protection."""

    entity_id: str
    unsafe_at: float
    safe_at: float
    retract: bool = True


@dataclass(frozen=True, slots=True)
class CoverConfig:
    """Complete policy for one physical cover."""

    id: str
    name: str
    entity_id: str
    area_id: str | None = None
    enabled: bool = True
    dry_run: bool = False
    facade_azimuth: float = 180.0
    exposure_angle: float = DEFAULT_EXPOSURE_ANGLE
    solar_exit_margin: float = 3.0
    minimum_elevation: float = DEFAULT_MIN_ELEVATION
    maximum_elevation: float | None = None
    elevation_exit_margin: float = 1.0
    mode: AutomationMode = AutomationMode.SUN
    normal_position: int = DEFAULT_NORMAL_POSITION
    shading_position: int = DEFAULT_SHADE_POSITION
    normal_tilt: int | None = None
    shading_tilt: int | None = None
    time_windows: tuple[TimeWindow, ...] = ()
    environment: EnvironmentConfig = EnvironmentConfig()
    activation_delay: int = DEFAULT_ACTIVATION_DELAY
    clear_delay: int = DEFAULT_CLEAR_DELAY
    minimum_movement_interval: int = DEFAULT_MIN_MOVEMENT_INTERVAL
    manual_override_mode: ManualOverrideMode = ManualOverrideMode.MINUTES_60
    manual_override_until: time | None = None
    safety_entities: tuple[str, ...] = ()
    safety_policy: SafetyPolicy = SafetyPolicy.BLOCK_LOWERING
    wind: WindConfig | None = None
    advanced_match: AdvancedMatch = AdvancedMatch.ALL
    advanced_conditions: frozenset[ConditionKey] = frozenset()

    @property
    def movement_lowers_cover(self) -> bool:
        """Return whether shading normally lowers this cover."""
        return self.shading_position < self.normal_position


@dataclass(frozen=True, slots=True)
class IntegrationConfig:
    """Parsed config-entry configuration."""

    house: HouseConfig
    covers: tuple[CoverConfig, ...]
    globally_enabled: bool = True
    dry_run: bool = True
    reconcile_interval: int = DEFAULT_RECONCILE_INTERVAL


@dataclass(frozen=True, slots=True)
class SolarExposure:
    """Deterministic solar-exposure result."""

    solar_azimuth: float
    solar_elevation: float
    facade_azimuth: float
    angular_difference: float
    absolute_difference: float
    effective_exposure_angle: float
    within_horizontal_exposure: bool
    within_elevation_range: bool
    sun_exposed: bool


@dataclass(frozen=True, slots=True)
class EnvironmentReadings:
    """Current external readings supplied to the pure evaluator."""

    values: dict[str, float | None] = field(default_factory=dict)
    states: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    """All current facts required for one cover evaluation."""

    now: datetime
    sun_azimuth: float
    sun_elevation: float
    time_active: bool
    current_position: int | None
    current_tilt: int | None
    cover_available: bool = True
    safety_states: dict[str, bool | None] = field(default_factory=dict)
    readings: EnvironmentReadings = EnvironmentReadings()


@dataclass(slots=True)
class RuleRuntimeState:
    """Small amount of state needed for hysteresis and delays."""

    sun_exposed: bool = False
    environment_active: dict[str, bool] = field(default_factory=dict)
    wind_unsafe: bool = False
    shading_active: bool = False
    pending_target: DecisionType | None = None
    pending_since: datetime | None = None
    last_rule_transition: datetime | None = None
    manual_override: bool = False
    manual_override_expires: datetime | None = None
    manual_rule_signature: str | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """Explainable desired-state decision, separate from execution."""

    decision: DecisionType
    reason: str
    target_position: int | None
    target_tilt: int | None
    conditions: dict[str, bool | None]
    solar: SolarExposure
    rule_signature: str
    safety_active: bool = False


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """An intended command used to identify its resulting state changes."""

    issued_at: datetime
    start_position: int | None
    target_position: int | None
    target_tilt: int | None
    context_id: str


def new_cover_id() -> str:
    """Return a stable ID for a new configured cover."""
    return uuid4().hex


def model_to_dict(value: Any) -> dict[str, Any]:
    """Convert a dataclass to JSON-compatible config-entry data."""

    def convert(item: Any) -> Any:
        if isinstance(item, StrEnum):
            return item.value
        if isinstance(item, time):
            return item.isoformat()
        if isinstance(item, frozenset | tuple):
            return [convert(part) for part in item]
        if isinstance(item, dict):
            return {str(key): convert(part) for key, part in item.items()}
        return item

    return cast("dict[str, Any]", convert(asdict(value)))
