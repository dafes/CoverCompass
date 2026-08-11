"""Test data builders."""

from __future__ import annotations

from custom_components.cover_compass.config import cover_to_dict
from custom_components.cover_compass.const import (
    CONF_COVERS,
    CONF_DRY_RUN,
    CONF_GLOBAL_ENABLED,
    CONF_HOUSE_NAME,
    CONF_HOUSE_ROTATION,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RECONCILE_INTERVAL,
    CONF_TIME_ZONE,
)
from custom_components.cover_compass.model import CoverConfig

ENTRY_DATA = {
    CONF_HOUSE_NAME: "Test Home",
    CONF_LATITUDE: 52.52,
    CONF_LONGITUDE: 13.405,
    CONF_TIME_ZONE: "Europe/Berlin",
    CONF_HOUSE_ROTATION: 0.0,
}


def make_cover(**changes) -> CoverConfig:
    """Return a complete zero-delay cover policy for HA-facing tests."""
    values = {
        "id": "kitchen",
        "name": "Kitchen",
        "entity_id": "cover.kitchen",
        "facade_azimuth": 135.0,
        "exposure_angle": 180.0,
        "minimum_elevation": -10.0,
        "activation_delay": 0,
        "clear_delay": 0,
        "minimum_movement_interval": 0,
    }
    values.update(changes)
    return CoverConfig(**values)


def options_for(cover: CoverConfig, *, dry_run: bool = True) -> dict:
    """Return ConfigEntry options for one cover."""
    return {
        CONF_COVERS: [cover_to_dict(cover)],
        CONF_GLOBAL_ENABLED: True,
        CONF_DRY_RUN: dry_run,
        CONF_RECONCILE_INTERVAL: 300,
    }
