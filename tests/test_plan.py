"""Tests for visual planner export validation."""

import json

import pytest

from custom_components.cover_compass.plan import PlanValidationError, parse_cover_plan


def valid_plan() -> dict:
    return {
        "format": "cover-compass-plan",
        "version": 1,
        "house": {
            "name": "Map House",
            "latitude": 52.52,
            "longitude": 13.405,
            "time_zone": "Europe/Berlin",
            "rotation": 0,
        },
        "outline": [
            {"latitude": 52.5201, "longitude": 13.4049},
            {"latitude": 52.5201, "longitude": 13.4051},
            {"latitude": 52.5199, "longitude": 13.4051},
        ],
        "shutters": [
            {
                "id": "kitchen-plan",
                "name": "Kitchen east",
                "facade_azimuth": 90,
                "segment_index": 1,
                "segment_position": 0.5,
            }
        ],
    }


def test_parse_cover_plan() -> None:
    plan = parse_cover_plan(json.dumps(valid_plan()))

    assert plan.house.name == "Map House"
    assert plan.house.time_zone == "Europe/Berlin"
    assert len(plan.outline) == 3
    assert plan.shutters[0].facade_azimuth == 90


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda plan: plan.update(version=2), "unsupported_plan_version"),
        (lambda plan: plan.update(outline=[]), "invalid_plan"),
        (
            lambda plan: plan["house"].update(time_zone="Not/AZone"),
            "invalid_plan",
        ),
        (
            lambda plan: plan["shutters"][0].update(facade_azimuth=360),
            "invalid_plan",
        ),
        (
            lambda plan: plan["shutters"][0].update(segment_index=3),
            "invalid_plan",
        ),
    ],
)
def test_invalid_cover_plan(change, code: str) -> None:
    value = valid_plan()
    change(value)

    with pytest.raises(PlanValidationError) as err:
        parse_cover_plan(json.dumps(value))

    assert err.value.code == code


def test_invalid_json() -> None:
    with pytest.raises(PlanValidationError) as err:
        parse_cover_plan("not json")

    assert err.value.code == "invalid_plan_json"
