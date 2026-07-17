# Hydronic Climate

Hydronic Climate is a Home Assistant custom integration for topology-aware hydronic heating and cooling control.

It starts as a shadow-mode plant model and will grow into a deterministic controller for shared valves, pumps, sources, and safety interlocks.

## Status

This repository is in early implementation.

The current slice establishes a shadow-only setup flow for one zone, temperature sensor, circuit, valve, pump, and delivery route.
It persists that topology in the plant config entry after compiling and validating it.
The deterministic controller remains read-only and never issues Home Assistant service calls.

## Installation

Install the repository as a custom integration through HACS.

Then add Hydronic Climate from the Home Assistant UI.

## Development

See [the development environment](docs/development.md) for reproducible setup and verification commands.
Use [the home-server staging contract](docs/home-server-staging.md) for final Home Assistant UI and runtime checks.
