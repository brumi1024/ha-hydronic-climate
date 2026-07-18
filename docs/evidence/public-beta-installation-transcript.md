# Disposable release evidence transcript

This transcript records the automated disposable-instance rehearsal for the Hydronicus public-beta installation contract.

It is synthetic evidence only and is not a non-author installation signoff.

The rehearsal uses no household entity IDs, credentials, physical service calls, or persistent Home Assistant configuration.

## Environment

- Home Assistant test dependency: `2026.7.2`.
- Hydronicus package domain: `hydronicus`.
- Hydronicus package version under test: `0.5.0`.
- Repository commit under test: `9a145ac`.
- Topology source: `tests/fixtures/migrations/v1-current-topology.json`.
- Entity references: synthetic `sensor.synthetic_*` and `switch.synthetic_*` values only.

## Fresh-package rehearsal

```text
$ uv run python scripts/package_release.py --dry-run
Dry-run validated Hydronicus 0.5.0 archive
```

The archive contains only `custom_components/hydronicus` and excludes the legacy `hydronic_climate` package.

The disposable topology starts in shadow mode and does not dispatch physical service calls.

## Upgrade and reload rehearsal

```text
$ uv run pytest tests/integration/test_migration.py -q
5 passed
```

The migration matrix exercises entry versions `(1, 0)` and `(1, 1)` and reconstructs the same UUID-linked topology after setup and reload.

Invalid topology input is rejected without mutating the source entry data or version.

## Rollback rehearsal

The rollback procedure is documented in [upgrade-and-rollback.md](../upgrade-and-rollback.md).

The procedure restores the previous integration release only when that release can read the unchanged config-entry data.

If the data version is incompatible, the procedure restores the complete Home Assistant configuration and storage backup instead of editing storage by hand.

The transcript does not claim an in-place downgrade from config-entry version `(2, 1)`.

## Evidence boundary

The automated rehearsal proves package shape, migration determinism, UUID preservation, atomic invalid-input handling, and synthetic shadow behavior.

A public-beta release still requires an independent human installation and explicit signoff under issue #19.
