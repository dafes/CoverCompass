"""Tests for integration setup, reconciliation and restart behavior."""

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.const import STATE_OPEN, STATE_UNAVAILABLE
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cover_compass.const import DOMAIN
from custom_components.cover_compass.model import (
    DecisionType,
    EnvironmentConfig,
    ManualOverrideMode,
    ThresholdConfig,
    WindConfig,
)

from .helpers import ENTRY_DATA, make_cover, options_for


async def setup_entry(hass, cover=None, *, dry_run=True):
    """Set up one CoverCompass entry."""
    cover = cover or make_cover(minimum_elevation=-90)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Home",
        data=ENTRY_DATA,
        options=options_for(cover, dry_run=dry_run),
        version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_entities_reload_and_unload(
    hass, enable_custom_integrations
) -> None:
    hass.states.async_set(
        "cover.kitchen",
        STATE_OPEN,
        {
            "current_position": 100,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    entry = await setup_entry(hass)
    runtime = entry.runtime_data
    assert runtime.decisions["kitchen"].decision is DecisionType.SHADE
    assert runtime.executions["kitchen"].command_sent is False
    assert "Dry run" in runtime.executions["kitchen"].reason
    first_runtime = runtime
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is not first_runtime
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_virtual_devices_use_area_and_current_via_device_api(
    hass, enable_custom_integrations
) -> None:
    area = ar.async_get(hass).async_create("Kitchen")
    hass.states.async_set("cover.kitchen", STATE_OPEN, {"current_position": 100})
    entry = await setup_entry(hass, make_cover(area_id=area.id))
    registry = dr.async_get(hass)
    house = registry.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}:house")})
    managed = registry.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}:cover:kitchen")}
    )
    assert house is not None
    assert managed is not None
    assert managed.via_device_id == house.id
    assert managed.area_id == area.id

    options = {**entry.options, "covers": []}
    hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert (
        registry.async_get_device(
            identifiers={(DOMAIN, f"{entry.entry_id}:cover:kitchen")}
        )
        is None
    )


async def test_unavailable_cover_recovers_without_blind_retry(
    hass, enable_custom_integrations
) -> None:
    hass.states.async_set("cover.kitchen", STATE_UNAVAILABLE)
    entry = await setup_entry(hass)
    runtime = entry.runtime_data
    assert runtime.decisions["kitchen"].decision is DecisionType.HOLD
    hass.states.async_set(
        "cover.kitchen",
        STATE_OPEN,
        {
            "current_position": 100,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    await hass.async_block_till_done()
    assert runtime.decisions["kitchen"].decision is DecisionType.SHADE
    assert runtime.rule_states["kitchen"].manual_override is False


async def test_external_change_activates_and_resume_clears_override(
    hass, enable_custom_integrations
) -> None:
    hass.states.async_set(
        "cover.kitchen",
        STATE_OPEN,
        {
            "current_position": 100,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    entry = await setup_entry(hass)
    runtime = entry.runtime_data
    hass.states.async_set(
        "cover.kitchen",
        STATE_OPEN,
        {
            "current_position": 50,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    await hass.async_block_till_done()
    assert runtime.rule_states["kitchen"].manual_override is True
    assert runtime.decisions["kitchen"].decision is DecisionType.HOLD
    await runtime.async_resume("kitchen")
    assert runtime.rule_states["kitchen"].manual_override is False
    assert runtime.decisions["kitchen"].decision is DecisionType.SHADE


async def test_manual_pause_persists_across_reload(
    hass, enable_custom_integrations
) -> None:
    hass.states.async_set(
        "cover.kitchen",
        STATE_OPEN,
        {
            "current_position": 100,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    entry = await setup_entry(hass)
    await entry.runtime_data.async_activate_manual_override(
        "kitchen", force_manual=True
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.rule_states["kitchen"].manual_override is True


async def test_global_dry_run_absolutely_prevents_calls(
    hass, enable_custom_integrations
) -> None:
    calls = []

    async def service(call):
        calls.append(call)

    hass.services.async_register("cover", "set_cover_position", service)
    hass.states.async_set(
        "cover.kitchen",
        STATE_OPEN,
        {
            "current_position": 100,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    entry = await setup_entry(hass, dry_run=True)
    await entry.runtime_data.async_evaluate_all()
    await hass.async_block_till_done()
    assert calls == []


async def test_runtime_control_switches_persist_options(
    hass, enable_custom_integrations
) -> None:
    hass.states.async_set(
        "cover.kitchen",
        STATE_OPEN,
        {
            "current_position": 100,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    entry = await setup_entry(hass)
    runtime = entry.runtime_data
    await runtime.async_set_global_enabled(False)
    assert runtime.config.globally_enabled is False
    assert entry.options["global_enabled"] is False
    await runtime.async_set_dry_run(False)
    assert runtime.config.dry_run is False
    await runtime.async_set_cover_enabled("kitchen", False)
    assert runtime.cover("kitchen").enabled is False
    assert entry.options["covers"][0]["enabled"] is False


async def test_manual_override_modes_and_input_parsing(
    hass, enable_custom_integrations
) -> None:
    cover = make_cover(
        minimum_elevation=-90,
        manual_override_mode=ManualOverrideMode.MINUTES_15,
        environment=EnvironmentConfig(
            outdoor_temperature=ThresholdConfig("sensor.outdoor", 23, 22)
        ),
        wind=WindConfig("sensor.wind", 40, 30),
        safety_entities=("binary_sensor.door",),
    )
    hass.states.async_set(
        "cover.kitchen",
        STATE_OPEN,
        {
            "current_position": 100,
            "current_tilt_position": 50,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    hass.states.async_set("sensor.outdoor", "24.5")
    hass.states.async_set("sensor.wind", "unknown")
    hass.states.async_set("binary_sensor.door", "off")
    entry = await setup_entry(hass, cover)
    runtime = entry.runtime_data
    readings = runtime._readings(cover)
    assert readings.values["sensor.outdoor"] == 24.5
    assert readings.values["sensor.wind"] is None
    assert runtime._safety_state(hass.states.get("binary_sensor.door")) is False
    await runtime.async_activate_manual_override("kitchen")
    state = runtime.rule_states["kitchen"]
    assert state.manual_override is True
    assert state.manual_override_expires is not None


async def test_disabled_manual_override_does_not_pause(
    hass, enable_custom_integrations
) -> None:
    cover = make_cover(
        minimum_elevation=-90,
        manual_override_mode=ManualOverrideMode.DISABLED,
    )
    hass.states.async_set("cover.kitchen", STATE_OPEN, {"current_position": 100})
    entry = await setup_entry(hass, cover)
    await entry.runtime_data.async_activate_manual_override("kitchen")
    assert entry.runtime_data.rule_states["kitchen"].manual_override is False


async def test_invalid_stored_configuration_creates_repair(
    hass, enable_custom_integrations
) -> None:
    invalid_data = {**ENTRY_DATA, "time_zone": "Not/A_Real_Zone"}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Broken Home",
        data=invalid_data,
        options=options_for(make_cover()),
        version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is False
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"invalid_configuration_{entry.entry_id}"
    )
    assert issue is not None
