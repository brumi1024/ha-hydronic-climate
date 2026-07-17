# Safety limits

Hydronicus is a topology and coordination layer inside Home Assistant.
It is not a safety-rated controller.

## Two separate safety layers

Physical protection and software coordination have different responsibilities.

| Layer | What it can do | What it cannot guarantee |
| --- | --- | --- |
| Physical protection | Enforce limits when Home Assistant, the network, or the integration is unavailable | Understand Hydronicus topology or explain a software decision |
| Hydronicus software | Describe topology, calculate shadow demand, sequence virtual requests, and explain the calculation | Prove flow, pressure, temperature, condensation safety, electrical safety, or equipment capacity |

Keep appropriate independent hardware protection in service.
Depending on the plant, this can include high-limit controls, pressure relief, flow protection, freeze protection, condensation protection, pump protection, source interlocks, and emergency isolation.
The correct protection set depends on the equipment design and local requirements.

Never remove or bypass a physical interlock because Hydronicus reports that a route is ready.

## Current release boundary

The current release evaluates heating demand in shadow mode.
It observes selected temperature sensors and calculates virtual valve and pump states.
It does not execute physical valve or pump service calls.

The following are not available as production safety controls in this release:

- Cooling demand control.
- Humidity aggregation and dew-point protection.
- Supply or surface-temperature interlocks.
- Source selection and source changeover.
- Flow confirmation or pump-fault handling.
- Minimum runtime and minimum rest enforcement for physical equipment.
- Safe shutdown commands to physical actuators.

These items are roadmap work and must not be represented as present safety features.

## Shadow mode is not a safety proof

Shadow mode is safe for observing the software decision path because the current runtime does not issue equipment service calls.
It still cannot prove that the configured topology matches the water circuit.

A passing topology validation means that the configured graph is internally consistent.
It does not mean that:

- A valve is installed on the expected pipe.
- A pump can serve all of its consumers.
- A sensor is calibrated or located correctly.
- A source can produce the requested water conditions.
- A circuit is protected against condensation or overheating.
- A hardware interlock will trip when required.

Use synthetic entities first, then shadow observation of real sensors if the staging contract and rollout decision permit it.
Do not use a shadow result to authorize physical control.

## Cooling and condensation

Cooling requires different evidence from heating.
Room temperature alone cannot establish a safe cooling request.

Condensation risk depends on humidity, dew point, supply or surface temperature, sensor freshness, circuit compatibility, and physical protection.
Hydronicus does not provide those production cooling controls in the current release.

Do not operate a real cooling plant from the current integration.
Keep any cooling experiment outside the physical actuator path until the relevant milestone is implemented, tested, staged, and explicitly rolled out.

## Shared equipment

Shared equipment is owned by the complete active-consumer set in the model.
That ownership prevents one virtual Zone release from stopping an actuator still needed by another virtual Circuit.

This software rule does not validate hydraulic balancing or manufacturer limits.
An actuator can be logically shared and still be physically unsuitable for the combined load.

Review shared valves, pumps, sources, and interlocks with the person responsible for the physical installation.

## Safe operating rule

For the current release, the safe operating rule is simple:

1. Use a disposable or isolated Home Assistant instance for initial setup.
2. Use synthetic entities for functional tests.
3. Confirm that the Plant remains in shadow mode.
4. Keep physical protection independent.
5. Stop the test if the topology, sensor state, or explanation is unexpected.
6. Report the issue with redacted diagnostics before changing the physical installation.
