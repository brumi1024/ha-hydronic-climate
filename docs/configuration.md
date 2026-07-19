# Configuration and simulation

This guide describes a generic first Plant and the topology choices currently represented by Hydronicus.
It uses synthetic Home Assistant entities so that the test cannot operate real equipment.

## Prepare synthetic entities

Create the test entities in a disposable Home Assistant instance or a separate staging configuration.
Do not use a production valve or pump entity for a first test.

Hydronicus currently needs these entity types:

| Purpose | Accepted entity type | Example name |
| --- | --- | --- |
| Zone temperature | Numeric `sensor` | `sensor.simulated_zone_temperature` |
| Valve actuator | `switch` or `valve` | `switch.simulated_zone_valve` |
| Pump actuator | `switch` | `switch.simulated_zone_pump` |

The example names are placeholders for a disposable test only.
Use the entity picker in the flow rather than copying these names into a production configuration.

Home Assistant Template helpers can create sensors and switches through the UI.
Manual YAML is also supported by Home Assistant's Template integration.
See the [Home Assistant Template documentation](https://www.home-assistant.io/integrations/template/) for the current helper and YAML syntax.
For a disposable fixture, copy [examples/simulated-entities.yaml](examples/simulated-entities.yaml) into the test configuration and restart or reload the relevant helpers.

The actuator entities are observed by the current release.
Every Plant created through the UI starts in Dry run, so Hydronicus does not switch them until the Plant setting is deliberately changed.

## Create the first Plant

Open **Settings > Devices & services > Add integration**, search for **Hydronicus**, and enter a Plant name.
Leave **Dry run** enabled for the first test.
The flow stores the Plant with Dry run enabled by default.

### Add a Comfort Zone

Give the Zone a descriptive generic name such as `Simulated zone`.
Select one or more numeric temperature sensors.
Selected sensors are required by default.
Enable detailed sensor editing to configure each observation as required or optional and set its calibration offset, maximum age, aggregation weight, or designated-reference status.
An unusable required sensor blocks the Zone immediately.
An unusable optional sensor is excluded and reported, but the Zone still blocks if no usable observation remains.
Select one of the available policies:

- **Mean** calculates the arithmetic mean.
- **Median** selects the middle value after sorting the readings.
- **Heating-oriented minimum** uses the lowest reading.
- **Cooling-oriented maximum** supports cooling shadow evaluation, but physical cooling starts are not supported or authorized in this release.
- **Designated reference** uses the one observation marked as the reference.
- **Weighted mean** applies the positive weights configured through detailed sensor editing.

Designated-reference and weighted-mean policies become available after completing the detailed sensor editor because they depend on per-sensor metadata.

Set a target temperature.
Configure the heating start and stop deltas to define the hysteresis band around that target.
Minimum active duration holds an already-requested Zone until its deadline unless a required sensor blocks it.
Minimum idle duration prevents a satisfied Zone from requesting heat again until its deadline.
Optional comfort, eco, and away target fields expose the corresponding Home Assistant climate presets.
Changing the target or preset reevaluates demand immediately without bypassing a remaining duration deadline.

### Add a Hydraulic Circuit

Give the Circuit a generic name such as `Simulated circuit`.
Select the synthetic valve and pump entities.
Keep the opening and overrun timings long enough to observe the virtual sequence.

The initial flow creates one valve, one pump, one Circuit, and one Delivery Route from the first Zone to that Circuit.
The review page shows the compiled relationship and the expected valve-before-pump ordering.
Submit the flow only after confirming that the topology is the synthetic one you intended to test.

## Observe the result

After setup, Hydronicus exposes entities associated with the Plant.
The exact entity IDs depend on the Plant and Zone names chosen in Home Assistant.

The useful states for a first simulation are:

- The Zone climate entity, which reports the aggregate current temperature and target.
- The Zone demand binary sensor, which reports the calculated virtual heat demand.
- The aggregate-temperature sensor, which identifies usable and excluded observations in its attributes.
- The blocked binary sensor and blocked-reason sensor, which expose fail-closed sensor decisions without parsing prose.
- The valve requested and pump requested binary sensors, which report virtual requests.
- The topology preview sensor, which reports object counts and exposes compiled logic and structured warnings as separate attributes.
- The Zone explanation sensor, which reports why demand is requested, idle, or blocked.

Change the synthetic temperature below the target and wait for the configured virtual valve opening time.
The virtual sequence is:

```text
zone demand -> circuit request -> valve opening -> valve ready -> pump requested
```

Raise the synthetic temperature above the stop threshold.
The pump enters virtual overrun before it becomes idle, and the valve closes after the pump no longer needs protection.

No physical service call is dispatched while Dry run remains enabled.

Cooling demand, condensation blocking, source recommendations, and source changeover reasoning are also visible in Dry run when their required objects and observations are configured.
Cooling starts and source-selector operations remain Dry run only.
When Dry run is off, heating valves, pumps, and a configured direct source-demand output can execute after the required confirmation and pump-path checks.

## Add more objects

After the first Plant exists, use the config entry's subentry controls to add more objects.

- Add a **Circuit** to connect existing Zones, valves, and pumps.
- Add a **Zone** to select one or more existing Circuits and temperature sensors.
- Add an **Actuator** to add a valve to one or more existing Circuits.

The current Actuator subentry represents a valve.
Pumps are selected by Circuits from the pumps already present in the Plant topology.

Every relationship is stored by a generated identifier rather than by a display name.
Renaming an object should therefore not be used as a substitute for reviewing the resulting topology.
Always open the topology preview after a reconfiguration.

## Shared equipment

Select the same existing pump or valve when it is physically shared by multiple Circuits.
Hydronicus keeps the actuator requested while any active Circuit still consumes it.
It warns when a shared valve prevents independent hydraulic control.

Read [how Hydronicus works](how-it-works.md) for diagrams and the complete ownership rules.

## Configuration checklist

Before accepting a simulated Plant, check all of the following:

- Every temperature sensor is numeric and available.
- Every selected sensor belongs to the intended test configuration.
- Each Zone has at least one selected Circuit.
- Each Circuit has a valid valve path and pump.
- Shared equipment is intentional and documented for the test.
- The topology preview describes the expected route and sequence.
- The Plant remains in Dry run for the first test.
- No real equipment is being used as a test substitute.

If validation rejects a proposed object, review the object references and ownership boundaries before trying a different name.
Hydronicus rejects orphaned or inconsistent topology rather than silently guessing the intended relationship.
