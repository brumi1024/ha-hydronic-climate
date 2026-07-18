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

The repository currently contains an early alpha shadow-mode implementation.
The current release is `0.1.0-alpha.1`.

The implementation can create and validate a plant through the Home Assistant UI, observe configured sensors, calculate heating and cooling shadow demand, recommend eligible sources, and expose the virtual sequence that would be needed by the configured topology.
The runtime also contains an explicit actuator executor seam for synthetic and intercepted service-call tests.
New plants remain in shadow mode, and this alpha release is not a production authorization for physical control.

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
- Cooling condensation diagnostics and shadow source recommendations.
- Explicit, idempotent switch and native-valve executor operations behind shadow controls.
- Shadow climate, demand, aggregate-temperature, blocked-state, actuator-request, topology-preview, and explanation entities.
- Structured non-fatal warnings when shared valves limit independent control.

Production cooling control, source changeover, Repairs, downloadable diagnostics, and rollout controls remain milestone work.
Treat the roadmap as a statement of intent rather than a promise that those features are available in this release.

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
Do not bind an alpha shadow-mode test to equipment that must not be observed or controlled by the test.

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
The new Plant remains in shadow mode.

To exercise the simulation, change the synthetic sensor below the target minus the heating start threshold.
The Zone demand entity should turn on, the virtual valve should move through opening, and the virtual pump should become requested after the configured opening time.
Raise the synthetic sensor above the stop threshold to release demand.
The virtual pump then follows its configured overrun period before the virtual valve closes.

Changing a climate target changes the calculated shadow demand only while the Plant remains in its default shadow mode.
It does not send a command to the configured valve or pump entity in that mode.

See [configuration and simulation](docs/configuration.md) for a complete generic example and [troubleshooting](docs/troubleshooting.md) if the flow or entities do not behave as expected.

## Shadow mode boundary

Shadow mode is a safety boundary, not a physical simulation of water flow, pressure, temperature, or equipment response.
It evaluates the configured graph and keeps virtual actuator state in memory.
It cannot prove that a real valve opens, that a pump produces flow, or that a heat source can deliver safe water.

Do not enable or infer physical control from a shadow result.
Physical control is not implemented in the current release.

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

Read [supported topology patterns](docs/topology.md) before mapping an existing plant.

## Safety limits

Hydronicus is software coordination.
It is not a boiler safety controller, pressure-relief system, flow proving device, condensation sensor, high-limit thermostat, or emergency shutdown circuit.

Keep physical protection independent of Home Assistant, including the protections required by the heat source, emitters, water circuit, electrical installation, and local regulations.
Do not use the integration to bypass a hardware interlock or to decide whether equipment is safe to operate.

The software currently calculates heating demand in shadow mode only.
Cooling interlocks, dew-point checks, source selection, and physical actuator execution are not available in this release.

Read [safety limits](docs/safety.md) before using any real sensor data.

## Troubleshooting, upgrades, and rollback

Start with [troubleshooting](docs/troubleshooting.md) for setup errors, unavailable sensors, warnings, logs, and recovery.
Use [upgrade and rollback](docs/upgrade-and-rollback.md) for HACS updates, configuration backups, safe reloads, and reverting an installation.

When reporting a problem, use the [diagnostic bug-report template](.github/ISSUE_TEMPLATE/diagnostic-bug-report.md).
Remove credentials, tokens, private addresses, and household-specific entity details before submitting any diagnostic information.

## Development

The deterministic controller core is isolated from Home Assistant imports.
See [the development environment](docs/development.md) for local setup and verification commands.
See [the staging contract](docs/home-server-staging.md) for synthetic and shadow runtime checks.
See [the implementation plan](docs/implementation-plan.md) for the roadmap and milestone boundaries.

Documentation in this repository describes the current alpha where it can be verified.
Public-beta feature accuracy and final release packaging will continue to be reviewed as Milestones 4 through 7 add active control, safety interlocks, cooling, and source coordination.

Contributions are welcome while the project is taking shape.
