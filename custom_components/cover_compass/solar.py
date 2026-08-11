"""Pure circular-angle and solar-exposure calculations."""

from __future__ import annotations

from .model import SolarExposure


def normalize_angle(angle: float) -> float:
    """Normalize an angle to the half-open interval [0, 360)."""
    return angle % 360.0


def signed_angular_difference(reference: float, angle: float) -> float:
    """Return the smallest signed turn from reference to angle."""
    difference = (normalize_angle(angle) - normalize_angle(reference) + 180) % 360 - 180
    return 180.0 if difference == -180.0 else difference


def angular_distance(first: float, second: float) -> float:
    """Return the smallest unsigned circular distance between two angles."""
    return abs(signed_angular_difference(first, second))


def calculate_solar_exposure(
    *,
    facade_azimuth: float,
    solar_azimuth: float,
    solar_elevation: float,
    exposure_angle: float,
    minimum_elevation: float,
    maximum_elevation: float | None = None,
    previously_exposed: bool = False,
    horizontal_exit_margin: float = 0.0,
    elevation_exit_margin: float = 0.0,
) -> SolarExposure:
    """Calculate deterministic facade exposure with optional exit hysteresis."""
    facade = normalize_angle(facade_azimuth)
    sun = normalize_angle(solar_azimuth)
    difference = signed_angular_difference(facade, sun)
    effective_angle = exposure_angle + (
        horizontal_exit_margin if previously_exposed else 0.0
    )
    minimum = minimum_elevation - (elevation_exit_margin if previously_exposed else 0.0)
    maximum = maximum_elevation
    if maximum is not None and previously_exposed:
        maximum += elevation_exit_margin
    horizontal = abs(difference) <= effective_angle
    elevation = solar_elevation >= minimum and (
        maximum is None or solar_elevation <= maximum
    )
    return SolarExposure(
        solar_azimuth=sun,
        solar_elevation=solar_elevation,
        facade_azimuth=facade,
        angular_difference=difference,
        absolute_difference=abs(difference),
        effective_exposure_angle=effective_angle,
        within_horizontal_exposure=horizontal,
        within_elevation_range=elevation,
        sun_exposed=horizontal and elevation,
    )
