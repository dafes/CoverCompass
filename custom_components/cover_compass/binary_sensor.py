"""CoverCompass sun-exposure binary sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import CoverCompassEntity
from .model import CoverConfig
from .runtime import CoverCompassRuntime


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry[CoverCompassRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up per-cover sun-exposure sensors."""
    async_add_entities(
        CoverSunExposureBinarySensor(entry.runtime_data, cover)
        for cover in entry.runtime_data.config.covers
    )


class CoverSunExposureBinarySensor(CoverCompassEntity, BinarySensorEntity):
    """Whether solar geometry currently exposes a configured facade."""

    _attr_translation_key = "sun_exposure"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: CoverCompassRuntime, cover: CoverConfig) -> None:
        super().__init__(runtime, f"{cover.id}_sun_exposure", cover=cover)

    @property
    def is_on(self) -> bool | None:
        """Return current exposure."""
        decision = self.runtime.decisions.get(self.cover_id or "")
        return decision.solar.sun_exposed if decision is not None else None
