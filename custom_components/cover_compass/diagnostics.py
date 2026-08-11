"""Privacy-aware diagnostics for CoverCompass."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CoverCompassConfigEntry
from .const import CONF_LATITUDE, CONF_LONGITUDE


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: CoverCompassConfigEntry
) -> dict[str, Any]:
    """Return sanitized configuration, capabilities and evaluator state."""
    runtime = entry.runtime_data
    capabilities: dict[str, object] = {}
    for cover in runtime.config.covers:
        state = runtime.hass.states.get(cover.entity_id)
        capabilities[cover.id] = {
            "entity_id": cover.entity_id,
            "available": state is not None and state.state != "unavailable",
            "supported_features": (
                state.attributes.get("supported_features")
                if state is not None
                else None
            ),
        }
    return {
        "integration_version": "1.0.0",
        "entry_data": async_redact_data(
            dict(entry.data), {CONF_LATITUDE, CONF_LONGITUDE}
        ),
        "options": dict(entry.options),
        "solar": {
            "azimuth": runtime.solar_azimuth,
            "elevation": runtime.solar_elevation,
        },
        "cover_capabilities": capabilities,
        "decisions": {
            cover_id: {
                "decision": decision.decision,
                "reason": decision.reason,
                "target_position": decision.target_position,
                "target_tilt": decision.target_tilt,
                "conditions": decision.conditions,
                "environment_readings": (
                    {
                        **runtime.latest_readings[cover_id].values,
                        **runtime.latest_readings[cover_id].states,
                    }
                    if cover_id in runtime.latest_readings
                    else {}
                ),
                "solar": {
                    "facade_azimuth": decision.solar.facade_azimuth,
                    "angular_difference": decision.solar.angular_difference,
                    "horizontal": decision.solar.within_horizontal_exposure,
                    "elevation": decision.solar.within_elevation_range,
                    "exposed": decision.solar.sun_exposed,
                },
                "execution": (
                    runtime.executions[cover_id].reason
                    if cover_id in runtime.executions
                    else None
                ),
            }
            for cover_id, decision in runtime.decisions.items()
        },
        "manual_overrides": {
            cover_id: {
                "active": state.manual_override,
                "expires": (
                    state.manual_override_expires.isoformat()
                    if state.manual_override_expires is not None
                    else None
                ),
            }
            for cover_id, state in runtime.rule_states.items()
        },
    }
