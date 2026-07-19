# How Hydronicus works

Hydronicus turns a Home Assistant configuration into an explicit hydronic Plant model.
It observes sensors, calculates demand, coordinates shared hydraulic paths, and publishes every decision back to Home Assistant.

Every new Plant starts in Dry run.
The Plant UI exposes one setting that can turn Dry run off for heating valves, pumps, and an explicitly configured direct source-demand output.
Cooling starts and automatic source selection remain Dry run only.
Re-enabling Dry run performs an ordered safe shutdown before further commands are suppressed.

## The model

A Plant contains the complete coordinated installation.

- A **Zone** represents a comfort target and its temperature and humidity observations.
- A **Circuit** represents one hydraulic delivery path.
- A **Delivery Route** connects a Zone to a Circuit.
- A **Valve** controls part of a Circuit path.
- A **Pump** circulates water for one or more Circuits.
- A **Source** represents equipment or stored heat that could supply the Plant.

Relationships are stored using generated identifiers rather than display names.
This allows objects to be renamed without changing their logical relationships.

## The evaluation cycle

Hydronicus repeatedly performs the same deterministic cycle:

```text
observe Home Assistant entities
  -> validate freshness and availability
  -> aggregate Zone observations
  -> calculate heating and cooling demand
  -> resolve eligible Delivery Routes
  -> calculate complete actuator consumer sets
  -> sequence valves before pumps
  -> evaluate source eligibility and changeover
  -> publish requests, blocks, and explanations
```

Running the cycle twice with unchanged input does not produce toggle behavior or duplicate commands.

## Heating behavior

Heating demand uses the Zone target, configured hysteresis, minimum active duration, and minimum idle duration.
A required observation that is unknown, unavailable, invalid, or stale blocks the Zone and releases demand immediately.

The virtual hydraulic sequence is:

```text
Zone demand
  -> Circuit requested
  -> required valves requested
  -> valve readiness confirmed by feedback or configured delay
  -> pump requested
  -> source demand permitted only with a valid pump path
```

The codebase contains a generic Home Assistant executor for switch and valve service calls.
It is tested with synthetic and intercepted services.
While Dry run is enabled, the executor records the complete plan as proposed operations and dispatches no service calls.

When Dry run is off, Hydronicus executes heating valves, pumps, and an explicitly configured direct source-demand output.
Turning it off requires one confirmation of the displayed output set.
Turning Dry run back on will perform the ordered safe shutdown before suppressing further commands.

## Cooling behavior

Cooling demand uses Zone temperature, humidity, dew point, supply or surface temperature, sensor freshness, and explicit Circuit cooling compatibility.
It blocks unsafe or incomplete paths and explains condensation and shared-equipment conflicts.

Cooling activation is more restricted than heating execution.
Cooling start operations are explicitly forced into Dry run by the runtime even when heating execution is enabled.
Hydronicus does not start physical cooling equipment in this release.

## Source behavior

Hydronicus can rank available sources using stable priority, freshness, temperature qualification, hysteresis, and dwell rules.
It publishes the recommended source and changeover reasoning.

Source-selector operations are explicitly kept in Dry run by the runtime.
The source recommendation remains visible in both modes.
Direct source-demand output can execute only when Dry run is off and a valid pump path exists.

## Shared equipment

Shared equipment is owned by its complete active-consumer set.
One Zone releasing demand cannot turn off an actuator that another requested Circuit still needs.

### Shared pump with independent valves

```text
Zone A -> Circuit A -> Valve A -+
                                +-> Shared pump
Zone B -> Circuit B -> Valve B -+
```

The pump remains requested until both ready Circuit consumer sets are empty.

### Shared valve and pump

```text
Zone A -> Circuit A -+
                      +-> Shared valve -> Shared pump
Zone B -> Circuit B -+
```

The topology is valid but physically coupled.
Hydronicus warns that separate climate entities cannot create independent flow through the shared valve.

### One Zone with multiple Circuits

```text
                +-> Floor Circuit -> Floor valve -> Floor pump
Zone A ---------+
                +-> Ceiling Circuit -> Ceiling valve -> Ceiling pump
```

Each route is evaluated explicitly.
The model does not infer water temperature, capacity, balancing, or manufacturer limits.

## What Home Assistant exposes

The integration publishes climate targets, aggregate temperatures, heating and cooling demand, blocked states and reasons, virtual valve and pump requests, source recommendations, topology summaries, and decision explanations.

Repairs identify configured entity bindings that are missing or unresolved.
Downloadable diagnostics provide bounded and redacted runtime information for troubleshooting.

## What Dry run proves

Dry run proves that Hydronicus can interpret the configured graph and produce an explainable software decision without dispatching actuator service calls.
It does not prove that the graph matches the pipework, that a valve moves, that a pump produces flow, or that physical safety controls are adequate.

Read [configuration and simulation](configuration.md) to create a Plant and [safety limits](safety.md) before using real sensor observations.
