# Hydronicus {{VERSION}}

## Highlights

- Complete shadow-mode Zone climate entities with comfort, eco, and away presets.
- Required and optional temperature observations with calibration and freshness handling.
- Mean, median, minimum, maximum, designated-reference, and weighted-mean aggregation.
- Configurable hysteresis, minimum active duration, and minimum idle duration.
- Aggregate-temperature, blocked-state, blocked-reason, and structured shared-valve warning visibility.

## Upgrade

Back up the Home Assistant configuration before upgrading.

Install this release through HACS, restart Home Assistant, and confirm that the Hydronicus config entry reloads without errors.

New plants remain in shadow mode until their compiled topology and explanations have been reviewed.

Legacy temperature sensors load as required observations with a maximum age of 1,800 seconds.
This freshness default is an intentional fail-closed behavior change: a legacy Zone blocks when a required reading becomes stale until the sensor reports again or its configuration is reviewed.

## Rollback

If the integration does not load correctly, restore the previous HACS release and restart Home Assistant.

Keep physical temperature, condensation, pressure, and flow safeguards independently active during any rollback.

## Known limitations

This release is shadow-mode software and does not issue physical actuator service calls.

Active equipment control, safety interlocks, heating and cooling changeover, source coordination, diagnostics, and repairs remain limited or planned while the public beta matures.

## Hydronicus rename boundary

Hydronicus is installed from `custom_components/hydronicus` and uses the `hydronicus` domain.

The former `hydronic_climate` integration name and domain are not supported and must not be recreated during an upgrade.
