# Troubleshooting

This guide covers the current shadow-mode implementation.
It is written for a disposable or staging Home Assistant instance.

## Before troubleshooting

Record the Hydronicus version, Home Assistant version, and the commit or HACS version being tested.
Keep credentials, access tokens, private addresses, and household-specific entity details out of issue reports.

If the test involves real sensors, confirm that the Plant is still in shadow mode before continuing.
The current release should not issue physical actuator service calls.

## Installation and setup

### Hydronicus is not listed in HACS

Confirm that the repository was added under HACS **Custom repositories** with type **Integration**.
Reload HACS or restart Home Assistant after installing the repository if the integration does not appear.
Check that the repository contains the integration under `custom_components/hydronicus`.

The repository is not currently a HACS default repository, so searching the default catalog alone is insufficient.

### Add integration fails immediately

Check the Home Assistant log for the first Hydronicus exception.
Confirm that Home Assistant meets the repository's minimum declared version.
Remove and reinstall only after preserving the relevant log excerpt and configuration backup.

Do not create a second Plant to work around a validation error.
First confirm whether a Plant with the same repository installation already exists.

### A setup form cannot select an entity

The first Zone form expects one or more `sensor` entities.
The first Circuit form expects a `switch` or `valve` entity for the valve and a `switch` entity for the pump.
Confirm that the synthetic entities have the expected domain and are visible in Home Assistant.

### The review reports an invalid topology

Review the selected sensor, valve, and pump entities.
Confirm that the Circuit and Delivery Route references are complete and that no object was removed while a relationship still points to it.
Hydronicus rejects orphaned and inconsistent graphs rather than guessing a relationship.

## Unavailable or invalid sensors

### The Zone is unavailable or demand is off

An unavailable, unknown, non-numeric, non-finite, untimestamped, or stale required sensor blocks the Zone immediately.
The blocked binary sensor turns on, the blocked-reason sensor explains the failure, and the Zone releases demand even during a minimum-active hold.
An unusable optional sensor is excluded from aggregation and appears in the aggregate-temperature and blocked-state attributes.
If no usable sensor remains, the Zone blocks even when every configured observation is optional.

Check the sensor state in **Developer tools > States**.
Use a numeric Celsius value for the simulated sensor.
Check the observation's configured required status, maximum age, and calibration offset before changing the topology.
Do not paste private device attributes into a public report.

### A battery sensor appears stale

Hydronicus uses Home Assistant's latest report timestamp and the observation's configured maximum age.
The runtime schedules reevaluation at the freshness deadline, so a Zone can become blocked without another state-change event.
Increase the maximum age only when the sensor's real reporting behavior justifies it.
Do not use a longer software timeout as a substitute for reliable sensing or independent physical protection.

### Multiple sensors give an unexpected aggregate

Check the selected aggregation policy and the current state of every usable sensor.
Mean and median use all usable calibrated readings selected for the Zone.
Minimum and maximum intentionally bias the aggregate toward one extreme.
Designated reference requires exactly one configured reference observation.
Weighted mean uses the positive weights configured through detailed sensor editing.
Inspect the aggregate-temperature sensor attributes to confirm which observations were usable or excluded.

### Demand remains on or off after crossing the threshold

Inspect the Zone explanation for a minimum-active hold or minimum-idle lockout deadline.
Changing a target or preset reevaluates the Zone immediately but does not bypass a remaining duration.
A required-sensor failure is the exception: it blocks and releases demand immediately.

## Warnings, explanations, and virtual states

### The valve is opening and the pump is not requested

This is expected while the configured virtual valve opening time has not elapsed.
The current sequence waits for virtual valve readiness before requesting the pump.
The valve state and Zone explanation entities should show the reason.

### The pump remains requested after demand stops

This is expected during the configured virtual pump overrun period.
The overrun protects the modeled Circuit sequence in shadow mode.
It does not prove that a physical pump needs the same timing.

### A shared pump does not turn off when one Zone releases

This is expected when another requested Circuit still consumes the same pump.
Inspect the topology preview and the Zone demand entities to identify the remaining virtual consumer.

### The topology preview counts do not match the setup

Reload the config entry after changing a subentry.
Then inspect the topology preview attributes and the Home Assistant log for a reload exception.
Do not assume that a display name identifies the persisted object relationship.

### A shared-valve warning appears during configuration

The proposed topology is valid, but the named Circuits share a valve that limits independent hydraulic control.
Review the affected valve, Circuits, and Zones before confirming the non-fatal warning.
Do not suppress the warning by modeling one physical valve as several independent actuators.

## Logs and diagnostics

Capture the earliest relevant error, not only the final repeated warning.
Include the Home Assistant version, Hydronicus version, operation being attempted, and the redacted exception text.

Useful checks include:

1. Open **Settings > System > Logs**.
2. Filter for `hydronicus`.
3. Reproduce the problem once with the smallest synthetic topology.
4. Copy the first relevant exception and its short traceback.
5. Redact tokens, credentials, hostnames, private addresses, and household-specific entity details.

The current release does not provide downloadable diagnostics or Repairs issues.
Do not claim that a missing diagnostic download is a runtime failure.
Use the [diagnostic bug-report template](../.github/ISSUE_TEMPLATE/diagnostic-bug-report.md) and provide only the information needed to reproduce the issue.

## Recovery

### The integration reload fails

Keep the Plant in shadow mode.
Check for an invalid or partially edited subentry and restore the last known-good configuration from a Home Assistant backup if necessary.
Then restart or reload the integration and confirm that the topology preview returns.

The current unload path does not issue equipment commands.
Physical equipment must still have its own independent controls and manual recovery procedure.

### The test instance is no longer trustworthy

Stop using real sensor or actuator entities for the test.
Restore the disposable Home Assistant configuration or recreate it from a clean backup.
Repeat the synthetic test before resuming any shadow observation.

### You need to undo an installation

Follow [upgrade and rollback](upgrade-and-rollback.md) for a backup-first rollback.
Do not delete the integration directory from a running Home Assistant instance as a first response to a configuration problem.
