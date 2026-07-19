# Hydronicus {{VERSION}}

Candidate note: the configurable Plant-level Dry run setting is implemented in the current `v0.1.0` candidate.
Do not publish these release notes until the control-mode implementation and required staging evidence are complete.

## Highlights

- Complete Dry run Zone climate entities with comfort, eco, and away presets.
- Required and optional temperature observations with calibration and freshness handling.
- Mean, median, minimum, maximum, designated-reference, and weighted-mean aggregation.
- Configurable hysteresis, minimum active duration, and minimum idle duration.
- Aggregate-temperature, blocked-state, blocked-reason, and structured shared-valve warning visibility.
- Cooling condensation diagnostics and deterministic Dry run source recommendations.
- Explicit idempotent actuator execution with one Plant-level Dry run control.
- Proposed-versus-executed operation reporting and ordered safe shutdown when Dry run is re-enabled.

## Upgrade

Back up the Home Assistant configuration before upgrading.

Install this release through HACS, restart Home Assistant, and confirm that the Hydronicus config entry reloads without errors.

Every Plant created through the UI starts in Dry run.
The Plant reconfiguration flow can disable Dry run after one confirmation of the configured heating outputs.
Cooling starts and source-selector operations remain Dry run only.

Legacy temperature sensors load as required observations with a maximum age of 1,800 seconds.
This freshness default is an intentional fail-closed behavior change: a legacy Zone blocks when a required reading becomes stale until the sensor reports again or its configuration is reviewed.

## Rollback

If the integration does not load correctly, follow the backup-first rollback guide.

Keep physical temperature, condensation, pressure, and flow safeguards independently active during any rollback.

## Known limitations

Dry run Plants do not issue physical actuator service calls.
When Dry run is off, the generic executor can control configured heating valves, pumps, and direct source demand after the safety checks.
Physical rollout remains unauthorized until the required disposable staging evidence and human approval are complete.

Repairs, redacted downloadable diagnostics, startup reconciliation, and bounded command-failure recovery are implemented.
Physical cooling starts and automatic source selection remain gated while the public beta matures.

## Hydronicus rename boundary

Hydronicus is installed from `custom_components/hydronicus` and uses the `hydronicus` domain.

The former `hydronic_climate` integration name and domain are not supported and must not be recreated during an upgrade.
