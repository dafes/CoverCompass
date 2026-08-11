"""CoverCompass Home Assistant integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from .config import parse_integration_config
from .const import (
    CONF_COVERS,
    CONF_HOUSE_ROTATION,
    CONFIG_VERSION,
    DOMAIN,
    MANUFACTURER,
    PLATFORMS,
)
from .model import IntegrationConfig, new_cover_id
from .runtime import CoverCompassRuntime

type CoverCompassConfigEntry = ConfigEntry[CoverCompassRuntime]

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(_hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Set up the CoverCompass integration."""
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: CoverCompassConfigEntry
) -> bool:
    """Set up a CoverCompass house from a config entry."""
    issue_id = f"invalid_configuration_{entry.entry_id}"
    try:
        config = parse_integration_config(dict(entry.data), dict(entry.options))
    except (KeyError, TypeError, ValueError) as err:
        _LOGGER.error("CoverCompass configuration is invalid: %s", err)
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="invalid_configuration",
        )
        return False
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    _async_setup_devices(hass, entry, config)
    runtime = CoverCompassRuntime(hass, entry.entry_id, config, entry)
    entry.runtime_data = runtime
    await runtime.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_setup_devices(
    hass: HomeAssistant, entry: CoverCompassConfigEntry, config: IntegrationConfig
) -> None:
    """Register the virtual house and managed covers with current registry APIs."""
    registry = dr.async_get(hass)
    areas = ar.async_get(hass)
    expected_identifiers = {(DOMAIN, f"{entry.entry_id}:house")}
    house = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=expected_identifiers,
        name=config.house.name,
        manufacturer=MANUFACTURER,
        model="CoverCompass House",
    )
    for cover in config.covers:
        identifier = (DOMAIN, f"{entry.entry_id}:cover:{cover.id}")
        expected_identifiers.add(identifier)
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={identifier},
            name=cover.name,
            manufacturer=MANUFACTURER,
            model="CoverCompass Cover",
            via_device_id=house.id,
        )
        area_id = (
            cover.area_id
            if cover.area_id is not None
            and areas.async_get_area(cover.area_id) is not None
            else None
        )
        if device.area_id != area_id:
            registry.async_update_device(device.id, area_id=area_id)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        cover_compass_identifiers = {
            identifier for identifier in device.identifiers if identifier[0] == DOMAIN
        }
        if cover_compass_identifiers and cover_compass_identifiers.isdisjoint(
            expected_identifiers
        ):
            registry.async_remove_device(device.id)


async def async_unload_entry(
    hass: HomeAssistant, entry: CoverCompassConfigEntry
) -> bool:
    """Unload a CoverCompass house."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def async_migrate_entry(
    hass: HomeAssistant, entry: CoverCompassConfigEntry
) -> bool:
    """Migrate persisted configuration to the current schema."""
    if entry.version > CONFIG_VERSION or entry.version < 1:
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"unsupported_config_version_{entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="unsupported_config_version",
            translation_placeholders={
                "version": str(entry.version),
                "supported": str(CONFIG_VERSION),
            },
        )
        return False
    if entry.version == 1:
        data = {**entry.data}
        data.setdefault(CONF_HOUSE_ROTATION, 0.0)
        options = {**entry.options}
        covers: list[dict[str, Any]] = []
        for old_cover in options.get(CONF_COVERS, []):
            cover = {**old_cover}
            cover.setdefault("id", new_cover_id())
            if "orientation" in cover and "facade_azimuth" not in cover:
                cover["facade_azimuth"] = float(cover.pop("orientation"))
            cover.setdefault("solar_exit_margin", 3.0)
            cover.setdefault("elevation_exit_margin", 1.0)
            cover.setdefault("time_windows", [])
            cover.setdefault("environment", {})
            cover.setdefault("safety_entities", [])
            cover.setdefault("advanced_conditions", [])
            covers.append(cover)
        options[CONF_COVERS] = covers
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, version=CONFIG_VERSION
        )
    ir.async_delete_issue(hass, DOMAIN, f"unsupported_config_version_{entry.entry_id}")
    return True
