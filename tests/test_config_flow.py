"""Tests for setup, reconfigure and cover-management flows."""

import json

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


def planner_json(*, two_shutters: bool = False) -> str:
    shutters = [
        {
            "id": "kitchen-plan",
            "name": "Kitchen east",
            "facade_azimuth": 90,
            "segment_index": 0,
            "segment_position": 0.5,
        }
    ]
    if two_shutters:
        shutters.append({
            "id": "office-plan",
            "name": "Office south",
            "facade_azimuth": 180,
            "segment_index": 1,
            "segment_position": 0.5,
        })
    return json.dumps({
        "format": "cover-compass-plan",
        "version": 1,
        "house": {
            "name": "Planned Home",
            "latitude": 48.137,
            "longitude": 11.575,
            "time_zone": "Europe/Berlin",
            "rotation": 0,
        },
        "outline": [
            {"latitude": 48.1371, "longitude": 11.5749},
            {"latitude": 48.1371, "longitude": 11.5751},
            {"latitude": 48.1369, "longitude": 11.5751},
        ],
        "shutters": shutters,
    })


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
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual_setup"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual_setup"
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


async def test_plan_import_config_flow(hass, enable_custom_integrations) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "import_plan"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"plan_json": planner_json(two_shutters=True)}
    )
    assert result["step_id"] == "assign_plan_cover"
    assert result["description_placeholders"]["name"] == "Kitchen east"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entity_id": "cover.kitchen"}
    )
    assert result["description_placeholders"]["name"] == "Office south"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entity_id": "cover.kitchen"}
    )
    assert result["errors"] == {"entity_id": "plan_entity_already_assigned"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entity_id": "cover.office"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Planned Home"
    assert result["data"]["latitude"] == 48.137
    assert [cover["entity_id"] for cover in result["options"][CONF_COVERS]] == [
        "cover.kitchen",
        "cover.office",
    ]
    assert [cover["facade_azimuth"] for cover in result["options"][CONF_COVERS]] == [
        90,
        180,
    ]


async def test_invalid_plan_import(hass, enable_custom_integrations) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "import_plan"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"plan_json": "not json"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"plan_json": "invalid_plan_json"}


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


async def test_options_plan_import_preserves_existing_policy(
    hass, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Home",
        data=ENTRY_DATA,
        options=options_for(make_cover()),
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "import_plan"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "plan_json": planner_json(two_shutters=True),
            "update_house": True,
            "remove_unmapped": False,
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entity_id": "cover.kitchen"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entity_id": "cover.office"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.title == "Planned Home"
    assert entry.data["latitude"] == 48.137
    assert len(entry.options[CONF_COVERS]) == 2
    kitchen = next(
        cover
        for cover in entry.options[CONF_COVERS]
        if cover["entity_id"] == "cover.kitchen"
    )
    assert kitchen["id"] == "kitchen"
    assert kitchen["name"] == "Kitchen east"
    assert kitchen["facade_azimuth"] == 90
    assert kitchen["exposure_angle"] == 180
