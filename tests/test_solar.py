"""Tests for circular geometry and exposure."""

from custom_components.cover_compass.solar import (
    angular_distance,
    calculate_solar_exposure,
    normalize_angle,
    signed_angular_difference,
)


def test_angle_normalization_and_north_boundary() -> None:
    assert normalize_angle(360) == 0
    assert normalize_angle(-1) == 359
    assert signed_angular_difference(350, 10) == 20
    assert signed_angular_difference(10, 350) == -20
    assert angular_distance(0, 359) == 1


def test_exact_boundary_and_opposite_facade() -> None:
    boundary = calculate_solar_exposure(
        facade_azimuth=0,
        solar_azimuth=60,
        solar_elevation=20,
        exposure_angle=60,
        minimum_elevation=10,
    )
    assert boundary.sun_exposed is True
    opposite = calculate_solar_exposure(
        facade_azimuth=0,
        solar_azimuth=180,
        solar_elevation=20,
        exposure_angle=60,
        minimum_elevation=10,
    )
    assert opposite.within_horizontal_exposure is False
    assert opposite.sun_exposed is False


def test_elevation_boundaries() -> None:
    below = calculate_solar_exposure(
        facade_azimuth=180,
        solar_azimuth=180,
        solar_elevation=-1,
        exposure_angle=55,
        minimum_elevation=0,
    )
    assert below.sun_exposed is False
    minimum = calculate_solar_exposure(
        facade_azimuth=180,
        solar_azimuth=180,
        solar_elevation=10,
        exposure_angle=55,
        minimum_elevation=10,
        maximum_elevation=40,
    )
    maximum = calculate_solar_exposure(
        facade_azimuth=180,
        solar_azimuth=180,
        solar_elevation=40,
        exposure_angle=55,
        minimum_elevation=10,
        maximum_elevation=40,
    )
    above = calculate_solar_exposure(
        facade_azimuth=180,
        solar_azimuth=180,
        solar_elevation=40.1,
        exposure_angle=55,
        minimum_elevation=10,
        maximum_elevation=40,
    )
    assert minimum.sun_exposed is True
    assert maximum.sun_exposed is True
    assert above.sun_exposed is False


def test_exposure_exit_hysteresis() -> None:
    entering = calculate_solar_exposure(
        facade_azimuth=180,
        solar_azimuth=236,
        solar_elevation=20,
        exposure_angle=55,
        minimum_elevation=10,
    )
    exiting = calculate_solar_exposure(
        facade_azimuth=180,
        solar_azimuth=236,
        solar_elevation=20,
        exposure_angle=55,
        minimum_elevation=10,
        previously_exposed=True,
        horizontal_exit_margin=3,
    )
    assert entering.sun_exposed is False
    assert exiting.sun_exposed is True
