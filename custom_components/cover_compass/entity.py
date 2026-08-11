"""Shared CoverCompass entity base classes."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER
from .model import CoverConfig
from .runtime import CoverCompassRuntime


class CoverCompassEntity(Entity):
    """Base for dispatcher-updated CoverCompass entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        runtime: CoverCompassRuntime,
        unique_key: str,
        *,
        cover: CoverConfig | None = None,
    ) -> None:
        self.runtime = runtime
        self.cover_id = cover.id if cover is not None else None
        self._attr_unique_id = f"{runtime.entry_id}_{unique_key}"
        if cover is None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{runtime.entry_id}:house")},
                name=runtime.config.house.name,
                manufacturer=MANUFACTURER,
                model="CoverCompass House",
            )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{runtime.entry_id}:cover:{cover.id}")},
                name=cover.name,
                manufacturer=MANUFACTURER,
                model="CoverCompass Cover",
            )

    @property
    def cover(self) -> CoverConfig | None:
        """Return the latest version of this entity's cover config."""
        if self.cover_id is None:
            return None
        return self.runtime.cover(self.cover_id)

    async def async_added_to_hass(self) -> None:
        """Subscribe while this entity is registered."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.runtime.signal_update, self._async_runtime_updated
            )
        )

    @callback
    def _async_runtime_updated(self) -> None:
        self.async_write_ha_state()
