# Safety limits

Hydronicus is a topology and coordination layer inside Home Assistant.
It is not a safety-rated controller.

## Two separate safety layers

Physical protection and software coordination have different responsibilities.

| Layer | What it can do | What it cannot guarantee |
| --- | --- | --- |
| Physical protection | Enforce limits when Home Assistant, the network, or the integration is unavailable | Understand Hydronicus topology or explain a software decision |
| Hydronicus software | Describe topology, calculate demand, sequence requests, explain the calculation, and record or dispatch explicit commands behind the Dry run boundary | Prove flow, pressure, temperature, condensation safety, electrical safety, or equipment capacity |

Keep appropriate independent hardware protection in service.
Depending on the plant, this can include high-limit controls, pressure relief, flow protection, freeze protection, condensation protection, pump protection, source interlocks, and emergency isolation.
The correct protection set depends on the equipment design and local requirements.

Never remove or bypass a physical interlock because Hydronicus reports that a route is ready.

## Current release boundary

Every new Plant starts in Dry run.
The Home Assistant UI exposes one Plant-level setting for changing that boundary.

Dry run can be turned off for heating valves, pumps, and an optional direct source-demand output.
Dry run remains the default, and cooling starts and automatic source selection remain Dry run only.

The release calculates and publishes:

- Heating and cooling demand.
- Required and optional observation handling.
- Humidity aggregation, dew point, and condensation margins.
- Supply or surface-temperature interlocks.
- Valve readiness, pump sequencing, and pump overrun.
- Minimum active and idle durations.
- Actuator feedback and mismatch diagnostics.
- Source eligibility, recommendation, demand permission, and changeover reasoning.
- Safe-shutdown plans, Repairs, and redacted diagnostics.

The codebase also contains a generic actuator executor and safe-shutdown dispatcher tested with synthetic and intercepted Home Assistant services.
The executor records proposed operations in Dry run and dispatches only the allowed heating operations when Dry run is off.
Cooling starts and source-selector operations are forcibly kept in Dry run by the runtime.
Direct source-demand output requires a valid running pump path.
Changing Dry run back on performs the ordered safe shutdown before further commands are suppressed.

## Dry run is not a safety proof

Dry run is safe for observing the software decision path because the runtime does not issue equipment service calls while it is enabled.
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
Do not use a Dry run result to authorize physical control.

## Cooling and condensation

Cooling requires different evidence from heating.
Room temperature alone cannot establish a safe cooling request.

Condensation risk depends on humidity, dew point, supply or surface temperature, sensor freshness, circuit compatibility, and physical protection.
Hydronicus calculates these conditions and exposes the resulting shadow decisions, but it does not start physical cooling equipment in the current release.

Do not operate a real cooling plant from the current integration.
Keep any cooling experiment outside the physical actuator path until a later release explicitly supports, tests, stages, and documents activation.

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
3. Confirm that requests and explanations change while the bound actuator entities do not.
4. Keep physical protection independent.
5. Stop the test if the topology, sensor state, or explanation is unexpected.
6. Report the issue with redacted diagnostics before changing the physical installation.
