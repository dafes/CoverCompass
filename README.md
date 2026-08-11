# CoverCompass

CoverCompass is a local, hardware-independent Home Assistant custom integration for orientation-aware solar shading. It controls existing `cover.*` entities only after evaluating the physical relationship between the configured house location, a window's facade azimuth, the current solar azimuth and elevation, optional schedules, environmental confirmation, manual overrides, and safety interlocks.

It does not talk to motors directly and it needs no cloud service. Solar geometry is calculated locally from the configured house coordinates. The default global **Dry run** setting lets the complete evaluator run without making any cover service call.

## Example

A kitchen window is configured as follows:

- south-east orientation (`135°`);
- shading position `25%`;
- normal position `100%`;
- **Sun AND Time** mode;
- active from `07:00` to `14:00`;
- outdoor temperature activates at `23 °C` and clears at a lower hysteresis threshold.

The cover shades only while the sun is within the configured exposure angle, above the minimum elevation, inside the time window, and the temperature condition passes continuously for the activation delay. The result changes naturally through the year because the solar path changes; the rule is not a fixed open/close timetable.

## Installation and setup

1. In HACS, add this repository as a custom **Integration** repository and install CoverCompass.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → CoverCompass**.
4. Confirm or override the Home Assistant location and timezone.
5. Add one or more existing cover entities and configure their facade, targets, rules, delays, override policy, and optional safety inputs.
6. Leave global **Dry run** enabled during commissioning. Review the per-cover State entity for several days, then turn dry run off when the decisions are correct.

Use **Configure** on the integration to add, edit, duplicate, remove, enable, or disable individual cover definitions. Use **Reconfigure** to change house coordinates, timezone, or overall rotation. No YAML is required.

## Orientation and solar exposure

Azimuth is stored as degrees clockwise from geographic north:

- north `0°`;
- east `90°`;
- south `180°`;
- west `270°`.

The UI includes all eight compass points and a custom `0–359.9°` value. An exposure of `±55°` around a `135°` south-east facade means the horizontal condition is active from `80°` through `190°`. Circular comparison handles north correctly: a `350°` facade and a `10°` sun differ by `20°`, not `340°`.

Minimum and optional maximum solar elevation model windows affected only at particular sun heights. Separate exit margins, temperature/cloud hysteresis, activation delay, clearing delay, and minimum movement interval prevent boundary oscillation.

Fixed, sunrise-relative, and sunset-relative time endpoints are supported. A range whose end resolves before its start crosses midnight. Selected weekdays belong to the start of that range, so a Monday `22:00–06:00` window remains active early Tuesday.

## Modes and positions

Supported modes are **Disabled**, **Sun only**, **Time only**, **Sun AND Time**, **Sun OR Time**, and **Advanced Rules**. Advanced Rules combines explicitly selected sun, time, and environmental conditions with either all/any matching; it is deliberately a bounded condition model rather than a scripting language.

Home Assistant cover semantics are always:

- `0%` = closed;
- `100%` = fully open.

CoverCompass checks `supported_features` at runtime. Position-capable covers use `set_cover_position`. An open/close-only cover maps targets at or above `50%` to open and lower targets to close when those actions are supported. Optional tilt targets are offered only when the selected entity advertises tilt positioning.

## Manual override

CoverCompass records each command, its Home Assistant context, starting position, target position, target tilt, and a bounded completion window. Matching state changes are treated as the integration's own movement. A conflicting or unrelated physical/entity change activates the configured override: 15 minutes, 30 minutes, 1 hour, until the next rule transition, until a local time, or until manually resumed.

Absolute expiries and manual pauses are stored across restarts. Each managed cover exposes **Pause automation** and **Resume automation** buttons. After resume or expiry, the entire current rule is evaluated again; CoverCompass does not blindly replay an old command.

## Safety and wind

Door contacts and input booleans can block lowering or all movement. An unavailable configured interlock is treated as unsafe. When the interlock clears, the complete current rule is evaluated before any action.

Optional wind protection has independent unsafe/safe thresholds. Unsafe wind suppresses deployment and can fully retract an exterior awning or blind. Configure this only after confirming which cover direction is physically safe. The global automation switch is an absolute cutoff and dry run never sends even a safety movement.

Wind thresholds use the numeric unit reported by the selected sensor; CoverCompass does not guess or convert between wind-speed units.

## Entities and diagnostics

CoverCompass creates a virtual house device plus one device per managed cover. It exposes:

- house automation and dry-run switches;
- active-shading and manual-override counts;
- optional-by-default solar azimuth/elevation diagnostics;
- per-cover automation switch, sun-exposure binary sensor, explainable State sensor, pause/resume buttons, and an optional-by-default solar-angle sensor.

The State attributes include the physical entity, facade and solar angles, exposure and condition results, desired/current position, decision and execution reasons, last automatic command, last rule transition, and override expiry. Download diagnostics from the integration entry for a sanitized snapshot; exact latitude and longitude are redacted.

For debug logging:

```yaml
logger:
  logs:
    custom_components.cover_compass: debug
```

If an entity is renamed or removed, edit the cover definition or restore that entity. CoverCompass raises a Home Assistant Repair only when a reference is absent from both the state machine and entity registry, not for a normal temporary `unavailable` state.

## Known limitations and future UI

- The standard flow configures one time window per cover. The persisted/domain model supports multiple windows for future UI expansion.
- Positionless covers necessarily reduce percentage targets to open/close actions.
- Surrounding buildings, trees, detailed overhang geometry, radiation forecasts, HVAC coordination, presence, and learned behavior are outside this deterministic first release.
- Weather-state confirmation uses the state labels exposed by Home Assistant; it does not call a weather provider.

A future optional frontend can render a top-down virtual house, place stable cover IDs on facades, and display the numeric sun direction and exposure status. The backend already stores numeric orientations and stable IDs, so such a view does not require a schema redesign and is not required for full v1 operation.
