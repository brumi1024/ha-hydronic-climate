# Development environment

The supported local workflow uses Python 3.14.2 or newer, `uv`, and the commands in the root `Makefile`.
The lockfile is the source of truth for exact development and test dependency versions.

## First setup

Install `uv`, then run:

```console
make bootstrap
make hooks
```

`make bootstrap` creates or updates `.venv` from `uv.lock`.
`make hooks` installs the shared pre-commit hooks after bootstrapping the environment.

## Daily commands

- `make test-core` runs deterministic topology and controller tests with core coverage.
- `make test-integration` runs Home Assistant config, entity, and lifecycle adapter tests.
- `make test-scenarios` runs named, time-ordered operating scenarios.
- `make lint` checks Ruff linting, Python compilation, and repository JSON files.
- `make format-check` checks the complete repository with the normal Ruff formatter configuration.
- `make typecheck` checks the dependency-free controller package with mypy.
- `make verify` runs the complete local quality gate used by CI.

Run the narrowest relevant target while developing and run `make verify` before handing off a chunk.
The pre-commit hook applies Ruff formatting to every changed Python file.
The CI format check covers the same complete source tree.

## Canonical pre-release configuration

Config-entry version 1 and minor version 1 are the initial supported persisted contract.
Fresh UI setup and reconfiguration are the source of truth for that contract.
They persist UUID-backed Plant, Zone, Circuit, Delivery Route, Valve, Pump, Source, and selector objects with one field name and representation per concept.
Zone observations are stored as typed temperature and humidity metadata collections, not parallel scalar IDs, ID lists, maps, or standalone weight fields.
Parent-owned initial objects and config-subentry-owned repeatable objects are composed before one atomic topology compile.

Hydronicus has no published release or tag, so development and staging Plants created before this boundary are disposable and must be recreated.
Do not add migration hooks, predecessor fixtures, rollback decoders, or speculative aliases for those entries.
Compatibility begins only after a schema has shipped in a published release with real users.

## Architecture boundaries

`custom_components/hydronicus/core/configuration.py` decodes only the canonical persisted objects into typed domain values.
`custom_components/hydronicus/entry_configuration.py` composes parent and subentry ownership without importing controller policy.
`custom_components/hydronicus/core/topology.py` indexes objects, validates relationships, and builds deterministic summaries and warnings.
`custom_components/hydronicus/core/controller.py` is a pure pipeline for heating, cooling, route arbitration, mode changeover, valve planning, pump planning, source coordination, and final assembly.
Its public evaluation result, diagnostics, deadlines, and command order are the contract; private phase helper structure is not.
`custom_components/hydronicus/runtime.py` owns the Home Assistant boundary and runs snapshot, evaluate, execute, and publish stages in that order.
Runtime deadline scheduling and per-operation reconciliation remain adapter concerns because they depend on Home Assistant time, observations, and service results.

## Test boundaries

Pure controller behavior belongs under `tests/core/` and must use only the dependency-free controller interface.
Home Assistant setup, subentry, entity, reload, and adapter behavior belongs under `tests/integration/`.
Multi-step behavior with a fake clock belongs under `tests/scenarios/` and should use the reusable scenario harness.
Safety invariants that must hold across many topology shapes or timings belong in property-based tests.

The current coverage threshold applies only to `custom_components/hydronicus/core`.
This keeps the safety-critical deterministic package measurable without obscuring incomplete adapter milestones behind a repository-wide percentage.

## Dependency changes

Edit `pyproject.toml`, then regenerate and validate the lockfile:

```console
uv lock
make bootstrap
make verify
```

Commit `pyproject.toml` and `uv.lock` together.
