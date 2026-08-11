"""Tests for diagnostics redaction and actionable repairs."""

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.const import STATE_OPEN
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cover_compass.const import DOMAIN
from custom_components.cover_compass.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.cover_compass.repairs import async_update_entity_issues

from .helpers import ENTRY_DATA, make_cover, options_for


async def test_diagnostics_redact_location(hass, enable_custom_integrations) -> None:
    hass.states.async_set(
        "cover.kitchen",
        STATE_OPEN,
        {
            "current_position": 100,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options=options_for(make_cover(minimum_elevation=-90)),
        version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry_data"]["latitude"] == "**REDACTED**"
    assert diagnostics["entry_data"]["longitude"] == "**REDACTED**"
    assert diagnostics["decisions"]["kitchen"]["reason"]
    assert diagnostics["decisions"]["kitchen"]["environment_readings"] == {}


async def test_missing_entity_repair_lifecycle(hass) -> None:
    cover = make_cover(
        entity_id="cover.removed", safety_entities=("binary_sensor.removed",)
    )
    from custom_components.cover_compass.config import parse_integration_config

    config = parse_integration_config(ENTRY_DATA, options_for(cover))
    async_update_entity_issues(hass, "entry", config)
    registry = ir.async_get(hass)
    cover_issue = f"missing_entity_entry_{cover.id}_{cover.entity_id}"
    assert registry.async_get_issue(DOMAIN, cover_issue) is not None
    hass.states.async_set(cover.entity_id, STATE_OPEN)
    async_update_entity_issues(hass, "entry", config)
    assert registry.async_get_issue(DOMAIN, cover_issue) is None

    missing_config = parse_integration_config(
        ENTRY_DATA,
        {
            **options_for(cover),
            "covers": [],
        },
    )
    hass.states.async_remove(cover.entity_id)
    async_update_entity_issues(hass, "entry", config)
    assert registry.async_get_issue(DOMAIN, cover_issue) is not None
    async_update_entity_issues(hass, "entry", missing_config)
    assert registry.async_get_issue(DOMAIN, cover_issue) is None
