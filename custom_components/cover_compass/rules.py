"""Pure explainable rule evaluation for CoverCompass."""

from __future__ import annotations

from datetime import timedelta

from .model import (
    AdvancedMatch,
    AutomationMode,
    ConditionKey,
    CoverConfig,
    Decision,
    DecisionType,
    EvaluationInput,
    IntegrationConfig,
    MaximumThresholdConfig,
    RuleRuntimeState,
    SolarExposure,
    ThresholdConfig,
)
from .solar import calculate_solar_exposure, normalize_angle


def _minimum_condition(
    threshold: ThresholdConfig,
    value: float | None,
    previously_active: bool,
) -> bool | None:
    if value is None:
        return None
    if previously_active:
        return value > threshold.clear_at
    return value >= threshold.activate_at


def _maximum_condition(
    threshold: MaximumThresholdConfig,
    value: float | None,
    previously_active: bool,
) -> bool | None:
    if value is None:
        return None
    if previously_active:
        return value < threshold.clear_at_or_above
    return value <= threshold.activate_at_or_below


def _environment_conditions(
    cover: CoverConfig,
    inputs: EvaluationInput,
    runtime: RuleRuntimeState,
) -> dict[str, bool | None]:
    environment = cover.environment
    conditions: dict[str, bool | None] = {}
    minimums = {
        ConditionKey.OUTDOOR_TEMPERATURE: environment.outdoor_temperature,
        ConditionKey.INDOOR_TEMPERATURE: environment.indoor_temperature,
        ConditionKey.ILLUMINANCE: environment.illuminance,
    }
    for key, threshold in minimums.items():
        if threshold is None:
            continue
        result = _minimum_condition(
            threshold,
            inputs.readings.values.get(threshold.entity_id),
            runtime.environment_active.get(key, False),
        )
        conditions[key] = result
        if result is not None:
            runtime.environment_active[key] = result
    cloud = environment.cloud_cover
    if cloud is not None:
        result = _maximum_condition(
            cloud,
            inputs.readings.values.get(cloud.entity_id),
            runtime.environment_active.get(ConditionKey.CLOUD_COVER, False),
        )
        conditions[ConditionKey.CLOUD_COVER] = result
        if result is not None:
            runtime.environment_active[ConditionKey.CLOUD_COVER] = result
    if environment.weather_entity_id:
        state = inputs.readings.states.get(environment.weather_entity_id)
        conditions[ConditionKey.WEATHER] = (
            None if state is None else state in environment.allowed_weather_states
        )
    return conditions


def _base_condition(
    cover: CoverConfig,
    conditions: dict[str, bool | None],
) -> bool | None:
    sun = conditions[ConditionKey.SUN]
    time_active = conditions[ConditionKey.TIME]
    environment_keys = {
        key for key in conditions if key not in (ConditionKey.SUN, ConditionKey.TIME)
    }
    environment_results = [conditions[key] for key in environment_keys]
    if cover.mode is AutomationMode.SUN:
        base = sun
    elif cover.mode is AutomationMode.TIME:
        base = time_active
    elif cover.mode is AutomationMode.SUN_AND_TIME:
        base = sun and time_active
    elif cover.mode is AutomationMode.SUN_OR_TIME:
        base = sun or time_active
    elif cover.mode is AutomationMode.ADVANCED:
        selected = [conditions.get(key) for key in cover.advanced_conditions]
        if any(result is None for result in selected):
            return None
        if cover.advanced_match is AdvancedMatch.ALL:
            return all(selected)
        return any(selected)
    else:
        return False
    if base and any(result is None for result in environment_results):
        return None
    return bool(base and all(environment_results))


def _signature(conditions: dict[str, bool | None], raw_shade: bool | None) -> str:
    values = ",".join(f"{key}={conditions[key]}" for key in sorted(conditions, key=str))
    return f"shade={raw_shade};{values}"


def _decision(
    decision_type: DecisionType,
    reason: str,
    cover: CoverConfig,
    solar: SolarExposure,
    conditions: dict[str, bool | None],
    signature: str,
    *,
    safety_active: bool = False,
    wind_retract: bool = False,
) -> Decision:
    if decision_type is DecisionType.SHADE:
        position = cover.shading_position
        tilt = cover.shading_tilt
    elif decision_type is DecisionType.OPEN:
        position = 100 if wind_retract else cover.normal_position
        tilt = cover.normal_tilt
    else:
        position = None
        tilt = None
    return Decision(
        decision=decision_type,
        reason=reason,
        target_position=position,
        target_tilt=tilt,
        conditions={str(key): value for key, value in conditions.items()},
        solar=solar,
        rule_signature=signature,
        safety_active=safety_active,
    )


def _manual_override_active(
    runtime: RuleRuntimeState,
    inputs: EvaluationInput,
    signature: str,
) -> bool:
    if not runtime.manual_override:
        return False
    if (
        runtime.manual_override_expires is not None
        and inputs.now >= runtime.manual_override_expires
    ):
        runtime.manual_override = False
        runtime.manual_override_expires = None
        runtime.manual_rule_signature = None
        return False
    if runtime.manual_rule_signature is not None and (
        runtime.manual_rule_signature != signature
    ):
        runtime.manual_override = False
        runtime.manual_rule_signature = None
        return False
    return True


def _update_wind(
    cover: CoverConfig,
    inputs: EvaluationInput,
    runtime: RuleRuntimeState,
) -> bool | None:
    if cover.wind is None:
        return False
    value = inputs.readings.values.get(cover.wind.entity_id)
    if value is None:
        return None
    if runtime.wind_unsafe:
        runtime.wind_unsafe = value > cover.wind.safe_at
    else:
        runtime.wind_unsafe = value >= cover.wind.unsafe_at
    return runtime.wind_unsafe


def evaluate_cover(
    integration: IntegrationConfig,
    cover: CoverConfig,
    inputs: EvaluationInput,
    runtime: RuleRuntimeState,
) -> Decision:
    """Evaluate priorities, rules, hysteresis and delays without performing I/O."""
    effective_facade = normalize_angle(
        cover.facade_azimuth + integration.house.rotation
    )
    solar = calculate_solar_exposure(
        facade_azimuth=effective_facade,
        solar_azimuth=inputs.sun_azimuth,
        solar_elevation=inputs.sun_elevation,
        exposure_angle=cover.exposure_angle,
        minimum_elevation=cover.minimum_elevation,
        maximum_elevation=cover.maximum_elevation,
        previously_exposed=runtime.sun_exposed,
        horizontal_exit_margin=cover.solar_exit_margin,
        elevation_exit_margin=cover.elevation_exit_margin,
    )
    runtime.sun_exposed = solar.sun_exposed
    conditions: dict[str, bool | None] = {
        ConditionKey.SUN: solar.sun_exposed,
        ConditionKey.TIME: inputs.time_active,
    }
    conditions.update(_environment_conditions(cover, inputs, runtime))
    raw_shade = _base_condition(cover, conditions)
    signature = _signature(conditions, raw_shade)

    if not inputs.cover_available:
        return _decision(
            DecisionType.HOLD,
            "The physical cover is unavailable; no command is safe.",
            cover,
            solar,
            conditions,
            signature,
        )
    if not integration.globally_enabled:
        return _decision(
            DecisionType.HOLD,
            "CoverCompass automation is globally disabled.",
            cover,
            solar,
            conditions,
            signature,
        )

    wind_unsafe = _update_wind(cover, inputs, runtime)
    if wind_unsafe and cover.wind is not None and cover.wind.retract:
        return _decision(
            DecisionType.OPEN,
            "Unsafe wind is active; the cover must retract fully.",
            cover,
            solar,
            conditions,
            signature,
            safety_active=True,
            wind_retract=True,
        )

    if not cover.enabled or cover.mode is AutomationMode.DISABLED:
        return _decision(
            DecisionType.HOLD,
            "Automation is disabled for this cover.",
            cover,
            solar,
            conditions,
            signature,
        )
    if _manual_override_active(runtime, inputs, signature):
        return _decision(
            DecisionType.HOLD,
            "A manual override is active.",
            cover,
            solar,
            conditions,
            signature,
        )
    if raw_shade is None:
        return _decision(
            DecisionType.HOLD,
            "A configured environmental condition is unavailable.",
            cover,
            solar,
            conditions,
            signature,
        )

    desired = DecisionType.SHADE if raw_shade else DecisionType.OPEN
    waiting_for_clear = False
    delay = cover.activation_delay if raw_shade else cover.clear_delay
    current_latched = (
        DecisionType.SHADE if runtime.shading_active else DecisionType.OPEN
    )
    if desired is not current_latched:
        if runtime.pending_target is not desired:
            runtime.pending_target = desired
            runtime.pending_since = inputs.now
        assert runtime.pending_since is not None
        if inputs.now - runtime.pending_since < timedelta(seconds=delay):
            if runtime.shading_active:
                desired = DecisionType.SHADE
                waiting_for_clear = True
            else:
                return _decision(
                    DecisionType.HOLD,
                    "The shading condition is waiting for its activation delay.",
                    cover,
                    solar,
                    conditions,
                    signature,
                )
        else:
            runtime.shading_active = raw_shade
            runtime.pending_target = None
            runtime.pending_since = None
            runtime.last_rule_transition = inputs.now
    else:
        runtime.pending_target = None
        runtime.pending_since = None

    target = (
        cover.shading_position
        if desired is DecisionType.SHADE
        else cover.normal_position
    )
    lowering = (
        target < inputs.current_position
        if inputs.current_position is not None
        else (desired is DecisionType.SHADE and cover.movement_lowers_cover)
    )
    active_interlocks = [
        entity_id
        for entity_id, active in inputs.safety_states.items()
        if active is not False
    ]
    if active_interlocks and (cover.safety_policy.value == "block_all" or lowering):
        names = ", ".join(active_interlocks)
        return _decision(
            DecisionType.BLOCKED,
            f"Movement is blocked by safety interlock: {names}.",
            cover,
            solar,
            conditions,
            signature,
            safety_active=True,
        )
    if wind_unsafe is None and lowering:
        return _decision(
            DecisionType.BLOCKED,
            "The configured wind sensor is unavailable; lowering is blocked.",
            cover,
            solar,
            conditions,
            signature,
            safety_active=True,
        )
    if wind_unsafe and lowering:
        return _decision(
            DecisionType.BLOCKED,
            "Unsafe wind is active; deployment is blocked.",
            cover,
            solar,
            conditions,
            signature,
            safety_active=True,
        )

    if waiting_for_clear:
        reason = "The clearing delay is active; the cover remains shaded."
    elif desired is DecisionType.SHADE:
        reason = (
            f"Shading conditions are active: sun is {solar.absolute_difference:.1f}° "
            f"from the facade at {solar.solar_elevation:.1f}° elevation."
        )
    else:
        reason = "The configured shading conditions are not active."
    return _decision(desired, reason, cover, solar, conditions, signature)
