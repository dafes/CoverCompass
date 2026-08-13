"""UI configuration and cover-management flows for CoverCompass."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.core import callback
from homeassistant.helpers import selector

from .config import cover_from_dict, cover_to_dict, validate_cover
from .const import (
    CONF_COVERS,
    CONF_DRY_RUN,
    CONF_GLOBAL_ENABLED,
    CONF_HOUSE_NAME,
    CONF_HOUSE_ROTATION,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RECONCILE_INTERVAL,
    CONF_TIME_ZONE,
    CONFIG_VERSION,
    DEFAULT_ACTIVATION_DELAY,
    DEFAULT_CLEAR_DELAY,
    DEFAULT_EXPOSURE_ANGLE,
    DEFAULT_HOUSE_NAME,
    DEFAULT_MIN_ELEVATION,
    DEFAULT_MIN_MOVEMENT_INTERVAL,
    DEFAULT_NORMAL_POSITION,
    DEFAULT_RECONCILE_INTERVAL,
    DEFAULT_SHADE_POSITION,
    DOMAIN,
)
from .model import (
    ORIENTATION_DEGREES,
    AdvancedMatch,
    AutomationMode,
    ConditionKey,
    EndpointType,
    ManualOverrideMode,
    SafetyPolicy,
    new_cover_id,
)
from .plan import CoverPlan, PlannedShutter, PlanValidationError, parse_cover_plan

ORIENTATIONS = [*ORIENTATION_DEGREES, "custom"]
WEATHER_STATES = [
    "sunny",
    "clear-night",
    "partlycloudy",
    "cloudy",
    "fog",
    "rainy",
    "pouring",
    "snowy",
    "snowy-rainy",
    "hail",
    "lightning",
    "lightning-rainy",
    "windy",
    "windy-variant",
    "exceptional",
]


def _number(
    minimum: float,
    maximum: float,
    step: float | Literal["any"],
    unit: str | None = None,
) -> selector.NumberSelector:
    config: selector.NumberSelectorConfig = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit is not None:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _select(
    options: list[str], translation_key: str, *, multiple: bool = False
) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=translation_key,
            mode=selector.SelectSelectorMode.DROPDOWN,
            multiple=multiple,
        )
    )


def _optional_marker(key: str, value: Any = None) -> vol.Optional:
    if value is None:
        return vol.Optional(key)
    return vol.Optional(key, description={"suggested_value": value})


def _house_schema(hass: Any, current: Mapping[str, Any] | None = None) -> vol.Schema:
    current = current or {}
    return vol.Schema({
        vol.Required(
            CONF_HOUSE_NAME,
            default=current.get(
                CONF_HOUSE_NAME,
                hass.config.location_name or DEFAULT_HOUSE_NAME,
            ),
        ): selector.TextSelector(),
        vol.Required(
            CONF_LATITUDE, default=current.get(CONF_LATITUDE, hass.config.latitude)
        ): _number(-90, 90, "any", "°"),
        vol.Required(
            CONF_LONGITUDE,
            default=current.get(CONF_LONGITUDE, hass.config.longitude),
        ): _number(-180, 180, "any", "°"),
        vol.Required(
            CONF_TIME_ZONE,
            default=current.get(CONF_TIME_ZONE, hass.config.time_zone),
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=sorted(available_timezones()),
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(
            CONF_HOUSE_ROTATION,
            default=current.get(CONF_HOUSE_ROTATION, 0.0),
        ): _number(0, 359.9, 0.1, "°"),
    })


def _orientation_choice(azimuth: float) -> str:
    for name, degrees in ORIENTATION_DEGREES.items():
        if abs(azimuth - degrees) < 0.01:
            return name
    return "custom"


def _house_data_from_plan(plan: CoverPlan) -> dict[str, Any]:
    return {
        CONF_HOUSE_NAME: plan.house.name,
        CONF_LATITUDE: plan.house.latitude,
        CONF_LONGITUDE: plan.house.longitude,
        CONF_TIME_ZONE: plan.house.time_zone,
        CONF_HOUSE_ROTATION: plan.house.rotation,
    }


def _covers_from_plan(
    plan: CoverPlan,
    assignments: Mapping[str, str],
    existing_covers: list[dict[str, Any]],
    *,
    remove_unmapped: bool,
) -> list[dict[str, Any]]:
    existing_by_entity = {
        str(cover.get("entity_id")): cover for cover in existing_covers
    }
    assigned_entities = set(assignments.values())
    unmatched = [
        cover
        for cover in existing_covers
        if cover.get("entity_id") not in assigned_entities
    ]
    reserved_ids = {str(cover.get("id")) for cover in existing_covers}
    used_ids: set[str] = set()
    imported: list[dict[str, Any]] = []
    for shutter in plan.shutters:
        entity_id = assignments[shutter.id]
        current = existing_by_entity.get(entity_id)
        if current is not None:
            cover = dict(current)
            cover_id = str(cover["id"])
        else:
            cover_id = shutter.id
            while cover_id in reserved_ids or cover_id in used_ids:
                cover_id = new_cover_id()
            cover = {"id": cover_id, "entity_id": entity_id}
        used_ids.add(cover_id)
        cover.update({
            "name": shutter.name,
            "entity_id": entity_id,
            "facade_azimuth": shutter.facade_azimuth,
        })
        imported.append(cover_to_dict(cover_from_dict(cover)))
    if not remove_unmapped:
        imported.extend(unmatched)
    return imported


class _CoverFlowMixin:
    """Reusable multi-step editor shared by setup and options flows."""

    hass: Any
    _working_cover: dict[str, Any]
    _editing_cover_id: str | None
    _plan: CoverPlan | None
    _plan_assignments: dict[str, str]
    _plan_index: int

    if TYPE_CHECKING:

        def async_show_form(
            self,
            *,
            step_id: str | None = None,
            data_schema: vol.Schema | None = None,
            errors: dict[str, str] | None = None,
            description_placeholders: Mapping[str, str] | None = None,
            last_step: bool | None = None,
            preview: str | None = None,
        ) -> config_entries.ConfigFlowResult: ...

    def _begin_cover(self, cover: dict[str, Any] | None = None) -> None:
        self._working_cover = dict(cover or {})
        self._working_cover.setdefault("id", new_cover_id())
        self._editing_cover_id = cover.get("id") if cover is not None else None

    def _configured_covers(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _plan_import_fields(self) -> dict[vol.Marker, object]:
        return {}

    def _set_plan_import_options(self, _user_input: Mapping[str, Any]) -> None:
        return

    def _suggest_plan_entity(self, shutter: PlannedShutter) -> str | None:
        return None

    async def _async_plan_import_finished(
        self, plan: CoverPlan, assignments: Mapping[str, str]
    ) -> config_entries.ConfigFlowResult:
        raise NotImplementedError

    async def async_step_import_plan(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate a planner export before assigning physical entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                plan = parse_cover_plan(user_input["plan_json"])
            except PlanValidationError as err:
                errors["plan_json"] = err.code
            else:
                self._plan = plan
                self._plan_assignments = {}
                self._plan_index = 0
                self._set_plan_import_options(user_input)
                return await self.async_step_assign_plan_cover()
        schema: dict[vol.Marker, object] = {
            vol.Required("plan_json"): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            )
        }
        schema.update(self._plan_import_fields())
        return self.async_show_form(
            step_id="import_plan", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_assign_plan_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Assign one physical cover to the current planned shutter."""
        if self._plan is None:
            return await self.async_step_import_plan()
        shutter = self._plan.shutters[self._plan_index]
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input["entity_id"]
            if entity_id in self._plan_assignments.values():
                errors["entity_id"] = "plan_entity_already_assigned"
            else:
                self._plan_assignments[shutter.id] = entity_id
                self._plan_index += 1
                if self._plan_index == len(self._plan.shutters):
                    return await self._async_plan_import_finished(
                        self._plan, self._plan_assignments
                    )
                return await self.async_step_assign_plan_cover()
        suggestion = self._suggest_plan_entity(shutter)
        marker = (
            vol.Required("entity_id", default=suggestion)
            if suggestion
            else vol.Required("entity_id")
        )
        return self.async_show_form(
            step_id="assign_plan_cover",
            data_schema=vol.Schema({
                marker: selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="cover")
                )
            }),
            errors=errors,
            description_placeholders={
                "name": shutter.name,
                "azimuth": f"{shutter.facade_azimuth:.1f}°",
                "progress": f"{self._plan_index + 1}/{len(self._plan.shutters)}",
            },
        )

    async def _async_cover_finished(
        self, cover: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        raise NotImplementedError

    async def async_step_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure identity, entity and facade orientation."""
        errors: dict[str, str] = {}
        current = self._working_cover
        if user_input is not None:
            duplicate = any(
                item.get("entity_id") == user_input["entity_id"]
                and item.get("id") != self._editing_cover_id
                for item in self._configured_covers()
            )
            if duplicate:
                errors["entity_id"] = "cover_already_configured"
            else:
                choice = user_input.pop("orientation_choice")
                azimuth = (
                    float(user_input.pop("custom_azimuth"))
                    if choice == "custom"
                    else ORIENTATION_DEGREES[choice]
                )
                self._working_cover.update(user_input)
                self._working_cover["facade_azimuth"] = azimuth
                return await self.async_step_cover_policy()
        azimuth = float(current.get("facade_azimuth", 180.0))
        schema: dict[vol.Marker, object] = {
            vol.Required(
                "name", default=current.get("name", "")
            ): selector.TextSelector(),
            vol.Required(
                "entity_id", default=current.get("entity_id", "")
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="cover")),
            _optional_marker(
                "area_id", current.get("area_id")
            ): selector.AreaSelector(),
            vol.Required(
                "orientation_choice", default=_orientation_choice(azimuth)
            ): _select(ORIENTATIONS, "orientation"),
            vol.Required("custom_azimuth", default=azimuth): _number(
                0, 359.9, 0.1, "°"
            ),
            vol.Required(
                "enabled", default=current.get("enabled", True)
            ): selector.BooleanSelector(),
            vol.Required(
                "dry_run", default=current.get("dry_run", False)
            ): selector.BooleanSelector(),
        }
        return self.async_show_form(
            step_id="cover", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_cover_policy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure geometry, targets, delays and override behavior."""
        errors: dict[str, str] = {}
        current = self._working_cover
        if user_input is not None:
            maximum = user_input.get("maximum_elevation")
            if maximum is not None and float(maximum) < float(
                user_input["minimum_elevation"]
            ):
                errors["maximum_elevation"] = "maximum_below_minimum"
            elif user_input["mode"] == AutomationMode.ADVANCED and not user_input.get(
                "advanced_conditions"
            ):
                errors["advanced_conditions"] = "advanced_conditions_required"
            elif user_input[
                "manual_override_mode"
            ] == ManualOverrideMode.UNTIL_TIME and not user_input.get(
                "manual_override_until"
            ):
                errors["manual_override_until"] = "override_time_required"
            else:
                self._working_cover.update(user_input)
                return await self.async_step_cover_time()
        schema: dict[vol.Marker, object] = {
            vol.Required(
                "exposure_angle",
                default=current.get("exposure_angle", DEFAULT_EXPOSURE_ANGLE),
            ): _number(0, 180, 0.1, "°"),
            vol.Required(
                "solar_exit_margin", default=current.get("solar_exit_margin", 3.0)
            ): _number(0, 30, 0.1, "°"),
            vol.Required(
                "minimum_elevation",
                default=current.get("minimum_elevation", DEFAULT_MIN_ELEVATION),
            ): _number(-10, 90, 0.1, "°"),
            _optional_marker(
                "maximum_elevation", current.get("maximum_elevation")
            ): _number(-10, 90, 0.1, "°"),
            vol.Required(
                "elevation_exit_margin",
                default=current.get("elevation_exit_margin", 1.0),
            ): _number(0, 15, 0.1, "°"),
            vol.Required(
                "mode", default=current.get("mode", AutomationMode.SUN)
            ): _select([item.value for item in AutomationMode], "automation_mode"),
            vol.Required(
                "normal_position",
                default=current.get("normal_position", DEFAULT_NORMAL_POSITION),
            ): _number(0, 100, 1, "%"),
            vol.Required(
                "shading_position",
                default=current.get("shading_position", DEFAULT_SHADE_POSITION),
            ): _number(0, 100, 1, "%"),
            vol.Required(
                "activation_delay",
                default=current.get("activation_delay", DEFAULT_ACTIVATION_DELAY),
            ): _number(0, 7200, 1, "s"),
            vol.Required(
                "clear_delay", default=current.get("clear_delay", DEFAULT_CLEAR_DELAY)
            ): _number(0, 7200, 1, "s"),
            vol.Required(
                "minimum_movement_interval",
                default=current.get(
                    "minimum_movement_interval", DEFAULT_MIN_MOVEMENT_INTERVAL
                ),
            ): _number(0, 86400, 1, "s"),
            vol.Required(
                "manual_override_mode",
                default=current.get(
                    "manual_override_mode", ManualOverrideMode.MINUTES_60
                ),
            ): _select(
                [item.value for item in ManualOverrideMode], "manual_override_mode"
            ),
            _optional_marker(
                "manual_override_until", current.get("manual_override_until")
            ): selector.TimeSelector(),
            vol.Required(
                "advanced_match",
                default=current.get("advanced_match", AdvancedMatch.ALL),
            ): _select([item.value for item in AdvancedMatch], "advanced_match"),
            vol.Optional(
                "advanced_conditions",
                default=current.get("advanced_conditions", []),
            ): _select(
                [item.value for item in ConditionKey],
                "advanced_condition",
                multiple=True,
            ),
        }
        state = self.hass.states.get(str(current.get("entity_id", "")))
        features = int(state.attributes.get("supported_features", 0)) if state else 0
        if features & CoverEntityFeature.SET_TILT_POSITION:
            schema[_optional_marker("normal_tilt", current.get("normal_tilt"))] = (
                _number(0, 100, 1, "%")
            )
            schema[_optional_marker("shading_tilt", current.get("shading_tilt"))] = (
                _number(0, 100, 1, "%")
            )
        else:
            self._working_cover["normal_tilt"] = None
            self._working_cover["shading_tilt"] = None
        return self.async_show_form(
            step_id="cover_policy", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_cover_time(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure a robust optional local or solar-relative time window."""
        errors: dict[str, str] = {}
        existing = (self._working_cover.get("time_windows") or [{}])[0]
        if user_input is not None:
            required_mode = self._working_cover.get("mode") in {
                AutomationMode.TIME,
                AutomationMode.SUN_AND_TIME,
                AutomationMode.SUN_OR_TIME,
            }
            if required_mode and not user_input["use_time_window"]:
                errors["use_time_window"] = "time_window_required"
            else:
                if user_input.pop("use_time_window"):
                    self._working_cover["time_windows"] = [
                        {
                            "start": {
                                "kind": user_input["start_kind"],
                                "value": user_input["start_time"],
                                "offset_minutes": user_input["start_offset"],
                            },
                            "end": {
                                "kind": user_input["end_kind"],
                                "value": user_input["end_time"],
                                "offset_minutes": user_input["end_offset"],
                            },
                            "weekdays": [int(day) for day in user_input["weekdays"]],
                        }
                    ]
                else:
                    self._working_cover["time_windows"] = []
                return await self.async_step_cover_environment()
        start = existing.get("start", {})
        end = existing.get("end", {})
        return self.async_show_form(
            step_id="cover_time",
            data_schema=vol.Schema({
                vol.Required(
                    "use_time_window",
                    default=bool(self._working_cover.get("time_windows")),
                ): selector.BooleanSelector(),
                vol.Required(
                    "start_kind", default=start.get("kind", EndpointType.FIXED)
                ): _select([item.value for item in EndpointType], "endpoint_type"),
                vol.Required(
                    "start_time", default=start.get("value", "07:00:00")
                ): selector.TimeSelector(),
                vol.Required(
                    "start_offset", default=start.get("offset_minutes", 0)
                ): _number(-720, 720, 1, "min"),
                vol.Required(
                    "end_kind", default=end.get("kind", EndpointType.FIXED)
                ): _select([item.value for item in EndpointType], "endpoint_type"),
                vol.Required(
                    "end_time", default=end.get("value", "18:00:00")
                ): selector.TimeSelector(),
                vol.Required(
                    "end_offset", default=end.get("offset_minutes", 0)
                ): _number(-720, 720, 1, "min"),
                vol.Required(
                    "weekdays",
                    default=[str(day) for day in existing.get("weekdays", range(7))],
                ): _select([str(day) for day in range(7)], "weekday", multiple=True),
            }),
            errors=errors,
        )

    async def async_step_cover_environment(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure optional entity-based environmental confirmation."""
        environment = self._working_cover.get("environment") or {}
        errors: dict[str, str] = {}
        if user_input is not None:
            for prefix in ("outdoor", "indoor", "illuminance"):
                if user_input.get(f"{prefix}_entity") and (
                    float(user_input[f"{prefix}_clear"])
                    > float(user_input[f"{prefix}_activate"])
                ):
                    errors[f"{prefix}_clear"] = "clear_above_activation"
            if user_input.get("cloud_entity") and (
                float(user_input["cloud_clear"]) < float(user_input["cloud_activate"])
            ):
                errors["cloud_clear"] = "clear_below_activation"
            if not errors:
                built: dict[str, Any] = {}
                for prefix, key in (
                    ("outdoor", "outdoor_temperature"),
                    ("indoor", "indoor_temperature"),
                    ("illuminance", "illuminance"),
                ):
                    entity_id = user_input.get(f"{prefix}_entity")
                    if entity_id:
                        built[key] = {
                            "entity_id": entity_id,
                            "activate_at": user_input[f"{prefix}_activate"],
                            "clear_at": user_input[f"{prefix}_clear"],
                        }
                if user_input.get("cloud_entity"):
                    built["cloud_cover"] = {
                        "entity_id": user_input["cloud_entity"],
                        "activate_at_or_below": user_input["cloud_activate"],
                        "clear_at_or_above": user_input["cloud_clear"],
                    }
                if user_input.get("weather_entity_id"):
                    built["weather_entity_id"] = user_input["weather_entity_id"]
                    built["allowed_weather_states"] = user_input[
                        "allowed_weather_states"
                    ]
                self._working_cover["environment"] = built
                return await self.async_step_cover_safety()

        def threshold(key: str, field: str, fallback: float) -> float:
            return float((environment.get(key) or {}).get(field, fallback))

        schema: dict[vol.Marker, object] = {}
        for prefix, key, activate_default, clear_default in (
            ("outdoor", "outdoor_temperature", 23.0, 22.0),
            ("indoor", "indoor_temperature", 22.0, 21.0),
            ("illuminance", "illuminance", 10000.0, 8000.0),
        ):
            schema[
                _optional_marker(
                    f"{prefix}_entity", (environment.get(key) or {}).get("entity_id")
                )
            ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
            schema[
                vol.Required(
                    f"{prefix}_activate",
                    default=threshold(key, "activate_at", activate_default),
                )
            ] = _number(-100, 200000, 0.1)
            schema[
                vol.Required(
                    f"{prefix}_clear",
                    default=threshold(key, "clear_at", clear_default),
                )
            ] = _number(-100, 200000, 0.1)
        cloud = environment.get("cloud_cover") or {}
        schema[_optional_marker("cloud_entity", cloud.get("entity_id"))] = (
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
        )
        schema[
            vol.Required(
                "cloud_activate", default=cloud.get("activate_at_or_below", 40.0)
            )
        ] = _number(0, 100, 1, "%")
        schema[
            vol.Required("cloud_clear", default=cloud.get("clear_at_or_above", 55.0))
        ] = _number(0, 100, 1, "%")
        schema[
            _optional_marker("weather_entity_id", environment.get("weather_entity_id"))
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="weather"))
        schema[
            vol.Optional(
                "allowed_weather_states",
                default=environment.get(
                    "allowed_weather_states", ["sunny", "partlycloudy"]
                ),
            )
        ] = _select(WEATHER_STATES, "weather_state", multiple=True)
        return self.async_show_form(
            step_id="cover_environment", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_cover_safety(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure safety interlocks and optional wind protection."""
        errors: dict[str, str] = {}
        wind = self._working_cover.get("wind") or {}
        if user_input is not None:
            if user_input.get("wind_entity") and (
                float(user_input["wind_safe"]) > float(user_input["wind_unsafe"])
            ):
                errors["wind_safe"] = "safe_above_unsafe"
            else:
                self._working_cover["safety_entities"] = user_input["safety_entities"]
                self._working_cover["safety_policy"] = user_input["safety_policy"]
                self._working_cover["wind"] = (
                    {
                        "entity_id": user_input["wind_entity"],
                        "unsafe_at": user_input["wind_unsafe"],
                        "safe_at": user_input["wind_safe"],
                        "retract": user_input["wind_retract"],
                    }
                    if user_input.get("wind_entity")
                    else None
                )
                try:
                    cover = cover_from_dict(self._working_cover)
                    validate_cover(cover)
                except (KeyError, TypeError, ValueError):
                    errors["base"] = "invalid_cover_configuration"
                else:
                    return await self._async_cover_finished(cover_to_dict(cover))
        return self.async_show_form(
            step_id="cover_safety",
            data_schema=vol.Schema({
                vol.Optional(
                    "safety_entities",
                    default=self._working_cover.get("safety_entities", []),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["binary_sensor", "input_boolean"], multiple=True
                    )
                ),
                vol.Required(
                    "safety_policy",
                    default=self._working_cover.get(
                        "safety_policy", SafetyPolicy.BLOCK_LOWERING
                    ),
                ): _select([item.value for item in SafetyPolicy], "safety_policy"),
                _optional_marker(
                    "wind_entity", wind.get("entity_id")
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(
                    "wind_unsafe", default=wind.get("unsafe_at", 40.0)
                ): _number(0, 300, 0.1),
                vol.Required("wind_safe", default=wind.get("safe_at", 30.0)): _number(
                    0, 300, 0.1
                ),
                vol.Required(
                    "wind_retract", default=wind.get("retract", True)
                ): selector.BooleanSelector(),
            }),
            errors=errors,
        )


class CoverCompassConfigFlow(_CoverFlowMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Create and reconfigure a virtual CoverCompass house."""

    VERSION = CONFIG_VERSION
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._house_data: dict[str, Any] = {}
        self._covers: list[dict[str, Any]] = []
        self._working_cover = {}
        self._editing_cover_id = None
        self._initial_options: dict[str, Any] = {}
        self._plan = None
        self._plan_assignments = {}
        self._plan_index = 0

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> CoverCompassOptionsFlow:
        return CoverCompassOptionsFlow()

    def _configured_covers(self) -> list[dict[str, Any]]:
        return self._covers

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Choose visual-plan import or the manual setup flow."""
        if user_input is not None:
            return await self.async_step_manual_setup(user_input)
        return self.async_show_menu(
            step_id="user", menu_options=["import_plan", "manual_setup"]
        )

    async def async_step_manual_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure the house and default location."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                ZoneInfo(user_input[CONF_TIME_ZONE])
            except ZoneInfoNotFoundError:
                errors[CONF_TIME_ZONE] = "invalid_time_zone"
            else:
                unique_id = (
                    f"{user_input[CONF_HOUSE_NAME].strip().casefold()}:"
                    f"{float(user_input[CONF_LATITUDE]):.6f}:"
                    f"{float(user_input[CONF_LONGITUDE]):.6f}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                self._house_data = user_input
                self._begin_cover()
                return await self.async_step_cover()
        return self.async_show_form(
            step_id="manual_setup", data_schema=_house_schema(self.hass), errors=errors
        )

    async def _async_plan_import_finished(
        self, plan: CoverPlan, assignments: Mapping[str, str]
    ) -> config_entries.ConfigFlowResult:
        house_data = _house_data_from_plan(plan)
        unique_id = (
            f"{plan.house.name.strip().casefold()}:"
            f"{plan.house.latitude:.6f}:{plan.house.longitude:.6f}"
        )
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        self._house_data = house_data
        self._covers = _covers_from_plan(plan, assignments, [], remove_unmapped=True)
        return await self.async_step_finish()

    async def _async_cover_finished(
        self, cover: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        self._covers.append(cover)
        return await self.async_step_setup_complete()

    async def async_step_setup_complete(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="setup_complete", menu_options=["add_cover", "finish"]
        )

    async def async_step_add_cover(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        self._begin_cover()
        return await self.async_step_cover()

    async def async_step_finish(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        self._initial_options = {
            CONF_COVERS: self._covers,
            CONF_GLOBAL_ENABLED: True,
            CONF_DRY_RUN: True,
            CONF_RECONCILE_INTERVAL: DEFAULT_RECONCILE_INTERVAL,
        }
        return self.async_create_entry(
            title=self._house_data[CONF_HOUSE_NAME], data=self._house_data
        )

    async def async_on_create_entry(
        self, result: config_entries.ConfigFlowResult
    ) -> config_entries.ConfigFlowResult:
        result["options"] = self._initial_options
        return result

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Reconfigure house coordinates, timezone and rotation."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                ZoneInfo(user_input[CONF_TIME_ZONE])
            except ZoneInfoNotFoundError:
                errors[CONF_TIME_ZONE] = "invalid_time_zone"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    title=user_input[CONF_HOUSE_NAME],
                    data=user_input,
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_house_schema(self.hass, entry.data),
            errors=errors,
        )


class CoverCompassOptionsFlow(_CoverFlowMixin, config_entries.OptionsFlowWithReload):
    """Add, edit, duplicate and remove covers without rebuilding the house."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._working_cover = {}
        self._editing_cover_id = None
        self._selection_action = ""
        self._plan = None
        self._plan_assignments = {}
        self._plan_index = 0
        self._plan_remove_unmapped = False
        self._plan_update_house = True

    def _configured_covers(self) -> list[dict[str, Any]]:
        return list(self._options.get(CONF_COVERS, []))

    async def async_step_init(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        self._options = dict(self.config_entry.options)
        menu = ["global", "import_plan", "add_cover"]
        if self._configured_covers():
            menu.extend(["edit_cover", "duplicate_cover", "remove_cover"])
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_global(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._options.update(user_input)
            return self.async_create_entry(data=self._options)
        return self.async_show_form(
            step_id="global",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_GLOBAL_ENABLED,
                    default=self._options.get(CONF_GLOBAL_ENABLED, True),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_DRY_RUN,
                    default=self._options.get(CONF_DRY_RUN, True),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_RECONCILE_INTERVAL,
                    default=self._options.get(
                        CONF_RECONCILE_INTERVAL, DEFAULT_RECONCILE_INTERVAL
                    ),
                ): _number(60, 3600, 1, "s"),
            }),
        )

    def _plan_import_fields(self) -> dict[vol.Marker, object]:
        return {
            vol.Required("update_house", default=True): selector.BooleanSelector(),
            vol.Required("remove_unmapped", default=False): selector.BooleanSelector(),
        }

    def _set_plan_import_options(self, user_input: Mapping[str, Any]) -> None:
        self._plan_update_house = bool(user_input["update_house"])
        self._plan_remove_unmapped = bool(user_input["remove_unmapped"])

    def _suggest_plan_entity(self, shutter: PlannedShutter) -> str | None:
        existing = next(
            (
                cover
                for cover in self._configured_covers()
                if cover.get("id") == shutter.id or cover.get("name") == shutter.name
            ),
            None,
        )
        return str(existing["entity_id"]) if existing is not None else None

    async def _async_plan_import_finished(
        self, plan: CoverPlan, assignments: Mapping[str, str]
    ) -> config_entries.ConfigFlowResult:
        self._options[CONF_COVERS] = _covers_from_plan(
            plan,
            assignments,
            self._configured_covers(),
            remove_unmapped=self._plan_remove_unmapped,
        )
        if self._plan_update_house:
            data = dict(self.config_entry.data)
            data.update(_house_data_from_plan(plan))
            self.hass.config_entries.async_update_entry(
                self.config_entry, title=plan.house.name, data=data
            )
        return self.async_create_entry(data=self._options)

    def _cover_selector_schema(self, action: str) -> vol.Schema:
        options: list[selector.SelectOptionDict] = [
            {"value": cover["id"], "label": cover["name"]}
            for cover in self._configured_covers()
        ]
        return vol.Schema({
            vol.Required("cover_id"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options)
            )
        })

    async def async_step_add_cover(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        self._begin_cover()
        return await self.async_step_cover()

    async def async_step_edit_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            selected = next(
                cover
                for cover in self._configured_covers()
                if cover["id"] == user_input["cover_id"]
            )
            self._begin_cover(selected)
            return await self.async_step_cover()
        return self.async_show_form(
            step_id="edit_cover", data_schema=self._cover_selector_schema("edit")
        )

    async def async_step_duplicate_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            selected = dict(
                next(
                    cover
                    for cover in self._configured_covers()
                    if cover["id"] == user_input["cover_id"]
                )
            )
            selected["id"] = new_cover_id()
            selected.pop("entity_id", None)
            self._begin_cover(selected)
            self._editing_cover_id = None
            return await self.async_step_cover()
        return self.async_show_form(
            step_id="duplicate_cover",
            data_schema=self._cover_selector_schema("duplicate"),
        )

    async def async_step_remove_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._editing_cover_id = user_input["cover_id"]
            return await self.async_step_confirm_remove()
        return self.async_show_form(
            step_id="remove_cover", data_schema=self._cover_selector_schema("remove")
        )

    async def async_step_confirm_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and user_input["confirm"]:
            self._options[CONF_COVERS] = [
                cover
                for cover in self._configured_covers()
                if cover["id"] != self._editing_cover_id
            ]
            return self.async_create_entry(data=self._options)
        if user_input is not None:
            errors["confirm"] = "confirmation_required"
        cover = next(
            item
            for item in self._configured_covers()
            if item["id"] == self._editing_cover_id
        )
        return self.async_show_form(
            step_id="confirm_remove",
            data_schema=vol.Schema({
                vol.Required("confirm", default=False): selector.BooleanSelector()
            }),
            errors=errors,
            description_placeholders={"name": cover["name"]},
        )

    async def _async_cover_finished(
        self, cover: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        covers = self._configured_covers()
        if self._editing_cover_id is None:
            covers.append(cover)
        else:
            covers = [
                cover if item["id"] == self._editing_cover_id else item
                for item in covers
            ]
        self._options[CONF_COVERS] = covers
        return self.async_create_entry(data=self._options)
