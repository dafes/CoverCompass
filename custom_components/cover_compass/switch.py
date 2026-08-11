"""CoverCompass global and per-cover controls."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    """Set up master, dry-run and per-cover switches."""
    runtime = entry.runtime_data
    async_add_entities(
        [AutomationEnabledSwitch(runtime), DryRunSwitch(runtime)]
        + [CoverEnabledSwitch(runtime, cover) for cover in runtime.config.covers]
    )


class AutomationEnabledSwitch(CoverCompassEntity, SwitchEntity):
    """Absolute master automation control."""

    _attr_translation_key = "automation_enabled"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime: CoverCompassRuntime) -> None:
        super().__init__(runtime, "automation_enabled")

    @property
    def is_on(self) -> bool:
        return self.runtime.config.globally_enabled

    async def async_turn_on(self, **_kwargs: object) -> None:
        await self.runtime.async_set_global_enabled(True)

    async def async_turn_off(self, **_kwargs: object) -> None:
        await self.runtime.async_set_global_enabled(False)


class DryRunSwitch(CoverCompassEntity, SwitchEntity):
    """Master dry-run control."""

    _attr_translation_key = "dry_run"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime: CoverCompassRuntime) -> None:
        super().__init__(runtime, "dry_run")

    @property
    def is_on(self) -> bool:
        return self.runtime.config.dry_run

    async def async_turn_on(self, **_kwargs: object) -> None:
        await self.runtime.async_set_dry_run(True)

    async def async_turn_off(self, **_kwargs: object) -> None:
        await self.runtime.async_set_dry_run(False)


class CoverEnabledSwitch(CoverCompassEntity, SwitchEntity):
    """Enable/disable automation for one configured cover."""

    _attr_translation_key = "cover_automation_enabled"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime: CoverCompassRuntime, cover: CoverConfig) -> None:
        super().__init__(runtime, f"{cover.id}_enabled", cover=cover)

    @property
    def is_on(self) -> bool:
        cover = self.cover
        return bool(cover and cover.enabled)

    async def async_turn_on(self, **_kwargs: object) -> None:
        assert self.cover_id is not None
        await self.runtime.async_set_cover_enabled(self.cover_id, True)

    async def async_turn_off(self, **_kwargs: object) -> None:
        assert self.cover_id is not None
        await self.runtime.async_set_cover_enabled(self.cover_id, False)
