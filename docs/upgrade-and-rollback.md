# Upgrade and rollback

Hydronicus `0.5.0` is the public-beta release.
Treat every upgrade as a configuration change and keep a Home Assistant backup before applying it.

The public-beta upgrade contract covers the only predecessor release distributed by this repository before `0.5.0`, `0.1.0-alpha.1`.
Its persisted Hydronicus config entries are covered at entry versions `(1, 0)` and `(1, 1)`.
The roadmap labels `0.3.0-beta.1` and `0.4.0-beta.1` are not distributed predecessor artifacts in this repository and are not claimed as supported upgrade sources.

## Before upgrading

1. Confirm the current Hydronicus version and Home Assistant version.
2. Export or back up the complete Home Assistant configuration and storage using the normal Home Assistant backup process.
3. Record the current Plant names and the synthetic or shadow test scenario.
4. Confirm that all Plants are in shadow mode.
5. Read the release notes for the target version.

Do not upgrade in the middle of a physical heating or cooling intervention.
The public beta does not execute physical actuator calls, but future releases may change the control boundary and must be reviewed on their own terms.

## Fresh HACS-style install

1. Open HACS and select **Integrations**.
2. Open the HACS menu and select **Custom repositories**.
3. Add `https://github.com/brumi1024/ha-hydronicus` and choose **Integration** as the repository type.
4. Install the `0.5.0` Hydronicus release.
5. Restart Home Assistant when HACS requests it.
6. Open **Settings > Devices & services > Add integration** and search for **Hydronicus**.
7. Follow [configuration and simulation](configuration.md) to create the synthetic Plant.

The package must install only `custom_components/hydronicus`.
The old `hydronic_climate` package and domain are not part of the public-beta package and must not be recreated during installation or upgrade.

## Upgrade through HACS

1. Keep the pre-upgrade backup until the upgraded Plant passes its smoke test.
2. Open HACS and select **Integrations**.
3. Open the Hydronicus repository page.
4. Select the available `0.5.0` update.
5. Apply the update and restart Home Assistant when HACS requests it.
6. Open the Hydronicus config entry and confirm that the Plant loads.
7. Confirm the Plant, Zone, Circuit, Actuator, and Delivery Route relationships in the topology preview.
8. Confirm that entity unique IDs are unchanged.
9. Confirm that the Plant remains in shadow mode.
10. Repeat the smallest synthetic scenario from [configuration and simulation](configuration.md).

After the upgrade, verify that the Plant UUID, Zone UUID, Circuit UUID, valve UUID, pump UUID, and Delivery Route UUID are unchanged.
Verify that the relationship from the Zone to the Circuit and from the Circuit to its valve and pump is unchanged.
Keep the old backup until these checks pass.

## Upgrade from a source checkout

Source checkouts are a development workflow rather than an end-user installation method.
If a source checkout is used for a test, synchronize only the `custom_components/hydronicus` directory into an isolated Home Assistant configuration and record the exact commit.
Follow the synthetic or shadow staging contract in [home-server-staging.md](home-server-staging.md).

Do not copy private server paths, credentials, or deployment commands into a public issue or repository document.

## Configuration migrations

The migration test matrix covers every supported stored schema (`1.0` and `1.1`) and both persisted entry versions registered by the public beta.
Each migration step validates the reconstructed topology before Home Assistant writes the migrated entry.
The migration is atomic, so an invalid candidate leaves the original config-entry data and version untouched.

The migration framework preserves UUID-backed object and relationship identifiers.
It does not invent a relationship when a predecessor entry is malformed or incomplete.
If a relationship is missing or the entry cannot load, stop and restore the backup before trying to repair the topology manually.

## Rollback through HACS

If the target version fails the synthetic smoke test:

1. Keep the Plant in shadow mode.
2. Capture the first relevant log error and the target version.
3. Stop making configuration changes.
4. In HACS, open the Hydronicus repository page and select the previous known-good release.
5. Apply that release and restart Home Assistant.
6. Confirm the Plant and topology preview load.
7. Repeat the synthetic scenario.

If the previous release is not offered by HACS, restore the complete Home Assistant backup made before the upgrade.
Then install the recorded previous Hydronicus release using the repository's documented distribution method.
Record the exact version rather than using an unpinned branch or unreviewed archive.

## Data-version limit

The public-beta migration path is forward-only from entry versions `(1, 0)` and `(1, 1)` to `(2, 1)`.
Hydronicus does not promise an in-place downgrade from `(2, 1)` to an older config-entry version.
Rolling back only the integration files is therefore safe only when the previous release can still read the unchanged config-entry data.
If the upgrade wrote a newer or incompatible config-entry schema, restore the complete Home Assistant configuration and storage backup before starting the previous release.
Do not edit config-entry storage by hand to force an older version to load.

## Rollback from a source checkout

Stop the isolated Home Assistant test instance before replacing the integration files.
Restore the previous integration directory from the recorded commit or from a clean backup.
Start Home Assistant and verify the config entry, topology preview, UUID relationships, and entity unique IDs.

Never use a destructive Git operation against a working tree that contains someone else's changes.
Keep the rollback scoped to the isolated test checkout.

## If rollback is incomplete

Do not enable physical equipment control to test whether the rollback worked.
Keep independent physical controls in charge of the plant.
Restore the Home Assistant configuration and storage backup, then repeat the synthetic test from a clean state.
Report the failed upgrade and rollback separately with the [diagnostic bug-report template](../.github/ISSUE_TEMPLATE/diagnostic-bug-report.md).
