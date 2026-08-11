"""CoverCompass manual override buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Set up pause and resume buttons per configured cover."""
    runtime = entry.runtime_data
    async_add_entities(
        entity
        for cover in runtime.config.covers
        for entity in (
            PauseAutomationButton(runtime, cover),
            ResumeAutomationButton(runtime, cover),
        )
    )


class PauseAutomationButton(CoverCompassEntity, ButtonEntity):
    """Pause a cover until explicitly resumed."""

    _attr_translation_key = "pause_automation"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime: CoverCompassRuntime, cover: CoverConfig) -> None:
        super().__init__(runtime, f"{cover.id}_pause", cover=cover)

    async def async_press(self) -> None:
        assert self.cover_id is not None
        await self.runtime.async_activate_manual_override(
            self.cover_id, force_manual=True
        )
        await self.runtime.async_evaluate_all()


class ResumeAutomationButton(CoverCompassEntity, ButtonEntity):
    """Clear the manual override and re-evaluate rules."""

    _attr_translation_key = "resume_automation"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime: CoverCompassRuntime, cover: CoverConfig) -> None:
        super().__init__(runtime, f"{cover.id}_resume", cover=cover)

    async def async_press(self) -> None:
        assert self.cover_id is not None
        await self.runtime.async_resume(self.cover_id)
