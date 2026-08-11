"""CoverCompass status and solar diagnostic sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import DEGREE, STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTR_CURRENT_POSITION
from .entity import CoverCompassEntity
from .model import AutomationMode, CoverConfig, DecisionType
from .runtime import CoverCompassRuntime


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry[CoverCompassRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up house and per-cover status sensors."""
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        HouseSolarSensor(runtime, "azimuth"),
        HouseSolarSensor(runtime, "elevation"),
        ActiveShadingCountSensor(runtime),
        ManualOverrideCountSensor(runtime),
    ]
    for cover in runtime.config.covers:
        entities.extend([
            CoverStatusSensor(runtime, cover),
            CoverSolarAngleSensor(runtime, cover),
        ])
    async_add_entities(entities)


class HouseSolarSensor(CoverCompassEntity, SensorEntity):
    """Current solar azimuth or elevation for the configured house."""

    _attr_native_unit_of_measurement = DEGREE
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, runtime: CoverCompassRuntime, kind: str) -> None:
        super().__init__(runtime, f"solar_{kind}")
        self.kind = kind
        self._attr_translation_key = f"solar_{kind}"

    @property
    def native_value(self) -> float:
        """Return the requested solar coordinate."""
        if self.kind == "azimuth":
            return round(self.runtime.solar_azimuth, 1)
        return round(self.runtime.solar_elevation, 1)


class ActiveShadingCountSensor(CoverCompassEntity, SensorEntity):
    """Number of covers with a SHADE desired state."""

    _attr_translation_key = "active_shading_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, runtime: CoverCompassRuntime) -> None:
        super().__init__(runtime, "active_shading_count")

    @property
    def native_value(self) -> int:
        return sum(
            decision.decision is DecisionType.SHADE
            for decision in self.runtime.decisions.values()
        )


class ManualOverrideCountSensor(CoverCompassEntity, SensorEntity):
    """Number of currently overridden covers."""

    _attr_translation_key = "manual_override_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, runtime: CoverCompassRuntime) -> None:
        super().__init__(runtime, "manual_override_count")

    @property
    def native_value(self) -> int:
        return sum(state.manual_override for state in self.runtime.rule_states.values())


class CoverStatusSensor(CoverCompassEntity, SensorEntity):
    """Explainable current CoverCompass status for one cover."""

    _attr_translation_key = "cover_status"

    def __init__(self, runtime: CoverCompassRuntime, cover: CoverConfig) -> None:
        super().__init__(runtime, f"{cover.id}_status", cover=cover)

    @property
    def native_value(self) -> str:
        """Return a compact translated-state key."""
        cover = self.cover
        assert cover is not None
        physical = self.hass.states.get(cover.entity_id)
        if physical is None or physical.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return "unavailable"
        runtime_state = self.runtime.rule_states[cover.id]
        if runtime_state.manual_override:
            return "manual_override"
        decision = self.runtime.decisions.get(cover.id)
        if decision is None:
            return "unavailable"
        if decision.decision is DecisionType.BLOCKED:
            return "blocked"
        if (
            not self.runtime.config.globally_enabled
            or not cover.enabled
            or cover.mode is AutomationMode.DISABLED
        ):
            return "paused"
        if decision.decision is DecisionType.SHADE or runtime_state.shading_active:
            return "shading"
        return "open"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose bounded structured 'why' diagnostics."""
        cover = self.cover
        assert cover is not None
        decision = self.runtime.decisions.get(cover.id)
        if decision is None:
            return {"cover_entity": cover.entity_id}
        physical = self.hass.states.get(cover.entity_id)
        runtime_state = self.runtime.rule_states[cover.id]
        controller = self.runtime.controllers[cover.id]
        execution = self.runtime.executions.get(cover.id)
        readings = self.runtime.latest_readings.get(cover.id)
        last_command: datetime | None = controller.last_automatic_command
        return {
            "cover_entity": cover.entity_id,
            "facade_orientation": cover.facade_azimuth,
            "effective_facade_orientation": decision.solar.facade_azimuth,
            "solar_azimuth": round(decision.solar.solar_azimuth, 1),
            "solar_elevation": round(decision.solar.solar_elevation, 1),
            "angle_to_facade": round(decision.solar.absolute_difference, 1),
            "sun_exposed": decision.solar.sun_exposed,
            "conditions": decision.conditions,
            "environment_readings": (
                {**readings.values, **readings.states} if readings is not None else {}
            ),
            "desired_position": decision.target_position,
            "current_position": (
                physical.attributes.get(ATTR_CURRENT_POSITION)
                if physical is not None
                else None
            ),
            "decision": decision.decision,
            "decision_reason": decision.reason,
            "execution_reason": execution.reason if execution is not None else None,
            "last_automatic_command": (
                last_command.isoformat() if last_command is not None else None
            ),
            "last_rule_transition": (
                runtime_state.last_rule_transition.isoformat()
                if runtime_state.last_rule_transition is not None
                else None
            ),
            "manual_override_expiry": (
                runtime_state.manual_override_expires.isoformat()
                if runtime_state.manual_override_expires is not None
                else None
            ),
            "dry_run": self.runtime.config.dry_run or cover.dry_run,
        }


class CoverSolarAngleSensor(CoverCompassEntity, SensorEntity):
    """Smallest current angle between sun and facade."""

    _attr_translation_key = "solar_angle"
    _attr_native_unit_of_measurement = DEGREE
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, runtime: CoverCompassRuntime, cover: CoverConfig) -> None:
        super().__init__(runtime, f"{cover.id}_solar_angle", cover=cover)

    @property
    def native_value(self) -> float | None:
        decision = self.runtime.decisions.get(self.cover_id or "")
        return (
            round(decision.solar.absolute_difference, 1)
            if decision is not None
            else None
        )
