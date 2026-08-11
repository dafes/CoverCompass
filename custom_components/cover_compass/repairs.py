"""Actionable missing-entity repairs for CoverCompass."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .model import CoverConfig, IntegrationConfig


def referenced_entities(cover: CoverConfig) -> set[str]:
    """Return all Home Assistant entities referenced by a cover definition."""
    entities = {cover.entity_id, *cover.safety_entities}
    environment = cover.environment
    for condition in (
        environment.outdoor_temperature,
        environment.indoor_temperature,
        environment.illuminance,
        environment.cloud_cover,
    ):
        if condition is not None:
            entities.add(condition.entity_id)
    if environment.weather_entity_id:
        entities.add(environment.weather_entity_id)
    if cover.wind is not None:
        entities.add(cover.wind.entity_id)
    return entities


def async_update_entity_issues(
    hass: HomeAssistant, entry_id: str, config: IntegrationConfig
) -> None:
    """Create repairs only for references absent from both state and registry."""
    registry = er.async_get(hass)
    issue_registry = ir.async_get(hass)
    expected: set[str] = set()
    for cover in config.covers:
        for entity_id in referenced_entities(cover):
            issue_id = f"missing_entity_{entry_id}_{cover.id}_{entity_id}"
            expected.add(issue_id)
            missing = (
                hass.states.get(entity_id) is None
                and registry.async_get(entity_id) is None
            )
            if missing:
                ir.async_create_issue(
                    hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key="missing_entity",
                    translation_placeholders={
                        "cover": cover.name,
                        "entity_id": entity_id,
                    },
                )
            else:
                ir.async_delete_issue(hass, DOMAIN, issue_id)
    prefix = f"missing_entity_{entry_id}_"
    for domain, issue_id in tuple(issue_registry.issues):
        if (
            domain == DOMAIN
            and issue_id.startswith(prefix)
            and issue_id not in expected
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
