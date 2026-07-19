# Install, update, and rollback

Hydronicus `0.1.0` is the first public release.
There is no earlier public installation or persisted schema to migrate.

## Fresh HACS installation

1. Open HACS and select **Integrations**.
2. Open the HACS menu and select **Custom repositories**.
3. Add `https://github.com/brumi1024/ha-hydronicus` and choose **Integration** as the repository type.
4. Install Hydronicus.
5. Restart Home Assistant when HACS requests it.
6. Open **Settings > Devices & services > Add integration** and search for **Hydronicus**.
7. Follow [configuration and simulation](configuration.md) and keep Dry run enabled for the first Plant.

The package installs only `custom_components/hydronicus`.
The old `hydronic_climate` package and domain are not part of Hydronicus and must not be created.

## Before changing an installation

Create a Home Assistant backup before updating Hydronicus, changing a Plant that controls heating, or testing a source checkout.

Keep Dry run enabled until the configured sensors, valves, pumps, source demand, topology preview, and proposed operations match the intended Plant.

Do not update or reload Hydronicus during a physical heating intervention.

## Updating after later releases exist

Use HACS to install the selected released version and restart Home Assistant when requested.

After the restart:

1. Confirm the Hydronicus version.
2. Confirm that every Plant loads.
3. Review the topology preview and configured entities.
4. Confirm the Dry run setting.
5. Exercise the smallest safe scenario before relying on active heating.

Release-specific compatibility or migration instructions belong in the release that introduces them.
Version `0.1.0` does not carry speculative predecessor support.

## Rolling back a development checkout

Keep the Plant in Dry run.
Stop the isolated Home Assistant test instance before replacing integration files.
Restore `custom_components/hydronicus` from the recorded commit or a clean backup.
Start Home Assistant and verify the config entry, topology preview, and Dry run behavior.

If the configuration is no longer trustworthy, restore the complete Home Assistant backup rather than editing `.storage` by hand.

Never use a destructive Git operation against a working tree that contains someone else's changes.

## If rollback is incomplete

Do not activate physical equipment to test whether rollback worked.
Keep independent physical controls in charge of the Plant.
Restore the Home Assistant backup, then repeat the Dry run test from a clean state.
Report the problem with the [diagnostic bug-report template](../.github/ISSUE_TEMPLATE/diagnostic-bug-report.md).
