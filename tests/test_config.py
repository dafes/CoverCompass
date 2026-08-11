"""Tests for stored configuration validation and migration."""

from datetime import time

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cover_compass import async_migrate_entry
from custom_components.cover_compass.config import (
    cover_from_dict,
    cover_to_dict,
    parse_integration_config,
    validate_cover,
)
from custom_components.cover_compass.const import (
    CONF_COVERS,
    CONF_HOUSE_ROTATION,
    CONFIG_VERSION,
    DOMAIN,
)
from custom_components.cover_compass.model import (
    AutomationMode,
    ConditionKey,
    EnvironmentConfig,
    ManualOverrideMode,
    MaximumThresholdConfig,
    ThresholdConfig,
    WindConfig,
)

from .helpers import ENTRY_DATA, make_cover, options_for


def test_duplicate_physical_cover_rejected() -> None:
    cover = make_cover()
    options = options_for(cover)
    options[CONF_COVERS].append(dict(options[CONF_COVERS][0], id="other"))
    try:
        parse_integration_config(ENTRY_DATA, options)
    except ValueError as err:
        assert "only be configured once" in str(err)
    else:
        raise AssertionError("duplicate physical cover was accepted")


def test_comprehensive_cover_round_trip() -> None:
    cover = make_cover(
        area_id="kitchen",
        normal_tilt=80,
        shading_tilt=30,
        manual_override_mode=ManualOverrideMode.UNTIL_TIME,
        manual_override_until=time(18),
        advanced_conditions=frozenset({ConditionKey.SUN}),
        environment=EnvironmentConfig(
            outdoor_temperature=ThresholdConfig("sensor.outdoor", 24, 22),
            indoor_temperature=ThresholdConfig("sensor.indoor", 23, 21),
            illuminance=ThresholdConfig("sensor.light", 10000, 8000),
            cloud_cover=MaximumThresholdConfig("sensor.cloud", 40, 60),
            weather_entity_id="weather.home",
            allowed_weather_states=frozenset({"sunny"}),
        ),
        wind=WindConfig("sensor.wind", 40, 30),
    )
    assert cover_from_dict(cover_to_dict(cover)) == cover


@pytest.mark.parametrize(
    "cover",
    [
        make_cover(facade_azimuth=360),
        make_cover(exposure_angle=181),
        make_cover(entity_id="sensor.not_a_cover"),
        make_cover(solar_exit_margin=-1),
        make_cover(minimum_elevation=20, maximum_elevation=10),
        make_cover(normal_position=101),
        make_cover(mode=AutomationMode.ADVANCED),
        make_cover(manual_override_mode=ManualOverrideMode.UNTIL_TIME),
        make_cover(
            environment=EnvironmentConfig(
                outdoor_temperature=ThresholdConfig("sensor.outdoor", 20, 21)
            )
        ),
        make_cover(
            environment=EnvironmentConfig(
                cloud_cover=MaximumThresholdConfig("sensor.cloud", 60, 50)
            )
        ),
        make_cover(wind=WindConfig("sensor.wind", 30, 40)),
        make_cover(activation_delay=-1),
    ],
)
def test_invalid_cover_configuration_rejected(cover) -> None:
    with pytest.raises(ValueError):
        validate_cover(cover)


@pytest.mark.parametrize(
    "data",
    [
        {**ENTRY_DATA, "house_name": ""},
        {**ENTRY_DATA, "latitude": 91},
        {**ENTRY_DATA, "longitude": 181},
        {**ENTRY_DATA, "house_rotation": 360},
    ],
)
def test_invalid_house_configuration_rejected(data) -> None:
    with pytest.raises(ValueError):
        parse_integration_config(data, options_for(make_cover()))


def test_duplicate_cover_ids_rejected() -> None:
    cover = make_cover()
    options = options_for(cover)
    options[CONF_COVERS].append(dict(options[CONF_COVERS][0], entity_id="cover.office"))
    with pytest.raises(ValueError, match="ids must be unique"):
        parse_integration_config(ENTRY_DATA, options)


async def test_migrate_version_one(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            key: value
            for key, value in ENTRY_DATA.items()
            if key != CONF_HOUSE_ROTATION
        },
        options={
            CONF_COVERS: [
                {
                    "name": "Kitchen",
                    "entity_id": "cover.kitchen",
                    "orientation": 135,
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == CONFIG_VERSION
    assert entry.data[CONF_HOUSE_ROTATION] == 0
    assert entry.options[CONF_COVERS][0]["facade_azimuth"] == 135
    assert entry.options[CONF_COVERS][0]["id"]


async def test_future_migration_fails_and_creates_repair(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=CONFIG_VERSION + 1,
        data=ENTRY_DATA,
        options=options_for(make_cover()),
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is False
