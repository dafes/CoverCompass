"""Conservative Home Assistant cover command execution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
)
from homeassistant.core import Context, HomeAssistant, State

from .const import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    ATTR_SUPPORTED_FEATURES,
    DEFAULT_COMMAND_TIMEOUT,
    POSITION_TOLERANCE,
)
from .model import CommandRecord, CoverConfig, Decision, DecisionType

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Result of reconciling desired and actual cover state."""

    command_sent: bool
    reason: str
    service_calls: tuple[str, ...] = ()


class CoverController:
    """Reconcile decisions with one physical Home Assistant cover."""

    def __init__(self, hass: HomeAssistant, cover: CoverConfig) -> None:
        self.hass = hass
        self.cover = cover
        self.last_command: CommandRecord | None = None
        self.last_automatic_command: datetime | None = None
        self._unsupported_position_warned = False
        self._unsupported_tilt_warned = False

    def update_config(self, cover: CoverConfig) -> None:
        """Replace configuration without losing pending-command tracking."""
        self.cover = cover

    @staticmethod
    def _position(state: State | None) -> int | None:
        if state is None:
            return None
        value = state.attributes.get(ATTR_CURRENT_POSITION)
        return int(value) if isinstance(value, int | float) else None

    @staticmethod
    def _tilt(state: State | None) -> int | None:
        if state is None:
            return None
        value = state.attributes.get(ATTR_CURRENT_TILT_POSITION)
        return int(value) if isinstance(value, int | float) else None

    @staticmethod
    def _at_target(current: int | None, target: int | None) -> bool:
        return target is None or (
            current is not None and abs(current - target) <= POSITION_TOLERANCE
        )

    def state_change_is_expected(
        self, old_state: State | None, new_state: State | None, now: datetime
    ) -> bool:
        """Return whether a cover event matches the latest automatic command."""
        command = self.last_command
        if command is None or new_state is None:
            return False
        if now - command.issued_at > DEFAULT_COMMAND_TIMEOUT:
            self.last_command = None
            return False
        if new_state.context.id == command.context_id or (
            new_state.context.parent_id == command.context_id
        ):
            if self._at_target(self._position(new_state), command.target_position) and (
                self._at_target(self._tilt(new_state), command.target_tilt)
            ):
                self.last_command = None
            return True
        target = command.target_position
        current = self._position(new_state)
        if target is not None:
            if current is not None and command.start_position is not None:
                lower = min(command.start_position, target) - POSITION_TOLERANCE
                upper = max(command.start_position, target) + POSITION_TOLERANCE
                old_position = self._position(old_state)
                moving_toward_target = old_position is None or (
                    current <= old_position + POSITION_TOLERANCE
                    if target < command.start_position
                    else current >= old_position - POSITION_TOLERANCE
                )
                if lower <= current <= upper and moving_toward_target:
                    if abs(current - target) <= POSITION_TOLERANCE:
                        self.last_command = None
                    return True
            expected_motion = (
                STATE_OPENING
                if target > (command.start_position or 0)
                else STATE_CLOSING
            )
            if new_state.state == expected_motion:
                return True
            if (target >= 50 and new_state.state == STATE_OPEN) or (
                target < 50 and new_state.state == STATE_CLOSED
            ):
                self.last_command = None
                return True
        target_tilt = command.target_tilt
        if target_tilt is not None and self._at_target(
            self._tilt(new_state), target_tilt
        ):
            self.last_command = None
            return True
        return False

    async def async_reconcile(
        self,
        decision: Decision,
        *,
        now: datetime,
        dry_run: bool,
    ) -> ExecutionResult:
        """Issue only supported, necessary and rate-limited cover commands."""
        if decision.decision not in (DecisionType.SHADE, DecisionType.OPEN):
            return ExecutionResult(False, decision.reason)
        state = self.hass.states.get(self.cover.entity_id)
        if state is None:
            return ExecutionResult(False, "The configured cover entity does not exist.")
        supported = CoverEntityFeature(
            int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0))
        )
        current_position = self._position(state)
        current_tilt = self._tilt(state)
        target_position = decision.target_position
        target_tilt = decision.target_tilt

        position_service: str | None = None
        position_data: dict[str, object] = {ATTR_ENTITY_ID: self.cover.entity_id}
        position_needed = not self._at_target(current_position, target_position)
        if position_needed:
            if supported & CoverEntityFeature.SET_POSITION:
                position_service = SERVICE_SET_COVER_POSITION
                position_data[ATTR_POSITION] = target_position
            elif (
                target_position is not None
                and target_position >= 50
                and (supported & CoverEntityFeature.OPEN)
            ):
                position_service = SERVICE_OPEN_COVER
            elif (
                target_position is not None
                and target_position < 50
                and (supported & CoverEntityFeature.CLOSE)
            ):
                position_service = SERVICE_CLOSE_COVER
            else:
                if not self._unsupported_position_warned:
                    _LOGGER.warning(
                        "Cover %s cannot execute requested target position %s",
                        self.cover.entity_id,
                        target_position,
                    )
                    self._unsupported_position_warned = True
            if position_service is not None:
                self._unsupported_position_warned = False
        else:
            self._unsupported_position_warned = False

        tilt_needed = target_tilt is not None and not self._at_target(
            current_tilt, target_tilt
        )
        tilt_supported = bool(supported & CoverEntityFeature.SET_TILT_POSITION)
        if tilt_needed and not tilt_supported:
            if not self._unsupported_tilt_warned:
                _LOGGER.warning(
                    "Cover %s is configured for tilt but does not support tilt "
                    "positioning",
                    self.cover.entity_id,
                )
                self._unsupported_tilt_warned = True
        else:
            self._unsupported_tilt_warned = False
        if position_service is None and not (tilt_needed and tilt_supported):
            if position_needed:
                return ExecutionResult(
                    False, "Cover does not support the required movement."
                )
            return ExecutionResult(False, "Cover is already at every requested target.")
        if dry_run:
            return ExecutionResult(
                False, "Dry run: the required movement was not sent."
            )
        if (
            not decision.safety_active
            and self.last_automatic_command is not None
            and (
                now - self.last_automatic_command
                < timedelta(seconds=self.cover.minimum_movement_interval)
            )
        ):
            return ExecutionResult(False, "Minimum movement interval is still active.")
        if self.last_command is not None and (
            now - self.last_command.issued_at <= DEFAULT_COMMAND_TIMEOUT
            and self.last_command.target_position == target_position
            and self.last_command.target_tilt == target_tilt
        ):
            return ExecutionResult(
                False, "An identical command is already in progress."
            )

        context = Context()
        self.last_command = CommandRecord(
            issued_at=now,
            start_position=current_position,
            target_position=target_position if position_service is not None else None,
            target_tilt=target_tilt if tilt_needed and tilt_supported else None,
            context_id=context.id,
        )
        calls: list[str] = []
        if position_service is not None:
            await self.hass.services.async_call(
                "cover",
                position_service,
                position_data,
                context=context,
                blocking=False,
            )
            calls.append(position_service)
        if tilt_needed and tilt_supported:
            await self.hass.services.async_call(
                "cover",
                SERVICE_SET_COVER_TILT_POSITION,
                {
                    ATTR_ENTITY_ID: self.cover.entity_id,
                    ATTR_TILT_POSITION: target_tilt,
                },
                context=context,
                blocking=False,
            )
            calls.append("set_cover_tilt_position")
        self.last_automatic_command = now
        return ExecutionResult(True, "Automatic command sent.", tuple(calls))


def state_change_is_manual(old_state: State | None, new_state: State | None) -> bool:
    """Return whether two states contain a substantive physical cover change."""
    if old_state is None or new_state is None:
        return False
    ignored_states = {"unknown", "unavailable"}
    if old_state.state in ignored_states or new_state.state in ignored_states:
        return False
    old_position = old_state.attributes.get(ATTR_CURRENT_POSITION)
    new_position = new_state.attributes.get(ATTR_CURRENT_POSITION)
    old_tilt = old_state.attributes.get(ATTR_CURRENT_TILT_POSITION)
    new_tilt = new_state.attributes.get(ATTR_CURRENT_TILT_POSITION)
    return (
        old_position != new_position
        or old_tilt != new_tilt
        or (
            old_state.state != new_state.state
            and new_state.state
            in {STATE_OPEN, STATE_CLOSED, STATE_OPENING, STATE_CLOSING}
        )
    )
