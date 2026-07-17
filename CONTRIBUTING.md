# Contributing

Keep changes focused on the current implementation plan.

Prefer deterministic controller logic in `custom_components/hydronic_climate/core/` and Home Assistant adapters at the integration boundary.

Add tests for pure domain code first, then integration tests for setup and unload behavior.

Use the commands in [the development environment guide](docs/development.md).
Run `make verify` before handing off a change.
Use the repository-local `hydronic-climate-verify` skill to map milestone acceptance criteria to test and staging evidence.
