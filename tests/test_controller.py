"""Tests for conservative Home Assistant cover execution."""

from datetime import datetime
from zoneinfo import ZoneInfo

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.const import STATE_CLOSED, STATE_CLOSING, STATE_OPEN, STATE_OPENING
from homeassistant.core import Context, State

from custom_components.cover_compass.controller import (
    CoverController,
    state_change_is_manual,
)
from custom_components.cover_compass.model import (
    CommandRecord,
    Decision,
    DecisionType,
    SolarExposure,
)

from .helpers import make_cover

NOW = datetime(2026, 8, 10, 10, tzinfo=ZoneInfo("Europe/Berlin"))
SOLAR = SolarExposure(135, 30, 135, 0, 0, 55, True, True, True)


def decision(
    target: int,
    kind: DecisionType = DecisionType.SHADE,
    *,
    safety_active: bool = False,
) -> Decision:
    return Decision(
        kind,
        "test",
        target,
        None,
        {},
        SOLAR,
        "test",
        safety_active=safety_active,
    )


async def test_already_at_target_sends_no_duplicate(hass) -> None:
    cover = make_cover(shading_position=20)
    hass.states.async_set(
        cover.entity_id,
        STATE_OPEN,
        {"current_position": 20, "supported_features": CoverEntityFeature.SET_POSITION},
    )
    controller = CoverController(hass, cover)
    result = await controller.async_reconcile(decision(20), now=NOW, dry_run=False)
    assert result.command_sent is False
    assert "already" in result.reason.lower()


async def test_position_cover_command_and_own_change_detection(hass) -> None:
    calls = []

    async def service(call):
        calls.append(call)

    hass.services.async_register("cover", "set_cover_position", service)
    cover = make_cover(shading_position=20)
    hass.states.async_set(
        cover.entity_id,
        STATE_OPEN,
        {
            "current_position": 100,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    controller = CoverController(hass, cover)
    result = await controller.async_reconcile(decision(20), now=NOW, dry_run=False)
    await hass.async_block_till_done()
    assert result.command_sent is True
    assert calls[0].data["position"] == 20
    assert controller.last_command is not None
    context = Context(id=controller.last_command.context_id)
    old = State(cover.entity_id, STATE_OPEN, {"current_position": 100})
    new = State(
        cover.entity_id,
        STATE_CLOSING,
        {"current_position": 80},
        context=context,
    )
    assert controller.state_change_is_expected(old, new, NOW) is True
    assert state_change_is_manual(old, new) is True


async def test_open_close_only_fallback(hass) -> None:
    calls = []

    async def service(call):
        calls.append(call)

    hass.services.async_register("cover", "close_cover", service)
    cover = make_cover(shading_position=20)
    hass.states.async_set(
        cover.entity_id,
        STATE_OPEN,
        {
            "current_position": 100,
            "supported_features": CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE,
        },
    )
    controller = CoverController(hass, cover)
    result = await controller.async_reconcile(decision(20), now=NOW, dry_run=False)
    await hass.async_block_till_done()
    assert result.command_sent is True
    assert result.service_calls == ("close_cover",)
    assert len(calls) == 1


async def test_dry_run_never_calls_cover_service(hass) -> None:
    calls = []

    async def service(call):
        calls.append(call)

    hass.services.async_register("cover", "set_cover_position", service)
    cover = make_cover(shading_position=20)
    hass.states.async_set(
        cover.entity_id,
        STATE_OPEN,
        {
            "current_position": 100,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    result = await CoverController(hass, cover).async_reconcile(
        decision(20), now=NOW, dry_run=True
    )
    await hass.async_block_till_done()
    assert result.command_sent is False
    assert calls == []


async def test_wind_safety_retraction_bypasses_comfort_rate_limit(hass) -> None:
    calls = []

    async def service(call):
        calls.append(call)

    hass.services.async_register("cover", "set_cover_position", service)
    cover = make_cover(minimum_movement_interval=600)
    hass.states.async_set(
        cover.entity_id,
        STATE_OPEN,
        {
            "current_position": 20,
            "supported_features": CoverEntityFeature.SET_POSITION,
        },
    )
    controller = CoverController(hass, cover)
    controller.last_automatic_command = NOW
    result = await controller.async_reconcile(
        decision(100, DecisionType.OPEN, safety_active=True),
        now=NOW,
        dry_run=False,
    )
    await hass.async_block_till_done()
    assert result.command_sent is True
    assert calls[0].data["position"] == 100


async def test_tilt_only_command(hass) -> None:
    calls = []

    async def service(call):
        calls.append(call)

    hass.services.async_register("cover", "set_cover_tilt_position", service)
    cover = make_cover(shading_position=20, shading_tilt=30)
    hass.states.async_set(
        cover.entity_id,
        STATE_OPEN,
        {
            "current_position": 20,
            "current_tilt_position": 100,
            "supported_features": CoverEntityFeature.SET_TILT_POSITION,
        },
    )
    result = await CoverController(hass, cover).async_reconcile(
        Decision(DecisionType.SHADE, "test", 20, 30, {}, SOLAR, "test"),
        now=NOW,
        dry_run=False,
    )
    await hass.async_block_till_done()
    assert result.command_sent is True
    assert calls[0].data["tilt_position"] == 30


async def test_open_close_fallback_opens_and_unsupported_holds(hass) -> None:
    calls = []

    async def service(call):
        calls.append(call)

    hass.services.async_register("cover", "open_cover", service)
    cover = make_cover(normal_position=100)
    hass.states.async_set(
        cover.entity_id,
        STATE_CLOSED,
        {
            "current_position": 0,
            "supported_features": CoverEntityFeature.OPEN,
        },
    )
    controller = CoverController(hass, cover)
    result = await controller.async_reconcile(
        decision(100, DecisionType.OPEN), now=NOW, dry_run=False
    )
    await hass.async_block_till_done()
    assert result.service_calls == ("open_cover",)
    hass.states.async_set(
        cover.entity_id,
        STATE_CLOSED,
        {"current_position": 0, "supported_features": 0},
    )
    controller.last_command = None
    unsupported = await controller.async_reconcile(
        decision(100, DecisionType.OPEN), now=NOW, dry_run=False
    )
    assert unsupported.command_sent is False
    assert "does not support" in unsupported.reason


def test_expected_change_fallback_and_timeout(hass) -> None:
    cover = make_cover()
    controller = CoverController(hass, cover)
    controller.last_command = CommandRecord(NOW, 100, 20, None, "different")
    old = State(cover.entity_id, STATE_OPEN, {"current_position": 100})
    progress = State(cover.entity_id, STATE_CLOSING, {"current_position": 60})
    assert controller.state_change_is_expected(old, progress, NOW) is True
    assert (
        controller.state_change_is_expected(old, progress, NOW.replace(hour=11))
        is False
    )


def test_reverse_movement_during_command_is_manual(hass) -> None:
    cover = make_cover()
    controller = CoverController(hass, cover)
    controller.last_command = CommandRecord(NOW, 100, 20, None, "different")
    old = State(cover.entity_id, STATE_CLOSING, {"current_position": 60})
    reversed_motion = State(cover.entity_id, STATE_OPENING, {"current_position": 80})
    assert controller.state_change_is_expected(old, reversed_motion, NOW) is False
    assert state_change_is_manual(old, reversed_motion) is True


def test_external_change_is_manual() -> None:
    old = State("cover.kitchen", STATE_OPEN, {"current_position": 20})
    new = State("cover.kitchen", STATE_OPEN, {"current_position": 100})
    assert state_change_is_manual(old, new) is True
    assert state_change_is_manual(None, new) is False
