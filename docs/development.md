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
- `make typecheck` checks the dependency-free controller package with mypy.
- `make verify` runs the complete local quality gate used by CI.

Run the narrowest relevant target while developing and run `make verify` before handing off a chunk.
The pre-commit hook applies Ruff formatting to changed Python files while the existing source tree converges on formatter-clean output.

## Test boundaries

Pure controller behavior belongs under `tests/core/` and must use only the dependency-free controller interface.
Home Assistant setup, subentry, entity, reload, and adapter behavior belongs under `tests/integration/`.
Multi-step behavior with a fake clock belongs under `tests/scenarios/` and should use the reusable scenario harness.
Safety invariants that must hold across many topology shapes or timings belong in property-based tests.

The current coverage threshold applies only to `custom_components/hydronic_climate/core`.
This keeps the safety-critical deterministic package measurable without obscuring incomplete adapter milestones behind a repository-wide percentage.

## Dependency changes

Edit `pyproject.toml`, then regenerate and validate the lockfile:

```console
uv lock
make bootstrap
make verify
```

Commit `pyproject.toml` and `uv.lock` together.
