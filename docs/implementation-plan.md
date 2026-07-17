# Hydronicus implementation plan

Status: Draft

## Purpose

Hydronicus is a standalone Home Assistant custom integration for dynamically configured hydronic heating and cooling plants.
It exposes climate controls for comfort zones while safely coordinating shared hydraulic circuits, valves, pumps, heat sources, and safety interlocks.
The integration is generic and must not contain entity IDs, topology, names, or assumptions belonging to any particular home.

## Product constraints

- The integration lives in its own public GitHub repository.
- The integration is installable through HACS.
- All normal configuration and reconfiguration happens through the Home Assistant UI.
- One Home Assistant config entry represents one hydronic plant.
- A Home Assistant instance may contain multiple independent plants.
- A plant may contain any number of zones, sensors, circuits, actuators, pumps, sources, and interlocks.
- Relationships between plant objects are many-to-many.
- Shared equipment is owned by the central controller, never directly by a zone.
- Heating and cooling are part of the domain model from the beginning.
- New plants start in shadow mode.
- Physical safety remains enforced by appropriate hardware independently of Home Assistant.
- Configuration migrations preserve user topology across releases.
- The project targets current Home Assistant integration conventions and quality requirements.

## Version 1 scope

Version 1 includes:

- Dynamic plant topology.
- Comfort-zone climate entities.
- Multiple temperature and humidity sensors per zone.
- Configurable sensor aggregation.
- Multiple circuits per zone.
- Multiple zones per circuit.
- Shared valves and pumps.
- Binary switch and native valve actuators.
- Heating and cooling demand.
- Dew-point and supply-temperature interlocks.
- Minimum runtime and minimum rest periods.
- Valve pre-opening and pump overrun.
- Heat-source demand and source changeover.
- Shadow mode.
- Human-readable control explanations.
- Repairs and downloadable diagnostics.
- HACS installation and GitHub releases.

Version 1 does not include:

- A custom frontend panel or Lovelace card.
- Automatic hydraulic topology discovery.
- Manufacturer-specific heat-pump protocols.
- Writing manufacturer weather curves.
- Tariff, solar-surplus, or forecast optimization.
- Model-predictive control.
- Automatic hydraulic balancing.
- Arbitrary templates or unrestricted Boolean expressions.
- An internal scheduling system.

Home Assistant automations, schedules, and dashboards may change climate targets without Hydronicus owning scheduling policy.

## Domain model

### Plant

A Plant is one hydraulically coordinated heating and cooling installation.
It owns the operating mode, topology, runtime controller, sources, interlocks, and shadow or active control state.

### Comfort Zone

A Comfort Zone is a group of spaces governed by one comfort target.
It owns target temperatures, supported modes, sensors, aggregation policy, tolerances, minimum demand durations, and presets.
A Comfort Zone never directly switches physical equipment.

### Hydraulic Circuit

A Hydraulic Circuit is an independently describable water path through one emitter group.
Examples include a floor loop group, ceiling loop group, radiator branch, or towel-dryer branch.
It owns emitter type, supported modes, temperature limits, valve path, pump path, and stabilization timings.

### Delivery Route

A Delivery Route connects one Comfort Zone to one Hydraulic Circuit.
This explicit relationship allows one zone to use multiple circuits and multiple zones to share one circuit.
A route owns mode eligibility, priority, arbitration policy, and enable state.

### Actuator

An Actuator is a physical entity that changes the plant.
Initial actuator kinds are binary valve, native Home Assistant valve, pump, source selector, and heat or cool request switch.
Each actuator tracks desired state, observed state, active consumers, transition state, timings, and optional feedback.

### Heat Source

A Heat Source provides usable heating or cooling water.
Initial source kinds are externally controlled source, heat-pump demand output, temperature-qualified buffer, and source behind a selector valve.

### Safety Interlock

A Safety Interlock is a condition that must permit an operation.
Examples include dew-point margin, supply-temperature limits, flow confirmation, pump health, source availability, sensor freshness, and a physical condensation input.

### Coupled Delivery Group

A Coupled Delivery Group contains zones that share equipment in a way that prevents independent hydraulic control.
The integration must identify this topology and explain that separate thermostat entities cannot overcome the physical coupling.

## Architectural seams

The implementation has three primary modules.
The safety-critical behavior belongs behind a small deterministic controller interface.
Home Assistant state observation and service calls remain adapters around that interface.

### Topology compiler

Interface:

```python
def compile_topology(configuration: PlantConfiguration) -> CompiledPlant:
    """Validate and compile a plant configuration."""
```

Responsibilities:

- Resolve UUID references.
- Build an acyclic dependency graph.
- Detect shared actuators.
- Compile demand and permit relationships.
- Detect incompatible modes.
- Detect orphaned objects.
- Produce validation errors and warnings.
- Produce a human-readable logic summary.

The topology compiler does not read Home Assistant states or issue service calls.

### Hydronic controller

Interface:

```python
def evaluate(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    runtime: RuntimeState,
    now: datetime,
) -> Evaluation:
    """Return the next runtime state and desired control plan."""
```

Result:

```python
@dataclass(frozen=True)
class Evaluation:
    next_runtime: RuntimeState
    control_plan: ControlPlan
    diagnostics: ControllerDiagnostics
```

Responsibilities:

- Aggregate zone sensors.
- Calculate heating and cooling demand.
- Apply hysteresis and minimum durations.
- Resolve delivery routes.
- Resolve shared-actuator consumers.
- Apply safety interlocks.
- Advance actuator state machines.
- Detect conflicts.
- Return idempotent desired commands.
- Explain every requested, idle, and blocked decision.

The hydronic controller is deterministic and contains no Home Assistant imports.

### Home Assistant runtime adapter

Responsibilities:

- Observe configured Home Assistant entities.
- Subscribe to relevant state changes.
- Schedule periodic reconciliation.
- Translate Home Assistant states into a PlantSnapshot.
- Call the hydronic controller.
- Execute a ControlPlan.
- Publish Home Assistant entities.
- Raise Repairs issues.
- Generate diagnostics.
- Reload safely after configuration changes.

Tests use a fake runtime adapter across the same seam.

## Topology semantics

Most AND and OR behavior is inferred from topology rather than programmed by the user.

### Circuit demand

```text
circuit request =
    OR of eligible delivery-route demands
```

### Valve demand

```text
valve request =
    OR of requesting circuits that consume the valve
```

A valve turns off only when its active-consumer set is empty.

### Circuit readiness

```text
circuit ready =
    circuit requested
    AND every required valve ready
    AND every applicable safety interlock permits operation
```

### Pump demand

```text
pump request =
    OR of ready circuits served by the pump
    AND every pump permit
```

### Plant demand

```text
plant request =
    OR of running pump paths with active circuit demand
    AND every source permit
```

### Safe arbitration policies

Version 1 supports:

- `any_demand`
- `all_demand`
- `priority`
- `at_least_n`
- `weighted_threshold`

Free-form templates and unrestricted expression trees are not supported.

## Active-consumer ownership

No zone sends an unconditional off command to a shared actuator.
The controller calculates the complete active-consumer set for each actuator during every evaluation.

Example:

```text
underfloor pump consumers:
  - living floor circuit
  - basement floor circuit
```

If the living circuit releases demand, only that consumer is removed.
The pump remains requested while the basement circuit is still a consumer.

## Actuator state machines

### Valve states

```text
CLOSED
OPENING
OPEN
CLOSING
FAULT
UNKNOWN
```

### Pump states

```text
OFF
WAITING_FOR_VALVES
STARTING
RUNNING
OVERRUN
LOCKOUT
FAULT
UNKNOWN
```

### Source states

```text
UNAVAILABLE
IDLE
CHANGEOVER
ACTIVE
MINIMUM_DWELL
FAULT
```

### Start sequence

1. Evaluate zone demand.
2. Resolve eligible delivery routes.
3. Request required valves.
4. Wait for position feedback or configured opening time.
5. Mark eligible circuits ready.
6. Request pumps.
7. Confirm pump power or flow when configured.
8. Assert plant demand.
9. Select or enable the source.

### Stop sequence

1. Release source demand when no plant demand remains.
2. Apply source minimum dwell where required.
3. Apply pump overrun.
4. Stop pumps whose consumer sets are empty.
5. Close valves whose consumer sets are empty.
6. Preserve equipment required by other circuits.

Commands are always explicit `turn_on`, `turn_off`, `open`, or `close` operations.
Toggle commands are forbidden.

## Zone control

### Sensor aggregation

Version 1 supports:

- Designated reference sensor.
- Mean.
- Median.
- Weighted mean.
- Heating-oriented minimum.
- Cooling-oriented maximum.

Each sensor may define a weight, calibration offset, maximum age, and required or optional status.

### Demand calculation

Version 1 uses:

- Separate heating and cooling deadbands.
- Separate start and stop thresholds.
- Minimum active duration.
- Minimum idle duration.
- Demand smoothing.
- Long-cycle behavior suitable for radiant slabs.
- Optional staged emitter activation.

PID and predictive strategies are deferred until there are two proven strategies that justify a strategy seam.

## Cooling safety

Cooling requires:

- Temperature and humidity observations for every required zone.
- A usable supply or surface-temperature reference.
- A configured condensation margin.
- A cooling-compatible circuit.
- Valid source and pump paths.
- No conflicting heating demand on shared equipment.

Cooling fails closed if a required sensor is unavailable or stale.
A physical condensation interlock remains strongly recommended and is independent of software protection.

## Heat-source control

Source selection supports:

- Eligibility checks.
- Priority.
- Required supply temperature.
- Temperature-qualified buffer availability.
- Hysteresis.
- Minimum source dwell.
- Break-before-make changeover.
- Shadow-only source recommendation.
- Deterministic fallback.

Source control is implemented after stable heating control and before production cooling control.

## Home Assistant configuration

One config entry represents one Plant.
Dynamic objects use config subentries where the Home Assistant interface is suitable.

Recommended subentry types:

- `zone`
- `circuit`
- `actuator`
- `source`
- `interlock`

Every topology object receives a generated UUID.
Relationships use UUIDs rather than display names.

### Initial setup flow

1. Create a Plant.
2. Select supported operating modes.
3. Confirm shadow mode.
4. Add the first Comfort Zone.
5. Assign sensors.
6. Add a Hydraulic Circuit.
7. Add Actuators.
8. Connect a Delivery Route.
9. Review the compiled topology.
10. Finish setup.

### Reconfiguration menu

- Plant settings.
- Zones.
- Circuits.
- Actuators.
- Sources.
- Interlocks.
- Topology review.
- Configuration validation.
- Physical-control enablement.
- Safe shutdown.

New Plants start in shadow mode.
Enabling physical control requires a valid topology and explicit confirmation.

## Home Assistant entities

### Zone entities

- One `climate` entity.
- Demand binary sensor.
- Aggregated temperature sensor.
- Aggregated humidity sensor.
- Blocked binary sensor.
- Blocked-reason sensor.
- Active-circuit count sensor.

### Circuit entities

- Requested binary sensor.
- Ready binary sensor.
- Operating-state sensor.
- Active-zone count sensor.
- Supply-temperature sensor when configured.
- Dew-point margin sensor for cooling circuits.

### Actuator entities

- Requested-state binary sensor.
- Observed-state sensor.
- Active-consumer count sensor.
- Transition-state sensor.
- Fault binary sensor.

Detailed actuator diagnostics are disabled by default to reduce Recorder load and UI clutter.

### Plant entities

- Operating-mode select.
- Shadow-mode switch.
- Plant-demand binary sensor.
- Active-source sensor.
- Topology-valid binary sensor.
- Controller-state sensor.
- Safe-shutdown button.

## Repository structure

```text
ha-hydronicus/
├── custom_components/
│   └── hydronicus/
│       ├── __init__.py
│       ├── manifest.json
│       ├── const.py
│       ├── config_flow.py
│       ├── climate.py
│       ├── sensor.py
│       ├── binary_sensor.py
│       ├── select.py
│       ├── switch.py
│       ├── button.py
│       ├── diagnostics.py
│       ├── repairs.py
│       ├── runtime.py
│       ├── entity_adapter.py
│       ├── core/
│       │   ├── model.py
│       │   ├── topology.py
│       │   ├── controller.py
│       │   ├── safety.py
│       │   └── explanations.py
│       └── translations/
│           └── en.json
├── tests/
│   ├── core/
│   ├── integration/
│   ├── scenarios/
│   └── conftest.py
├── docs/
│   ├── concepts.md
│   ├── configuration.md
│   ├── safety.md
│   ├── troubleshooting.md
│   └── examples/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   └── workflows/
├── hacs.json
├── pyproject.toml
├── README.md
├── CONTRIBUTING.md
└── LICENSE
```

## Implementation milestones

### Milestone 0: Repository and quality foundation

Deliver:

- Integration manifest.
- HACS metadata.
- Development environment.
- Test harness.
- Formatting and typing configuration.
- HACS and Hassfest workflows.
- Release workflow.
- Contribution and security documentation.

Acceptance criteria:

- The repository installs through HACS as a custom repository.
- The integration appears in Add Integration.
- An empty Plant can be added, reloaded, and removed.
- HACS and Hassfest validation pass.

### Milestone 1: Shadow-mode vertical slice

Deliver:

- Plant configuration.
- One zone.
- One temperature sensor.
- One circuit.
- One valve.
- One pump.
- Pure controller evaluation.
- Shadow-mode entities and explanations.
- No physical service calls.

Acceptance criteria:

- Temperature below target produces the expected virtual sequence.
- The valve becomes virtually ready before pump request.
- Removing demand produces pump overrun and valve closure.
- No real Home Assistant entity name appears in source or tests outside generated fixtures.

Release target: `v0.1.0-alpha.1`

### Milestone 2: Dynamic topology

Deliver:

- Config subentries.
- Multiple sensors per zone.
- Multiple zones and circuits.
- Shared valve and pump resolution.
- Delivery-route arbitration.
- UUID relationships.
- Graph validation.
- Human-readable topology preview.

Acceptance criteria:

- Two zones with independent valves can share one pump.
- Two zones can share one valve and pump.
- One zone can use floor and ceiling circuits.
- One valve can be shared across circuits.
- One circuit can require multiple series valves.
- Removing one consumer never stops a shared actuator.
- Circular and orphaned graphs are rejected.

Release target: `v0.1.0-alpha.2`

### Milestone 3: Zone climate entities

Deliver:

- Climate entities.
- Presets.
- Sensor aggregation policies.
- Hysteresis.
- Minimum demand and idle times.
- Stale-sensor handling.
- Active and blocked explanations.

Acceptance criteria:

- Every aggregation policy has deterministic tests.
- A failed optional sensor is excluded.
- A failed required sensor blocks the zone.
- Setpoint changes recalculate shadow demand.
- Shared-valve limitations appear as configuration warnings.

#### Milestone 3 implementation status

Baseline snapshot: 2026-07-17 at commit `14158cf`, after the integration rename to Hydronicus.
The canonical integration directory is `custom_components/hydronicus`, the integration domain is `hydronicus`, and new implementation or staging work must not recreate the former `hydronic_climate` package or domain.

The baseline quality gate is green.
`make verify` passes 100 tests with 93.70 percent branch coverage for `custom_components/hydronicus/core`.
This baseline must remain green throughout the milestone.

Completion coverage:

| Deliverable or criterion | Status | Evidence |
| --- | --- | --- |
| Climate entities | Implemented | `climate.py` publishes one target-temperature climate entity per configured zone and integration tests cover setup, unload, and target changes. |
| Presets | Implemented | The climate entity exposes configured comfort, eco, and away presets; preset and manual target changes persist and reevaluate shadow demand. |
| Sensor aggregation policies | Implemented | Designated reference, mean, median, minimum, maximum, and weighted mean use first-class editable sensor metadata in the pure controller. |
| Hysteresis | Implemented | Separate heating start and stop deltas are persisted through setup and reconfiguration and applied by the controller. |
| Minimum demand and idle times | Implemented | Immutable Zone runtime state records the last demand transition and enforces minimum-active and minimum-idle deadlines. |
| Stale-sensor handling | Implemented | The controller applies per-observation maximum ages and the runtime schedules reevaluation at freshness deadlines. |
| Active and blocked explanations | Implemented | Structured Zone decisions drive aggregate-temperature, blocked-state, blocked-reason, demand, and explanation entities. |
| Every aggregation policy has deterministic tests | Implemented | Core tests cover every policy, calibrated values, ordering independence, and weighted metadata; integration tests cover the editable metadata path. |
| Failed optional sensor is excluded | Implemented | Core, property, and named-scenario tests prove optional degradation is reported without blocking while a usable observation remains. |
| Failed required sensor blocks the zone | Implemented | Core and Home Assistant adapter tests prove unavailable, invalid, or stale required observations block and release demand immediately. |
| Setpoint changes recalculate shadow demand | Implemented | The climate service persists the target and immediately refreshes the shadow evaluation. |
| Shared-valve limitations appear as configuration warnings | Implemented | Topology compilation produces a stable non-fatal warning that configuration review presents and topology-preview attributes publish separately from logic prose. |

#### Milestone 3 scope decisions

Milestone 3 remains heating-only and shadow-only.
It must not issue Home Assistant service calls to valves, pumps, or heat sources.
Cooling, humidity aggregation, condensation protection, physical actuator execution, and source selection remain owned by later milestones.
Demand smoothing for this milestone consists of hysteresis plus minimum active and minimum idle durations.
Additional smoothing strategies and staged emitter activation remain deferred until they justify a separate deterministic strategy seam.

The pure controller remains the owner of aggregation, freshness decisions, demand transitions, duration enforcement, and explanations.
The Home Assistant adapter owns entity observation, persistence, scheduled reevaluation, service handling for the climate entity, and entity publication.
No Home Assistant import is permitted under `custom_components/hydronicus/core`.

#### Milestone 3 core contract

The contract must be settled and tested before Home Assistant adapter work proceeds in parallel.

Temperature sensor configuration must become a first-class immutable value object rather than a collection of parallel maps.
Each configured temperature observation must contain:

- Home Assistant entity ID.
- Required or optional status, defaulting legacy sensors to required.
- Positive finite aggregation weight, defaulting to `1.0`.
- Finite calibration offset in degrees Celsius, defaulting to `0.0`.
- Positive finite maximum age in seconds, defaulting legacy sensors to `1800` seconds.
- Designated-reference status when the zone uses reference aggregation.

Every zone must contain at least one temperature sensor.
An all-optional sensor set is valid, but the zone blocks whenever none of those observations is usable.
Exactly one configured sensor must be designated when the selected aggregation policy is designated reference.
Unknown metadata keys, duplicate entity IDs, invalid weights, invalid maximum ages, multiple reference sensors, and a reference outside the configured sensor set must fail topology validation.
Legacy `temperature_sensor`, `temperature_sensors`, and `temperature_sensor_weights` data must decode with required status, zero calibration, existing weights, and the `1800` second maximum-age default.
Freshness enforcement is an intentional Milestone 3 behavior change for legacy configurations and must be visible in release notes and explanations.

Aggregation must produce a structured result containing the aggregate value, usable sensor IDs, excluded optional sensor IDs, blocking required sensor IDs, and an explanation.
Calibration offsets are applied before aggregation.
An observation is unusable when it is missing, non-finite, lacks a usable observation timestamp, or is older than its configured maximum age at evaluation time.
An unusable optional sensor is excluded from the aggregate and appears in diagnostics.
An unusable required sensor blocks the zone immediately.
If no usable readings remain, the zone is blocked even when every failed sensor is optional.
A required-sensor block overrides minimum active duration and releases zone demand immediately because fail-closed safety takes precedence over comfort timing.

The supported aggregation policies are designated reference, mean, median, weighted mean, heating-oriented minimum, and cooling-oriented maximum.
Every policy must have deterministic tests for ordering independence and calibrated values.
Weighted mean must not be offered in the Home Assistant UI until every selected sensor has a complete editable weight path.

Zone demand runtime must use a dedicated immutable zone runtime record containing at least the current demand state and the time of the last demand-state transition.
When an active zone reaches its stop threshold before the minimum active deadline, demand remains active and the explanation reports the hold deadline.
When an idle zone reaches its start threshold before the minimum idle deadline, demand remains idle and the explanation reports the lockout deadline.
Setpoint and preset changes trigger immediate reevaluation but do not bypass a remaining minimum active or minimum idle duration.
Zero-duration defaults preserve the behavior of existing configurations.
Restored runtime state without a trustworthy transition timestamp must apply the conservative behavior defined by the controller tests and must not silently assume that a duration has elapsed.

Controller diagnostics must distinguish requested, satisfied, duration-held, duration-locked, and sensor-blocked zone decisions.
Existing human-readable reason strings may remain for entity display, but structured status must be available so adapter entities never infer safety state by parsing prose.

Topology compilation must expose non-fatal structured warnings separately from validation errors and the human-readable logic summary.
The initial warning code is `shared_valve_limits_independent_control`.
It is emitted when one valve belongs to more than one circuit and identifies the affected valve, circuits, and zones.
A warning must never reject an otherwise valid topology.

#### Preset contract

Milestone 3 supports the standard Home Assistant `comfort`, `eco`, and `away` preset names.
Each zone may configure a finite target temperature for any of these presets.
Only configured presets appear in `preset_modes`.
The climate entity advertises `ClimateEntityFeature.PRESET_MODE` only when at least one preset is configured.

Selecting a preset persists the selected preset and its target temperature, then immediately reevaluates shadow demand.
Setting a target temperature manually clears the active preset to `none` before reevaluation.
Reconfiguring or removing the active preset clears the preset selection while preserving the current target temperature.
Reload reconstructs both the current target and active preset deterministically.
Preset targets use the same temperature bounds and finite-number validation as manual targets.

#### Home Assistant configuration and entity work

Zone setup and reconfiguration must expose required versus optional sensors, maximum age, calibration, aggregation policy, designated reference, weights when applicable, heating start and stop deltas, minimum active duration, minimum idle duration, and preset targets.
If the Home Assistant selector framework cannot express per-sensor metadata clearly in one form, use a small multi-step zone flow instead of encoding metadata into free-form strings.
Reconfiguration must remain atomic and must preserve the zone UUID and retained delivery-route UUIDs.

Milestone 3 completes these heating-zone entities:

- Climate entity with target-temperature and optional preset support.
- Demand binary sensor.
- Aggregated temperature sensor.
- Blocked binary sensor.
- Blocked-reason sensor.
- Existing human-readable explanation sensor.

The aggregated humidity sensor remains deferred to Milestone 6.
The active-circuit count sensor may be added in Milestone 3 if it can be derived without changing the controller contract, but it is not a Milestone 3 completion gate.

The runtime scheduler must wake the controller at the earliest valve deadline, pump-overrun deadline, zone minimum-duration deadline, or sensor-staleness deadline.
Staleness must therefore be detected even when Home Assistant emits no new state event.
Listener and timer replacement must remain idempotent across refresh, reload, unload, and stop.

Configuration review and reconfiguration confirmation steps must show structured topology warnings before saving.
The topology preview entity must expose warnings in a separate attribute rather than mixing them into `logic_summary`.
Warning text must explain that separate climate entities cannot independently control circuits coupled by the same physical valve.

#### Parallel implementation sequence

Only one writer may modify the core contract during Gate 0.
After Gate 0 passes, the remaining chunks may proceed in parallel with the file ownership below.
Agents are not allowed to edit files owned by another active chunk or revert another agent's work.

Gate 0, core contract freeze:

- Owner: primary implementation agent.
- Files: `custom_components/hydronicus/core/model.py` plus the smallest compile-safe signature updates required across current callers and tests.
- Deliverable: immutable sensor metadata, structured aggregation and decision results, zone runtime timing state, structured topology warnings, legacy defaults, documented safety precedence, and a green compatibility baseline.
- Constraint: Gate 0 may touch files later owned by Chunks A, B, and C only to establish the shared public contract, and it must stop changing those files once parallel ownership begins.
- Gate: `make verify` passes before parallel writers start.

Chunk A, deterministic controller and topology:

- Files: `custom_components/hydronicus/core/controller.py`, `custom_components/hydronicus/core/topology.py`, `tests/core/test_controller.py`, `tests/core/test_topology.py`, `tests/core/test_properties.py`, `tests/scenarios/harness.py`, and `tests/scenarios/test_operating_scenarios.py`.
- Deliverable: all aggregation policies, optional exclusion, required blocking, calibration, staleness, hysteresis, minimum active and idle behavior, structured warnings, and named time-ordered scenarios.
- Narrow gates: `make test-core`, `make test-scenarios`, and `make typecheck`.

Chunk B, persistence and configuration flows:

- Files: `custom_components/hydronicus/core/configuration.py`, `custom_components/hydronicus/entry_configuration.py`, `custom_components/hydronicus/config_flow.py`, `custom_components/hydronicus/const.py`, `custom_components/hydronicus/strings.json`, `custom_components/hydronicus/translations/en.json`, `tests/core/test_configuration.py`, and `tests/integration/test_config_flow.py`.
- Deliverable: legacy-safe decoding, complete zone persistence, atomic setup and reconfiguration flows, preset fields, per-sensor metadata editing, and warning presentation.
- Narrow gates: `uv run pytest tests/core/test_configuration.py` and `uv run pytest tests/integration/test_config_flow.py`.

Chunk C, runtime and zone entities:

- Files: `custom_components/hydronicus/runtime.py`, `custom_components/hydronicus/climate.py`, `custom_components/hydronicus/sensor.py`, `custom_components/hydronicus/binary_sensor.py`, `tests/test_runtime.py`, `tests/integration/test_init.py`, and `tests/integration/test_zone_subentry.py`.
- Deliverable: deadline scheduling, preset behavior, structured blocked and explanation entities, aggregated temperature publication, setpoint recalculation, reload reconstruction, and unload cleanup.
- Narrow gates: `uv run pytest tests/test_runtime.py tests/integration/test_init.py tests/integration/test_zone_subentry.py`.

Integration and review are owned by the primary implementation agent after all three chunks report completion.
The primary agent resolves contract mismatches, reviews the combined diff, and runs `make lint`, `make format-check`, `make typecheck`, `make test-core`, `make test-integration`, `make test-scenarios`, and `make verify`.
No chunk may be declared complete while its selected acceptance evidence is missing or failing.

#### Required Milestone 3 test evidence

Pure controller tests must prove:

- Every aggregation policy, including designated reference, is deterministic.
- Calibration is applied before aggregation.
- Invalid or stale optional sensors are excluded and reported.
- Invalid or stale required sensors block immediately.
- Required-sensor blocking overrides minimum active duration.
- Minimum active and minimum idle deadlines hold the correct demand state.
- Hysteresis and duration behavior remain deterministic across repeated evaluation.
- Structured shared-valve warnings are stable and non-fatal.

Property tests must prove evaluation determinism and unchanged-snapshot idempotence across generated sensor metadata and timing combinations.
Property tests must also prove that a blocked required sensor never produces zone demand.

Home Assistant adapter tests must prove:

- Initial setup, zone add, reconfigure, reload, and delete preserve the new fields and UUID relationships.
- Manual setpoint and preset changes persist and trigger immediate evaluation.
- Staleness and duration deadlines schedule reevaluation without a state event.
- Climate, demand, aggregate temperature, blocked, blocked-reason, and explanation entities update together from one evaluation.
- Config flow warnings appear for valid shared-valve topologies without turning them into errors.
- Unload and stop cancel every listener and timer.

Named scenarios must include `zone_sensor_becomes_stale`, an optional-sensor degradation case, a minimum-active hold followed by release, and a minimum-idle lockout followed by demand.
All scenario time advances use the fake clock.

#### Development Home Assistant staging gate

Local tests and CI remain the first two evidence layers.
The disposable development Home Assistant instance is the final UI and runtime evidence layer and must remain isolated from production Home Assistant and physical control.
Its address, credentials, deployment path, restart command, and log command remain in the site-specific local handoff and must not be committed to this repository.

The development instance may still contain a pre-rename Hydronic Climate installation and config entries.
The implementing agent must treat that state as legacy test data, deploy the renamed integration only as `config/custom_components/hydronicus`, and never recreate `custom_components/hydronic_climate`.
Existing parent test plants must not be deleted or mutated destructively without explicit user approval.
If the old domain prevents a clean Hydronicus setup, stop and request approval before uninstalling the old integration or deleting its config entries.

Before deployment, run `make verify` and record the commit SHA.
Deploy only to the disposable development instance in synthetic or shadow mode.
Use synthetic temperature sensors for required, optional, stale, calibrated, and weighted observations.
Do not bind the Milestone 3 plant to physical valve or pump entities when synthetic helpers can satisfy the topology.

The staging pass must verify through the Home Assistant UI:

1. Hydronicus loads under domain `hydronicus` with no manifest, import, config-flow, or translation error.
2. A synthetic plant can be created, reconfigured, reloaded, and removed without affecting retained legacy test plants.
3. Every configured aggregation policy produces the expected visible temperature.
4. An unavailable optional sensor is excluded and explained.
5. An unavailable or stale required sensor sets the blocked entities and releases demand immediately.
6. Manual target and preset changes recalculate visible shadow demand.
7. Minimum active and idle deadlines change explanations at the expected time without requiring a new sensor event.
8. A shared valve produces a visible non-fatal configuration warning.
9. Home Assistant logs contain no unexpected exception or repeated warning.
10. No physical entity changes state and Hydronicus issues no physical service call.

Record the Home Assistant version, Hydronicus commit SHA, scenario names, visible results, and only the minimal redacted log excerpts needed to explain failures.
Staging evidence from the former Hydronic Climate package or domain does not satisfy the Milestone 3 gate after the rename.

Release target: `v0.1.0-beta.1`

### Milestone 4: Heating actuator execution

Deliver:

- Switch and valve adapters.
- Idempotent command executor.
- Valve readiness timers.
- Optional position, power, flow, and fault feedback.
- Pump preconditions and overrun.
- Manual-state mismatch detection.
- Global and per-actuator shadow mode.
- Safe-shutdown action.

Acceptance criteria:

- A pump is never requested without a ready circuit.
- A shared actuator never turns off while it has a consumer.
- A source request is never asserted without a valid pump path.
- A faulted pump blocks dependent circuits.
- Repeated evaluation never produces toggle behavior.
- Integration reload reconstructs a conservative state.

Release target: `v0.2.0-beta.1`

### Milestone 5: Operational hardening

Deliver:

- Startup reconciliation.
- Home Assistant stop and unload handling.
- Command timeout handling.
- Repairs issues.
- Downloadable diagnostics.
- Sensitive-data redaction.
- Configuration migrations.
- Event throttling and periodic reconciliation.
- Recorder-friendly diagnostics.

Acceptance criteria:

- Restart during every actuator transition is tested.
- Missing and renamed entities produce actionable Repairs issues.
- Migrations preserve UUID relationships.
- Diagnostics contain no secrets.
- The controller recovers from delayed and failed service calls.

Release target: `v0.2.0`

### Milestone 6: Cooling and condensation protection

Deliver:

- Cooling demand.
- Humidity aggregation.
- Dew-point calculation.
- Supply and surface-temperature references.
- Configurable condensation margin.
- Cooling-specific circuit compatibility.
- Mode-change lockout.
- Cooling interlock explanations.

Acceptance criteria:

- Cooling is blocked if a required sensor is unavailable or stale.
- Cooling is blocked when the margin is below its configured threshold.
- Shared equipment cannot receive simultaneous heating and cooling requests.
- Mode change waits for a safe idle state.
- Cooling must be explicitly enabled per circuit.

Release target: `v0.3.0-beta.1`

### Milestone 7: Heat sources and changeover

Deliver:

- Source availability.
- Source priority.
- Temperature-qualified buffer source.
- Source-selection actuator.
- Minimum dwell and hysteresis.
- Break-before-make sequencing.
- Shadow source recommendation.
- Heat-pump demand output.

Acceptance criteria:

- A source change cannot occur during an unsafe hydraulic transition.
- Stale buffer temperature makes the buffer ineligible.
- Fallback source selection is deterministic.
- Temperature changes cannot chatter the selector.
- Recommendation can run without execution.

Release target: `v0.4.0-beta.1`

### Milestone 8: Public beta

Deliver:

- Complete README.
- Configuration examples.
- Hydraulic topology examples.
- Safety limitations.
- Troubleshooting guide.
- English translations.
- Diagnostic bug-report template.
- Upgrade and rollback instructions.
- Packaged GitHub Releases.

Acceptance criteria:

- A new user can install and create a simulated Plant without repository knowledge.
- Documentation contains no household-specific assumptions.
- Fresh install and upgrade paths are tested.
- HACS and Hassfest workflows pass without ignored checks.
- At least one non-author installation completes setup.

Release target: `v0.5.0`

### Milestone 9: Stable release

Release requirements:

- Heating pilot completed without unresolved safety faults.
- Cooling shadow pilot completed through representative humidity conditions.
- Source-switching shadow data reviewed.
- No open critical topology or actuator bugs.
- Migrations tested from every public beta.
- Performance measured with a large synthetic plant.
- External feedback incorporated.
- Home Assistant Integration Quality Scale Bronze expectations substantially met.

Release target: `v1.0.0`

HACS default inclusion should be requested only after the stable release and independent use.

## Test strategy

### Pure controller tests

- Demand thresholds.
- Sensor aggregation.
- Active-consumer ownership.
- Valve sequencing.
- Pump sequencing.
- Source sequencing.
- Minimum runtime.
- Lockout.
- Cooling interlocks.
- Conflict arbitration.
- Explanations.
- Restart reconstruction.

All time-sensitive tests use a fake clock.

### Property-based invariants

Generated topologies must prove:

- No actuator stops while it has active consumers.
- Pumps cannot run without ready downstream circuits.
- Sources cannot run without permitted hydraulic demand.
- Cooling cannot run through a blocked condensation interlock.
- Cyclic graphs are rejected.
- Evaluation is deterministic.
- Re-evaluating an unchanged snapshot produces no new command.

### Home Assistant adapter tests

- Config flow.
- Every subentry flow.
- Reconfigure and delete.
- Entry setup, unload, and reload.
- Entity creation and unique IDs.
- Referenced entity removal.
- Service-call translation.
- Diagnostics redaction.
- Repairs creation and removal.
- Translation loading.
- Config migration.

### Named operating scenarios

- `two_zones_release_shared_pump_independently`
- `coupled_zones_share_one_valve`
- `living_zone_stages_floor_and_ceiling`
- `pump_fault_blocks_only_dependent_circuits`
- `cooling_stops_before_condensation_margin_is_crossed`
- `buffer_becomes_ineligible_during_active_heating`
- `restart_while_valve_is_opening`
- `manual_pump_override_is_detected`
- `zone_sensor_becomes_stale`
- `heat_to_cool_changeover_waits_for_safe_idle`

## Pilot rollout

### Stage 1: Synthetic

Use fake sensors and input booleans only.

### Stage 2: Shadow

Use real sensors and observed equipment without service calls.

### Stage 3: Partial heating control

Control one heating circuit while the heat source remains independently controlled.
Provide immediate manual rollback.

### Stage 4: Full heating control

Control all heating circuits and shared pumps.
Keep cooling and source switching in shadow mode.

Cooling and source switching each repeat the staged rollout independently.

## Release and HACS policy

- Use semantic versioning.
- Publish GitHub Releases rather than tags alone.
- Run HACS validation and Hassfest on pushes, pull requests, and scheduled checks.
- Keep the integration under one `custom_components/hydronicus` directory.
- Keep all runtime files inside the integration directory.
- Include root-level `hacs.json`.
- Include `domain`, `documentation`, `issue_tracker`, `codeowners`, `name`, and `version` in `manifest.json`.
- Include complete English custom-integration translations in `translations/en.json`.
- Select the minimum supported Home Assistant version after the config-subentry prototype identifies the earliest version covered by CI.
- Do not submit for HACS default inclusion before a stable release and independent use.

## Initial architectural decisions

- Repository name: `ha-hydronicus`.
- Integration domain: `hydronicus`.
- License: MIT.
- Implementation language: Python.
- Runtime style: asynchronous.
- Configuration: Home Assistant config entries and subentries.
- New Plant control mode: shadow.
- Initial control algorithm: hysteresis and long-cycle demand.
- User logic: fixed validated arbitration policies.
- Zone interface: Home Assistant climate entity.
- Plant mode interface: plant-level mode selector.
- Source integration: generic entity adapters before vendor-specific support.

## Open design questions

These questions should be resolved through prototypes before their related milestone begins:

1. Whether Delivery Routes need their own config subentry or remain relationships owned by zones and circuits.
2. How a plant-level operating mode constrains zone climate modes in the clearest Home Assistant UI.
3. How entity references survive user renames while remaining compatible with normal Home Assistant selectors.
4. Which observed feedback qualifies an actuator as ready when several feedback signals are available.
5. How best-effort safe shutdown behaves during Home Assistant shutdown when service calls may no longer complete.
6. Whether actuator manual override should reassert control, suspend the dependent route, or be user-configurable.
7. Which diagnostic entities should be enabled by default without producing Recorder noise.

## Definition of done

The integration is complete when a user can install it through HACS, create an arbitrary hydronic topology through the UI, understand the compiled control logic, validate it in shadow mode, and safely enable heating, cooling, and source coordination without editing YAML or source code.
Every shared actuator must remain controlled by the complete current consumer set.
Every safety decision must be visible and explainable.
Every safety-critical invariant must be covered by automated tests.
