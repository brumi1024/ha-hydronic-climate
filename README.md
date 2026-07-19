<p align="center">
  <img src="custom_components/hydronicus/brand/icon@2x.png" alt="Hydronicus" width="220">
</p>

<h1 align="center">Hydronicus</h1>

<p align="center">
  <strong>One plant. Many zones. No valve fights.</strong>
</p>

Hydronicus is a Home Assistant custom integration for describing hydronic heating and cooling plants as explicit topologies.
It models comfort zones, hydraulic circuits, delivery routes, valves, pumps, and the decisions that connect them.

## Current status

The release target is `0.1.0`.
The current candidate supports end-to-end Dry run behavior and a configurable Plant control boundary.
Anyone can install Hydronicus, configure a Plant through the Home Assistant UI, exercise heating and cooling demand, inspect hydraulic sequencing and source recommendations, and troubleshoot the result without operating equipment.
Release publication and complete disposable staging evidence remain pending.

| Capability | Current candidate | `v0.1.0` release target |
| --- | --- | --- |
| Heating valves and pumps | Proposed in Dry run; controlled when off | Same behavior |
| Cooling and condensation protection | Starts remain Dry run | Starts remain Dry run |
| Source recommendation | Visible in both modes; selection remains Dry run | Same behavior |
| Direct source demand | Proposed in Dry run; controlled when off after a valid pump path | Same behavior |

The codebase contains the tested generic service-call executor needed for heating control.
The Plant UI exposes one Dry run setting, records proposed versus executed operations, and performs an ordered safe shutdown when Dry run is re-enabled.

The current implementation includes:

- HACS custom-repository installation.
- One Home Assistant config entry per Plant.
- A first Plant setup flow for one Comfort Zone and one Hydraulic Circuit.
- Additional Zone, Circuit, and valve Actuator subentries.
- Required and optional temperature sensors with freshness limits and calibration offsets.
- Mean, median, minimum, maximum, designated-reference, and weighted-mean aggregation.
- Comfort, eco, and away preset targets.
- Configurable heating hysteresis plus minimum active and idle durations.
- Multiple zones per circuit and multiple circuits per zone.
- Shared valve and pump modeling with active-consumer tracking.
- Heating demand with hysteresis and virtual valve opening and pump overrun timing.
- Cooling condensation diagnostics and Dry run source recommendations.
- Explicit, idempotent switch and native-valve executor operations behind the Plant Dry run control.
- Dry run climate, demand, aggregate-temperature, blocked-state, actuator-request, topology-preview, and explanation entities.
- Structured non-fatal warnings when shared valves limit independent control.

Home Assistant Repairs for unresolved bindings, redacted downloadable diagnostics, startup reconciliation, and bounded command-failure handling are implemented.
Cooling starts, source changeover, source-demand starts, and physical actuator rollout remain gated milestone work.
Treat the roadmap as a statement of intent rather than authorization to use those paths on physical equipment.

## Installation

Hydronicus is currently installed as a HACS custom repository.
Use a disposable or staging Home Assistant instance for initial evaluation.

1. Open HACS and select **Integrations**.
2. Open the HACS menu and select **Custom repositories**.
3. Add `https://github.com/brumi1024/ha-hydronicus` and choose **Integration** as the repository type.
4. Install **Hydronicus** from HACS.
5. Restart Home Assistant.
6. Open **Settings > Devices & services**, select **Add integration**, and search for **Hydronicus**.

The minimum Home Assistant version declared by this repository is `2026.7.0`.
The integration is not currently part of the HACS default repository list, so the custom-repository step is required.

## First simulated Plant

Use a disposable Home Assistant instance or a staging configuration with synthetic entities.
Do not bind a Dry run test to equipment that must not be observed or controlled by the test.

Before opening the Hydronicus setup flow, prepare these generic entities in Home Assistant:

- One numeric `sensor` entity that can be set below and above a test target.
- One `switch` or `valve` entity to represent a valve.
- One `switch` entity to represent a pump.

Home Assistant Template helpers can provide these entities without connecting physical equipment.
The exact helper or template setup is described in [Home Assistant's Template integration documentation](https://www.home-assistant.io/integrations/template/).
An optional copyable fixture is available at [examples/simulated-entities.yaml](docs/examples/simulated-entities.yaml).

Then complete the Hydronicus flow:

1. Select **Add integration > Hydronicus**.
2. Enter a Plant name.
3. Add the first Comfort Zone, select the synthetic temperature sensor, choose an aggregation policy, and set a target temperature.
4. Add the first Hydraulic Circuit, select the synthetic valve and pump entities, and keep the default timing values for a first test.
5. Review the compiled topology and submit the flow.

The review should describe the route from the Zone to the Circuit, the valve opening step, and the pump request step.
The new Plant starts in Dry run.

To exercise the simulation, change the synthetic sensor below the target minus the heating start threshold.
The Zone demand entity should turn on, the virtual valve should move through opening, and the virtual pump should become requested after the configured opening time.
Raise the synthetic sensor above the stop threshold to release demand.
The virtual pump then follows its configured overrun period before the virtual valve closes.

Changing a climate target changes the calculated demand and the latest proposed operations.
Dry run does not send a command to the configured valve, pump, or direct source-demand entity.
If Dry run is turned off in an isolated test, heating valve and pump operations can execute after the configured confirmation.
Cooling starts and source-selector operations remain proposed and do not execute.

See [configuration and simulation](docs/configuration.md) for a complete generic example and [troubleshooting](docs/troubleshooting.md) if the flow or entities do not behave as expected.

## Dry run boundary

Dry run is a safety boundary, not a physical simulation of water flow, pressure, temperature, or equipment response.
It evaluates the configured graph, reads actuator feedback, and records proposed operations without dispatching them.
It cannot prove that a real valve opens, that a pump produces flow, or that a heat source can deliver safe water.

Do not infer physical safety from a Dry run result.
Turning Dry run off is a tested software control boundary for heating operations, not authorization to operate a physical plant.
Keep real equipment outside the actuator path until the exact staged scope has human approval.

## Supported topology

Hydronicus uses explicit objects and relationships:

- A Plant owns the complete topology and runtime state.
- A Comfort Zone owns a target and its temperature observations.
- A Hydraulic Circuit describes a water path and its required valves and pump.
- A Delivery Route connects one Zone to one Circuit.
- A valve can be required by more than one Circuit.
- A pump can serve more than one Circuit.

Independent branches, shared pumps, shared valves, and one Zone routed to multiple Circuits can be represented.
Sharing a valve or another hydraulically coupled component does not create independent physical control.

Read [how Hydronicus works](docs/how-it-works.md) before mapping an existing plant.

## Safety limits

Hydronicus is software coordination.
It is not a boiler safety controller, pressure-relief system, flow proving device, condensation sensor, high-limit thermostat, or emergency shutdown circuit.

Keep physical protection independent of Home Assistant, including the protections required by the heat source, emitters, water circuit, electrical installation, and local regulations.
Do not use the integration to bypass a hardware interlock or to decide whether equipment is safe to operate.

The software calculates heating, cooling, and source decisions while every new Plant starts in Dry run.
Cooling interlocks, dew-point checks, source selection, and the internal actuator executor are implemented and tested.
The Dry run setting controls heating valves, pumps, and configured direct source demand, while cooling starts and source selectors remain Dry run only.
These are not production safety controls or authorization to operate physical equipment.

Read [safety limits](docs/safety.md) before using any real sensor data.

## Troubleshooting, upgrades, and rollback

Start with [troubleshooting](docs/troubleshooting.md) for setup errors, unavailable sensors, warnings, logs, and recovery.
Use [upgrade and rollback](docs/upgrade-and-rollback.md) for HACS updates, configuration backups, safe reloads, and reverting an installation.

When reporting a problem, use the [diagnostic bug-report template](.github/ISSUE_TEMPLATE/diagnostic-bug-report.md).
Remove credentials, tokens, private addresses, and household-specific entity details before submitting any diagnostic information.

## Documentation

- [How Hydronicus works](docs/how-it-works.md) explains the model, evaluation cycle, shared equipment, and exact control boundary.
- [Configuration and simulation](docs/configuration.md) walks through a complete UI-created Plant.
- [Safety limits](docs/safety.md) separates software coordination from physical protection.
- [Troubleshooting](docs/troubleshooting.md) covers setup, observations, explanations, Repairs, and diagnostics.
- [Upgrade and rollback](docs/upgrade-and-rollback.md) covers backup-first installation changes.
- [Development](docs/development.md) and [staging](docs/home-server-staging.md) are contributor references.

## Development

The deterministic controller core is isolated from Home Assistant imports.
See [the development environment](docs/development.md) for local setup and verification commands.
See [the staging contract](docs/home-server-staging.md) for synthetic and shadow runtime checks.
See [the implementation plan](docs/implementation-plan.md) for the roadmap and milestone boundaries.

Documentation in this repository describes the current public beta where it can be verified.
Active physical control remains outside this public-beta release, even where synthetic execution seams exist for tests.

Contributions are welcome while the project is taking shape.
