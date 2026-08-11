"""Event-driven Home Assistant runtime for CoverCompass."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from astral import Observer  # type: ignore[import-untyped]
from astral.sun import (  # type: ignore[import-untyped]
    azimuth,
    elevation,
    sunrise,
    sunset,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_start
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .config import cover_to_dict
from .const import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    CONF_COVERS,
    CONF_DRY_RUN,
    CONF_GLOBAL_ENABLED,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
    SUN_ENTITY_ID,
)
from .controller import (
    CoverController,
    ExecutionResult,
    state_change_is_manual,
)
from .model import (
    CoverConfig,
    Decision,
    EndpointType,
    EnvironmentReadings,
    EvaluationInput,
    IntegrationConfig,
    ManualOverrideMode,
    RuleRuntimeState,
)
from .repairs import async_update_entity_issues, referenced_entities
from .rules import evaluate_cover
from .time_window import any_time_window_active, next_time_boundary

_LOGGER = logging.getLogger(__name__)


class CoverCompassRuntime:
    """Own subscriptions, stateful evaluation and command controllers."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        config: IntegrationConfig,
        entry: ConfigEntry[Any],
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.config = config
        self.entry = entry
        self.rule_states = {cover.id: RuleRuntimeState() for cover in config.covers}
        self.controllers = {
            cover.id: CoverController(hass, cover) for cover in config.covers
        }
        self.decisions: dict[str, Decision] = {}
        self.executions: dict[str, ExecutionResult] = {}
        self.latest_readings: dict[str, EnvironmentReadings] = {}
        self.solar_azimuth = 0.0
        self.solar_elevation = -90.0
        self._store: Store[dict[str, object]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry_id}"
        )
        self._unsubscribers: list[Callable[[], None]] = []
        self._deadline_unsub: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._stopping = False

    @property
    def signal_update(self) -> str:
        """Return the dispatcher signal for entity updates."""
        return f"{DOMAIN}_{self.entry_id}_update"

    @property
    def observer(self) -> Observer:
        """Return the configured house observer."""
        return Observer(
            latitude=self.config.house.latitude,
            longitude=self.config.house.longitude,
        )

    @property
    def timezone(self) -> ZoneInfo:
        """Return the configured house timezone."""
        return ZoneInfo(self.config.house.time_zone)

    async def async_start(self) -> None:
        """Restore state, subscribe to inputs and perform startup reconciliation."""
        await self._async_restore_overrides()
        entity_ids = {SUN_ENTITY_ID}
        for cover in self.config.covers:
            entity_ids.update(referenced_entities(cover))
        self._unsubscribers.append(
            async_track_state_change_event(
                self.hass, entity_ids, self._async_state_changed
            )
        )
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass,
                self._async_periodic_reconcile,
                timedelta(seconds=self.config.reconcile_interval),
            )
        )
        self._unsubscribers.append(async_at_start(self.hass, self._async_hass_started))
        await self.async_evaluate_all()

    async def async_stop(self) -> None:
        """Remove every runtime subscription."""
        self._stopping = True
        if self._deadline_unsub is not None:
            self._deadline_unsub()
            self._deadline_unsub = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    async def _async_hass_started(self, _hass: HomeAssistant) -> None:
        await self.async_evaluate_all()
        async_update_entity_issues(self.hass, self.entry_id, self.config)

    async def _async_periodic_reconcile(self, _now: datetime) -> None:
        await self.async_evaluate_all()
        async_update_entity_issues(self.hass, self.entry_id, self.config)

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        if self._stopping:
            return
        self.hass.async_create_task(
            self._async_process_state_changed(event),
            f"{DOMAIN} state change",
        )

    async def _async_process_state_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        entity_id = event.data["entity_id"]
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        now = dt_util.now(self.timezone)
        for cover in self.config.covers:
            if cover.entity_id != entity_id:
                continue
            controller = self.controllers[cover.id]
            if controller.state_change_is_expected(old_state, new_state, now):
                break
            if state_change_is_manual(old_state, new_state):
                await self.async_activate_manual_override(cover.id, now=now)
            break
        await self.async_evaluate_all(now=now)

    def _solar_event(self, kind: EndpointType, event_date: date) -> datetime:
        if kind is EndpointType.SUNRISE:
            return cast("datetime", sunrise(self.observer, event_date, self.timezone))
        if kind is EndpointType.SUNSET:
            return cast("datetime", sunset(self.observer, event_date, self.timezone))
        msg = f"Unsupported solar event: {kind}"
        raise ValueError(msg)

    def _time_active(self, cover: CoverConfig, now: datetime) -> bool:
        if not cover.time_windows:
            return False
        try:
            return any_time_window_active(cover.time_windows, now, self._solar_event)
        except ValueError as err:
            _LOGGER.warning("Cannot resolve a solar-relative time window: %s", err)
            return False

    @staticmethod
    def _numeric_state(state: State | None) -> float | None:
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    @staticmethod
    def _safety_state(state: State | None) -> bool | None:
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return None
        return state.state.lower() not in {"off", "closed", "false", "0", "idle"}

    def _readings(self, cover: CoverConfig) -> EnvironmentReadings:
        values: dict[str, float | None] = {}
        states: dict[str, str | None] = {}
        environment = cover.environment
        for condition in (
            environment.outdoor_temperature,
            environment.indoor_temperature,
            environment.illuminance,
            environment.cloud_cover,
        ):
            if condition is not None:
                values[condition.entity_id] = self._numeric_state(
                    self.hass.states.get(condition.entity_id)
                )
        if environment.weather_entity_id:
            state = self.hass.states.get(environment.weather_entity_id)
            states[environment.weather_entity_id] = (
                None
                if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}
                else state.state
            )
        if cover.wind is not None:
            values[cover.wind.entity_id] = self._numeric_state(
                self.hass.states.get(cover.wind.entity_id)
            )
        return EnvironmentReadings(values=values, states=states)

    @staticmethod
    def _position(state: State | None, attribute: str) -> int | None:
        if state is None:
            return None
        value = state.attributes.get(attribute)
        return int(value) if isinstance(value, int | float) else None

    async def async_evaluate_all(self, now: datetime | None = None) -> None:
        """Evaluate all covers once and reconcile physical state."""
        async with self._lock:
            now = now or dt_util.now(self.timezone)
            self.solar_azimuth = azimuth(self.observer, now)
            self.solar_elevation = elevation(self.observer, now)
            overrides_changed = False
            for cover in self.config.covers:
                state = self.hass.states.get(cover.entity_id)
                readings = self._readings(cover)
                self.latest_readings[cover.id] = readings
                available = state is not None and state.state not in {
                    STATE_UNKNOWN,
                    STATE_UNAVAILABLE,
                }
                inputs = EvaluationInput(
                    now=now,
                    sun_azimuth=self.solar_azimuth,
                    sun_elevation=self.solar_elevation,
                    time_active=self._time_active(cover, now),
                    current_position=self._position(state, ATTR_CURRENT_POSITION),
                    current_tilt=self._position(state, ATTR_CURRENT_TILT_POSITION),
                    cover_available=available,
                    safety_states={
                        entity_id: self._safety_state(self.hass.states.get(entity_id))
                        for entity_id in cover.safety_entities
                    },
                    readings=readings,
                )
                rule_state = self.rule_states[cover.id]
                override_before = (
                    rule_state.manual_override,
                    rule_state.manual_override_expires,
                    rule_state.manual_rule_signature,
                )
                decision = evaluate_cover(self.config, cover, inputs, rule_state)
                overrides_changed |= override_before != (
                    rule_state.manual_override,
                    rule_state.manual_override_expires,
                    rule_state.manual_rule_signature,
                )
                self.decisions[cover.id] = decision
                _LOGGER.debug(
                    "Cover %s decision=%s target=%s tilt=%s conditions=%s "
                    "sun_azimuth=%.1f sun_elevation=%.1f facade=%.1f angle=%.1f "
                    "reason=%s",
                    cover.entity_id,
                    decision.decision,
                    decision.target_position,
                    decision.target_tilt,
                    decision.conditions,
                    decision.solar.solar_azimuth,
                    decision.solar.solar_elevation,
                    decision.solar.facade_azimuth,
                    decision.solar.angular_difference,
                    decision.reason,
                )
                try:
                    self.executions[cover.id] = await self.controllers[
                        cover.id
                    ].async_reconcile(
                        decision,
                        now=now,
                        dry_run=self.config.dry_run or cover.dry_run,
                    )
                except Exception:
                    _LOGGER.exception(
                        "Failed to control configured cover %s", cover.entity_id
                    )
                    self.executions[cover.id] = ExecutionResult(
                        False, "The Home Assistant cover action failed."
                    )
            if overrides_changed:
                await self._async_save_overrides()
            self._reschedule_deadline(now)
            async_dispatcher_send(self.hass, self.signal_update)

    def _reschedule_deadline(self, now: datetime) -> None:
        if self._deadline_unsub is not None:
            self._deadline_unsub()
            self._deadline_unsub = None
        candidates: list[datetime] = []
        for cover in self.config.covers:
            try:
                boundary = next_time_boundary(
                    cover.time_windows, now, self._solar_event
                )
            except ValueError:
                boundary = None
            if boundary is not None:
                candidates.append(boundary)
            state = self.rule_states[cover.id]
            if state.pending_since is not None and state.pending_target is not None:
                delay = (
                    cover.activation_delay
                    if state.pending_target.value == "shade"
                    else cover.clear_delay
                )
                candidates.append(state.pending_since + timedelta(seconds=delay))
            if state.manual_override_expires is not None:
                candidates.append(state.manual_override_expires)
        future = [candidate for candidate in candidates if candidate > now]
        if future:
            self._deadline_unsub = async_track_point_in_utc_time(
                self.hass, self._async_deadline, min(future)
            )

    async def _async_deadline(self, now: datetime) -> None:
        self._deadline_unsub = None
        await self.async_evaluate_all(now=now.astimezone(self.timezone))

    async def async_activate_manual_override(
        self, cover_id: str, *, now: datetime | None = None, force_manual: bool = False
    ) -> None:
        """Activate the configured manual override after external movement."""
        cover = self.cover(cover_id)
        mode = ManualOverrideMode.MANUAL if force_manual else cover.manual_override_mode
        if mode is ManualOverrideMode.DISABLED:
            return
        now = now or dt_util.now(self.timezone)
        state = self.rule_states[cover_id]
        state.manual_override = True
        state.manual_override_expires = None
        state.manual_rule_signature = None
        if mode in {
            ManualOverrideMode.MINUTES_15,
            ManualOverrideMode.MINUTES_30,
            ManualOverrideMode.MINUTES_60,
        }:
            minutes = {
                ManualOverrideMode.MINUTES_15: 15,
                ManualOverrideMode.MINUTES_30: 30,
                ManualOverrideMode.MINUTES_60: 60,
            }[mode]
            state.manual_override_expires = now + timedelta(minutes=minutes)
        elif mode is ManualOverrideMode.NEXT_TRANSITION:
            decision = self.decisions.get(cover_id)
            state.manual_rule_signature = (
                decision.rule_signature if decision is not None else None
            )
        elif mode is ManualOverrideMode.UNTIL_TIME:
            assert cover.manual_override_until is not None
            expiry = datetime.combine(
                now.date(), cover.manual_override_until, tzinfo=self.timezone
            )
            if expiry <= now:
                expiry += timedelta(days=1)
            state.manual_override_expires = expiry
        await self._async_save_overrides()
        async_dispatcher_send(self.hass, self.signal_update)

    async def async_resume(self, cover_id: str) -> None:
        """Clear manual override and immediately re-evaluate all conditions."""
        state = self.rule_states[cover_id]
        state.manual_override = False
        state.manual_override_expires = None
        state.manual_rule_signature = None
        await self._async_save_overrides()
        await self.async_evaluate_all()

    async def _async_restore_overrides(self) -> None:
        saved = await self._store.async_load() or {}
        now = dt_util.now(self.timezone)
        for cover_id, raw in saved.items():
            if cover_id not in self.rule_states or not isinstance(raw, dict):
                continue
            expires_raw = raw.get("expires")
            expires = (
                datetime.fromisoformat(expires_raw)
                if isinstance(expires_raw, str)
                else None
            )
            if expires is not None and expires <= now:
                continue
            state = self.rule_states[cover_id]
            state.manual_override = bool(raw.get("active", False))
            state.manual_override_expires = expires
            signature = raw.get("signature")
            state.manual_rule_signature = (
                signature if isinstance(signature, str) else None
            )

    async def _async_save_overrides(self) -> None:
        await self._store.async_save({
            cover_id: {
                "active": state.manual_override,
                "expires": (
                    state.manual_override_expires.isoformat()
                    if state.manual_override_expires is not None
                    else None
                ),
                "signature": state.manual_rule_signature,
            }
            for cover_id, state in self.rule_states.items()
            if state.manual_override
        })

    def cover(self, cover_id: str) -> CoverConfig:
        """Return a cover configuration by stable ID."""
        return next(cover for cover in self.config.covers if cover.id == cover_id)

    def _store_options(self) -> None:
        options = {
            **self.entry.options,
            CONF_GLOBAL_ENABLED: self.config.globally_enabled,
            CONF_DRY_RUN: self.config.dry_run,
            CONF_COVERS: [cover_to_dict(cover) for cover in self.config.covers],
        }
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    async def async_set_global_enabled(self, enabled: bool) -> None:
        """Persist and apply the global automation gate."""
        self.config = replace(self.config, globally_enabled=enabled)
        self._store_options()
        await self.async_evaluate_all()

    async def async_set_dry_run(self, enabled: bool) -> None:
        """Persist and apply global dry-run mode."""
        self.config = replace(self.config, dry_run=enabled)
        self._store_options()
        await self.async_evaluate_all()

    async def async_set_cover_enabled(self, cover_id: str, enabled: bool) -> None:
        """Persist and apply one cover's automation gate."""
        covers = tuple(
            replace(cover, enabled=enabled) if cover.id == cover_id else cover
            for cover in self.config.covers
        )
        self.config = replace(self.config, covers=covers)
        self.controllers[cover_id].update_config(self.cover(cover_id))
        self._store_options()
        await self.async_evaluate_all()
