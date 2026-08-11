"""Tests for setup, reconfigure and cover-management flows."""

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cover_compass.const import (
    CONF_COVERS,
    CONF_DRY_RUN,
    CONF_GLOBAL_ENABLED,
    CONF_HOUSE_NAME,
    CONF_HOUSE_ROTATION,
    CONF_RECONCILE_INTERVAL,
    DOMAIN,
)

from .helpers import ENTRY_DATA, make_cover, options_for


async def complete_cover(flow, flow_id: str, entity_id: str = "cover.kitchen"):
    """Complete all five cover configuration forms."""
    result = await flow.async_configure(
        flow_id,
        {
            "name": "Kitchen",
            "entity_id": entity_id,
            "orientation_choice": "se",
            "custom_azimuth": 135,
            "enabled": True,
            "dry_run": False,
        },
    )
    assert result["step_id"] == "cover_policy"
    result = await flow.async_configure(
        flow_id,
        {
            "exposure_angle": 55,
            "solar_exit_margin": 3,
            "minimum_elevation": 10,
            "elevation_exit_margin": 1,
            "mode": "sun_and_time",
            "normal_position": 100,
            "shading_position": 25,
            "activation_delay": 300,
            "clear_delay": 600,
            "minimum_movement_interval": 300,
            "manual_override_mode": "minutes_60",
            "advanced_match": "all",
            "advanced_conditions": [],
        },
    )
    assert result["step_id"] == "cover_time"
    result = await flow.async_configure(
        flow_id,
        {
            "use_time_window": True,
            "start_kind": "fixed",
            "start_time": "07:00:00",
            "start_offset": 0,
            "end_kind": "fixed",
            "end_time": "14:00:00",
            "end_offset": 0,
            "weekdays": ["0", "1", "2", "3", "4", "5", "6"],
        },
    )
    assert result["step_id"] == "cover_environment"
    result = await flow.async_configure(
        flow_id,
        {
            "outdoor_activate": 23,
            "outdoor_clear": 22,
            "indoor_activate": 22,
            "indoor_clear": 21,
            "illuminance_activate": 10000,
            "illuminance_clear": 8000,
            "cloud_activate": 40,
            "cloud_clear": 55,
            "allowed_weather_states": ["sunny", "partlycloudy"],
        },
    )
    assert result["step_id"] == "cover_safety"
    return await flow.async_configure(
        flow_id,
        {
            "safety_entities": [],
            "safety_policy": "block_lowering",
            "wind_unsafe": 40,
            "wind_safe": 30,
            "wind_retract": True,
        },
    )


async def test_full_config_flow(hass, enable_custom_integrations) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["step_id"] == "cover"
    result = await complete_cover(hass.config_entries.flow, result["flow_id"])
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "setup_complete"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Home"
    assert result["options"][CONF_DRY_RUN] is True
    assert result["options"][CONF_COVERS][0]["facade_azimuth"] == 135


async def test_duplicate_house_aborts(hass, enable_custom_integrations) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="test home:52.520000:13.405000")
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=ENTRY_DATA
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_flow(hass, enable_custom_integrations) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Home",
        data=ENTRY_DATA,
        options=options_for(make_cover()),
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["step_id"] == "reconfigure"
    changed = {**ENTRY_DATA, CONF_HOUSE_NAME: "Other", CONF_HOUSE_ROTATION: 10}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], changed)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "Other"
    assert entry.data[CONF_HOUSE_ROTATION] == 10


async def test_options_global_and_remove(hass, enable_custom_integrations) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options=options_for(make_cover()),
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "global"}
    )
    assert result["step_id"] == "global"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_GLOBAL_ENABLED: False,
            CONF_DRY_RUN: True,
            CONF_RECONCILE_INTERVAL: 600,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_GLOBAL_ENABLED] is False

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_cover"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"cover_id": "kitchen"}
    )
    assert result["step_id"] == "confirm_remove"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"confirm": "confirmation_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_COVERS] == []


async def test_options_add_edit_and_duplicate(hass, enable_custom_integrations) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options=options_for(make_cover()),
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_cover"}
    )
    result = await complete_cover(
        hass.config_entries.options, result["flow_id"], "cover.office"
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(entry.options[CONF_COVERS]) == 2

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "edit_cover"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"cover_id": "kitchen"}
    )
    result = await complete_cover(
        hass.config_entries.options, result["flow_id"], "cover.kitchen"
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(entry.options[CONF_COVERS]) == 2

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "duplicate_cover"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"cover_id": "kitchen"}
    )
    result = await complete_cover(
        hass.config_entries.options, result["flow_id"], "cover.bedroom"
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(entry.options[CONF_COVERS]) == 3
    assert len({item["id"] for item in entry.options[CONF_COVERS]}) == 3
