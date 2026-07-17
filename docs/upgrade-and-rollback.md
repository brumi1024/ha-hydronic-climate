# Upgrade and rollback

Hydronicus is an early alpha custom integration.
Treat every upgrade as a configuration change and keep a Home Assistant backup before applying it.

## Before upgrading

1. Confirm the current Hydronicus version and Home Assistant version.
2. Export or back up the Home Assistant configuration and storage using the normal Home Assistant backup process.
3. Record the current Plant names and the synthetic or shadow test scenario.
4. Confirm that all Plants are in shadow mode.
5. Read the release notes for the target version.

Do not upgrade in the middle of a physical heating or cooling intervention.
The current release does not execute physical actuator calls, but future releases may change the control boundary and must be reviewed on their own terms.

## Upgrade through HACS

1. Open HACS and select **Integrations**.
2. Open the Hydronicus repository page.
3. Select the available version or update action.
4. Apply the update.
5. Restart Home Assistant when HACS requests it.
6. Open the Hydronicus config entry and confirm that the Plant loads.
7. Confirm the topology preview, Zone explanations, and shadow-mode state.
8. Repeat the smallest synthetic scenario from [configuration and simulation](configuration.md).

Keep the old backup until the upgraded Plant has passed the intended checks.

## Upgrade from a source checkout

Source checkouts are a development workflow rather than an end-user installation method.
If a source checkout is used for a test, synchronize only the `custom_components/hydronicus` directory into an isolated Home Assistant configuration and record the exact commit.
Follow the synthetic or shadow staging contract in [home-server-staging.md](home-server-staging.md).

Do not copy private server paths, credentials, or deployment commands into a public issue or repository document.

## Configuration migrations

The roadmap requires migrations to preserve UUID relationships across releases.
The current alpha has a small configuration surface and does not promise that every future schema change is automatically migrated.

After every upgrade, verify:

- The Plant config entry still exists.
- Zones still reference the intended Circuits.
- Circuits still reference the intended valves and pumps.
- Shared actuators still have the expected consumers.
- The topology preview is internally consistent.
- The Plant remains in shadow mode.

If a relationship is missing or the entry cannot load, stop and restore the backup before trying to repair the topology manually.

## Rollback through HACS

If the target version fails the synthetic smoke test:

1. Keep the Plant in shadow mode.
2. Capture the first relevant log error and the target version.
3. Restore the Home Assistant backup if configuration data changed.
4. In HACS, open the Hydronicus repository page and select an earlier known-good version if that version is available.
5. Restart Home Assistant.
6. Confirm the Plant and topology preview load.
7. Repeat the synthetic scenario.

If HACS does not offer the previous version, reinstall the known-good release using the repository's documented distribution method.
Record the exact version rather than using an unpinned branch or unreviewed archive.

## Rollback from a source checkout

Stop the isolated Home Assistant test instance before replacing the integration files.
Restore the previous integration directory from the recorded commit or from a clean backup.
Start Home Assistant and verify the config entry and topology preview.

Never use a destructive Git operation against a working tree that contains someone else's changes.
Keep the rollback scoped to the isolated test checkout.

## If rollback is incomplete

Do not enable physical equipment control to test whether the rollback worked.
Keep independent physical controls in charge of the plant.
Restore the Home Assistant configuration and storage backup, then repeat the synthetic test from a clean state.
Report the failed upgrade and rollback separately with the [diagnostic bug-report template](../.github/ISSUE_TEMPLATE/diagnostic-bug-report.md).
