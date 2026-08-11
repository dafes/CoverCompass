"""Tests for modes, environment, delays, overrides and safety priority."""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.cover_compass.model import (
    AdvancedMatch,
    AutomationMode,
    ConditionKey,
    CoverConfig,
    DecisionType,
    EnvironmentConfig,
    EnvironmentReadings,
    EvaluationInput,
    HouseConfig,
    IntegrationConfig,
    MaximumThresholdConfig,
    RuleRuntimeState,
    SafetyPolicy,
    ThresholdConfig,
    WindConfig,
)
from custom_components.cover_compass.rules import evaluate_cover
from custom_components.cover_compass.simulation import simulate

NOW = datetime(2026, 8, 10, 10, tzinfo=ZoneInfo("Europe/Berlin"))
HOUSE = HouseConfig("Home", 52.5, 13.4, "Europe/Berlin")


def make_cover(**changes) -> CoverConfig:
    return replace(
        CoverConfig(
            id="kitchen",
            name="Kitchen",
            entity_id="cover.kitchen",
            facade_azimuth=135,
            exposure_angle=55,
            minimum_elevation=10,
            activation_delay=0,
            clear_delay=0,
            minimum_movement_interval=0,
        ),
        **changes,
    )


def evaluate(
    cover: CoverConfig,
    runtime: RuleRuntimeState | None = None,
    *,
    sun: bool = True,
    time_active: bool = True,
    readings: EnvironmentReadings | None = None,
    safety: dict[str, bool | None] | None = None,
    now: datetime = NOW,
    position: int | None = 100,
    enabled: bool = True,
):
    integration = IntegrationConfig(
        HOUSE, (cover,), globally_enabled=enabled, dry_run=False
    )
    return evaluate_cover(
        integration,
        cover,
        EvaluationInput(
            now=now,
            sun_azimuth=135 if sun else 270,
            sun_elevation=30,
            time_active=time_active,
            current_position=position,
            current_tilt=None,
            safety_states=safety or {},
            readings=readings or EnvironmentReadings(),
        ),
        runtime or RuleRuntimeState(),
    )


@pytest.mark.parametrize(
    ("mode", "sun", "time_active", "expected"),
    [
        (AutomationMode.SUN, True, False, DecisionType.SHADE),
        (AutomationMode.SUN, False, True, DecisionType.OPEN),
        (AutomationMode.TIME, False, True, DecisionType.SHADE),
        (AutomationMode.TIME, True, False, DecisionType.OPEN),
        (AutomationMode.SUN_AND_TIME, True, True, DecisionType.SHADE),
        (AutomationMode.SUN_AND_TIME, True, False, DecisionType.OPEN),
        (AutomationMode.SUN_OR_TIME, False, True, DecisionType.SHADE),
        (AutomationMode.SUN_OR_TIME, False, False, DecisionType.OPEN),
        (AutomationMode.DISABLED, True, True, DecisionType.HOLD),
    ],
)
def test_rule_modes(mode, sun, time_active, expected) -> None:
    assert (
        evaluate(make_cover(mode=mode), sun=sun, time_active=time_active).decision
        is expected
    )


def test_advanced_rules_all_and_any() -> None:
    all_cover = make_cover(
        mode=AutomationMode.ADVANCED,
        advanced_match=AdvancedMatch.ALL,
        advanced_conditions=frozenset({ConditionKey.SUN, ConditionKey.TIME}),
    )
    any_cover = replace(all_cover, advanced_match=AdvancedMatch.ANY)
    assert (
        evaluate(all_cover, sun=True, time_active=False).decision is DecisionType.OPEN
    )
    assert (
        evaluate(any_cover, sun=True, time_active=False).decision is DecisionType.SHADE
    )


def test_environment_threshold_hysteresis_and_unavailable() -> None:
    temperature = ThresholdConfig("sensor.outdoor", activate_at=24, clear_at=22.5)
    cover = make_cover(environment=EnvironmentConfig(outdoor_temperature=temperature))
    runtime = RuleRuntimeState()
    assert (
        evaluate(
            cover,
            runtime,
            readings=EnvironmentReadings(values={"sensor.outdoor": 23.9}),
        ).decision
        is DecisionType.OPEN
    )
    assert (
        evaluate(
            cover,
            runtime,
            readings=EnvironmentReadings(values={"sensor.outdoor": 24}),
        ).decision
        is DecisionType.SHADE
    )
    assert (
        evaluate(
            cover,
            runtime,
            readings=EnvironmentReadings(values={"sensor.outdoor": 23}),
        ).decision
        is DecisionType.SHADE
    )
    assert (
        evaluate(
            cover,
            runtime,
            readings=EnvironmentReadings(values={"sensor.outdoor": 22.5}),
        ).decision
        is DecisionType.OPEN
    )
    assert (
        evaluate(
            cover,
            runtime,
            readings=EnvironmentReadings(values={"sensor.outdoor": None}),
        ).decision
        is DecisionType.HOLD
    )


def test_cloud_maximum_hysteresis() -> None:
    cover = make_cover(
        environment=EnvironmentConfig(
            cloud_cover=MaximumThresholdConfig("sensor.cloud", 40, 55)
        )
    )
    runtime = RuleRuntimeState()
    assert (
        evaluate(
            cover,
            runtime,
            readings=EnvironmentReadings(values={"sensor.cloud": 35}),
        ).decision
        is DecisionType.SHADE
    )
    assert (
        evaluate(
            cover,
            runtime,
            readings=EnvironmentReadings(values={"sensor.cloud": 50}),
        ).decision
        is DecisionType.SHADE
    )
    assert (
        evaluate(
            cover,
            runtime,
            readings=EnvironmentReadings(values={"sensor.cloud": 55}),
        ).decision
        is DecisionType.OPEN
    )


def test_activation_and_clearing_delays() -> None:
    cover = make_cover(activation_delay=300, clear_delay=600)
    runtime = RuleRuntimeState()
    assert evaluate(cover, runtime, now=NOW).decision is DecisionType.HOLD
    assert (
        evaluate(cover, runtime, now=NOW + timedelta(minutes=5)).decision
        is DecisionType.SHADE
    )
    assert (
        clearing := evaluate(
            cover, runtime, sun=False, now=NOW + timedelta(minutes=6), position=20
        )
    ).decision is DecisionType.SHADE
    assert "clearing delay" in clearing.reason
    assert (
        evaluate(
            cover, runtime, sun=False, now=NOW + timedelta(minutes=16), position=20
        ).decision
        is DecisionType.OPEN
    )


def test_manual_override_expiry_and_next_transition() -> None:
    cover = make_cover()
    runtime = RuleRuntimeState(
        manual_override=True, manual_override_expires=NOW + timedelta(hours=1)
    )
    assert evaluate(cover, runtime, now=NOW).decision is DecisionType.HOLD
    assert (
        evaluate(cover, runtime, now=NOW + timedelta(hours=1)).decision
        is DecisionType.SHADE
    )

    baseline = evaluate(cover)
    runtime = RuleRuntimeState(
        manual_override=True, manual_rule_signature=baseline.rule_signature
    )
    assert evaluate(cover, runtime).decision is DecisionType.HOLD
    assert evaluate(cover, runtime, sun=False).decision is DecisionType.OPEN
    assert runtime.manual_override is False


def test_safety_door_and_clearing() -> None:
    cover = make_cover(safety_entities=("binary_sensor.door",))
    runtime = RuleRuntimeState()
    blocked = evaluate(
        cover, runtime, safety={"binary_sensor.door": True}, position=100
    )
    assert blocked.decision is DecisionType.BLOCKED
    assert blocked.safety_active is True
    cleared = evaluate(
        cover, runtime, safety={"binary_sensor.door": False}, position=100
    )
    assert cleared.decision is DecisionType.SHADE


def test_interlock_block_all_blocks_opening() -> None:
    cover = make_cover(
        safety_entities=("binary_sensor.door",),
        safety_policy=SafetyPolicy.BLOCK_ALL,
    )
    assert (
        evaluate(
            cover,
            safety={"binary_sensor.door": True},
            sun=False,
            position=20,
        ).decision
        is DecisionType.BLOCKED
    )


def test_wind_retracts_before_manual_override() -> None:
    cover = make_cover(wind=WindConfig("sensor.wind", unsafe_at=40, safe_at=30))
    runtime = RuleRuntimeState(manual_override=True)
    decision = evaluate(
        cover,
        runtime,
        position=20,
        readings=EnvironmentReadings(values={"sensor.wind": 45}),
    )
    assert decision.decision is DecisionType.OPEN
    assert decision.target_position == 100
    assert decision.safety_active is True


def test_global_disable_is_absolute_even_in_wind() -> None:
    cover = make_cover(wind=WindConfig("sensor.wind", unsafe_at=40, safe_at=30))
    decision = evaluate(
        cover,
        enabled=False,
        readings=EnvironmentReadings(values={"sensor.wind": 45}),
    )
    assert decision.decision is DecisionType.HOLD


def test_restart_evaluation_is_deterministic() -> None:
    cover = make_cover()
    first = evaluate(cover, RuleRuntimeState())
    second = evaluate(cover, RuleRuntimeState())
    assert first == second


def test_representative_kitchen_scenario() -> None:
    cover = make_cover(
        mode=AutomationMode.SUN_AND_TIME,
        environment=EnvironmentConfig(
            outdoor_temperature=ThresholdConfig("sensor.outdoor", 23, 22.5)
        ),
        activation_delay=300,
        clear_delay=600,
        shading_position=20,
        normal_position=100,
    )
    runtime = RuleRuntimeState()
    cold = EnvironmentReadings(values={"sensor.outdoor": 18})
    warm = EnvironmentReadings(values={"sensor.outdoor": 24})
    assert (
        evaluate(cover, runtime, sun=False, readings=cold).decision is DecisionType.OPEN
    )
    assert evaluate(cover, runtime, readings=cold).decision is DecisionType.OPEN
    assert evaluate(cover, runtime, readings=warm).decision is DecisionType.HOLD
    shaded = evaluate(cover, runtime, readings=warm, now=NOW + timedelta(minutes=5))
    assert shaded.decision is DecisionType.SHADE
    assert shaded.target_position == 20
    runtime.manual_override = True
    runtime.manual_override_expires = NOW + timedelta(minutes=65)
    assert (
        evaluate(cover, runtime, readings=warm, now=NOW + timedelta(minutes=6)).decision
        is DecisionType.HOLD
    )
    assert (
        evaluate(
            cover, runtime, readings=warm, now=NOW + timedelta(minutes=65)
        ).decision
        is DecisionType.SHADE
    )
    assert (
        evaluate(
            cover,
            runtime,
            readings=warm,
            sun=False,
            now=NOW + timedelta(minutes=66),
            position=20,
        ).decision
        is DecisionType.SHADE
    )
    assert (
        evaluate(
            cover,
            runtime,
            readings=warm,
            sun=False,
            now=NOW + timedelta(minutes=76),
            position=20,
        ).decision
        is DecisionType.OPEN
    )


def test_weather_condition_and_simulation_helper() -> None:
    cover = make_cover(
        environment=EnvironmentConfig(
            weather_entity_id="weather.home",
            allowed_weather_states=frozenset({"sunny"}),
        )
    )
    integration = IntegrationConfig(HOUSE, (cover,), dry_run=True)
    blocked = simulate(
        integration,
        cover,
        now=NOW,
        solar_azimuth=135,
        solar_elevation=30,
        time_active=True,
        current_position=100,
        state_readings={"weather.home": "rainy"},
    )
    assert blocked.decision is DecisionType.OPEN
    allowed = evaluate(
        cover,
        readings=EnvironmentReadings(states={"weather.home": "sunny"}),
    )
    assert allowed.decision is DecisionType.SHADE


def test_wind_unavailable_and_non_retracting_block() -> None:
    cover = make_cover(wind=WindConfig("sensor.wind", 40, 30, retract=False))
    unavailable = evaluate(
        cover,
        readings=EnvironmentReadings(values={"sensor.wind": None}),
        position=100,
    )
    assert unavailable.decision is DecisionType.BLOCKED
    unsafe = evaluate(
        cover,
        readings=EnvironmentReadings(values={"sensor.wind": 45}),
        position=100,
    )
    assert unsafe.decision is DecisionType.BLOCKED
