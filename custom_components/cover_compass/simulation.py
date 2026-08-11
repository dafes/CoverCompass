"""Pure simulation helper for commissioning and tests."""

from __future__ import annotations

from datetime import datetime

from .model import (
    CoverConfig,
    Decision,
    EnvironmentReadings,
    EvaluationInput,
    IntegrationConfig,
    RuleRuntimeState,
)
from .rules import evaluate_cover


def simulate(
    integration: IntegrationConfig,
    cover: CoverConfig,
    *,
    now: datetime,
    solar_azimuth: float,
    solar_elevation: float,
    time_active: bool,
    current_position: int | None,
    current_tilt: int | None = None,
    numeric_readings: dict[str, float | None] | None = None,
    state_readings: dict[str, str | None] | None = None,
    safety_states: dict[str, bool | None] | None = None,
    runtime: RuleRuntimeState | None = None,
) -> Decision:
    """Evaluate arbitrary inputs without starting Home Assistant."""
    return evaluate_cover(
        integration,
        cover,
        EvaluationInput(
            now=now,
            sun_azimuth=solar_azimuth,
            sun_elevation=solar_elevation,
            time_active=time_active,
            current_position=current_position,
            current_tilt=current_tilt,
            safety_states=safety_states or {},
            readings=EnvironmentReadings(
                values=numeric_readings or {}, states=state_readings or {}
            ),
        ),
        runtime or RuleRuntimeState(),
    )
