# Supported topology patterns

Hydronicus represents hydraulic relationships explicitly.
A Comfort Zone requests one or more Delivery Routes, each Delivery Route reaches one Hydraulic Circuit, and each Circuit names the valves and pump required for its path.

The examples below use logical names only.
They do not describe a particular home or installation.

## Independent branches

```text
Zone A -> Circuit A -> Valve A -> Pump A
Zone B -> Circuit B -> Valve B -> Pump B
```

This is the simplest topology for independent hydraulic paths.
Demand from Zone A can request Circuit A without making Circuit B a consumer of Valve B or Pump B.

Use independent branches when the hydraulic design actually provides independent isolation and circulation paths.
Two separate Home Assistant entities do not prove that the physical paths are independent.

## Shared pump with independent valves

```text
Zone A -> Circuit A -> Valve A -+
                                +-> Pump shared
Zone B -> Circuit B -> Valve B -+
```

The two Circuits have separate valve paths but the same pump.
The pump is requested while at least one ready Circuit consumes it.
Releasing Zone A must not stop the shared pump while Zone B still requests Circuit B.

This pattern models shared pump ownership.
It does not decide whether the pump has enough capacity, whether the hydraulic balancing is acceptable, or whether simultaneous branches are allowed by the equipment manufacturer.

## Shared valve and pump

```text
Zone A -> Circuit A -+
                      +-> Valve shared -> Pump shared
Zone B -> Circuit B -+
```

Both Circuits consume the same valve and pump.
The shared valve remains requested while either Circuit is an active consumer.

This is a physically coupled delivery group when the shared valve controls the common path.
Separate climate entities can express separate comfort targets, but they cannot create independent flow through a component that is physically common.
Hydronicus emits the non-fatal `shared_valve_limits_independent_control` warning and identifies the affected valve, Circuits, and Zones during configuration review.
The topology-preview entity exposes the warning separately from its compiled logic summary.

Do not model a shared valve as independent per-zone control unless the real hydraulic installation contains another independently controlled path.

## One Zone with multiple Circuits

```text
                         +-> Circuit floor -> Valve floor -> Pump floor
Zone A ------------------+
                         +-> Circuit ceiling -> Valve ceiling -> Pump ceiling
```

One Zone can have more than one Delivery Route.
The current heating evaluator requests every eligible route reached by the Zone.

This pattern is useful when one comfort target can request multiple emitter groups.
It does not imply that the emitters have the same water temperature, response time, or operating limits.
Those physical constraints remain outside the current shadow-only implementation.

## Series valves on one Circuit

```text
Zone A -> Circuit A -> Valve upstream -> Valve downstream -> Pump A
```

A Circuit can require multiple valves.
The Circuit is not virtually ready until all required valves are ready.
The pump request follows valve readiness.

The model describes ordering and ownership.
It does not verify that the physical valve order, flow direction, differential pressure, or end-switch wiring is correct.

## Delivery routes and ownership

The complete current consumer set owns a shared actuator decision.
No individual Zone is allowed to issue an unconditional off decision for a shared actuator.

The relationship can be summarized as:

```text
Zone demand
  -> eligible Delivery Route
    -> Circuit request
      -> valve consumers
        -> valve readiness
          -> pump consumers
```

When one consumer releases demand, the remaining consumers continue to protect the shared actuator request.
When the consumer set becomes empty, the virtual stop sequence can begin.

## Unsupported or unsafe inferences

Hydronicus does not discover hydraulic topology automatically.
It does not infer pipe connections from entity names.
It does not use separate thermostat entities to override physical coupling.
It does not validate pump sizing, valve authority, flow rate, pressure, heat-source capacity, or emitter limits.
It does not provide a custom Lovelace panel or unrestricted user expressions.

If the real plant is coupled in a way the model cannot express clearly, keep Hydronicus in shadow mode and document the limitation before considering any future active-control milestone.
