# Home-server staging environment

The home-server environment is a final integration-test surface for Home Assistant UI and runtime behavior.
It does not replace deterministic local tests or CI.

Keep staging isolated from the production Home Assistant configuration and physical plant control.
Use a separate Home Assistant instance or a separate disposable configuration directory with its own storage, database, and test entities.

## Required shape

- Run the same Home Assistant release declared in `hacs.json` or the compatibility version currently under test.
- Mount or synchronize this checkout into `config/custom_components/hydronicus`.
- Use synthetic temperature sensors and input booleans for the first validation stage.
- Keep Dry run enabled when observing real sensors or equipment.
- Keep credentials, server addresses, tokens, and site-specific paths outside the repository.
- Make restoring the staging configuration or container a simple, documented server-side operation.

The existing site-specific home-server document should supply the host, deployment path, restart command, and log command.
Do not duplicate those private values in this repository.

## Chunk smoke test

Run `make verify` locally before deploying a chunk.
Then perform the applicable staging checks:

1. Synchronize the integration directory and restart Home Assistant or reload the integration as appropriate.
2. Confirm Home Assistant starts without integration or translation errors.
3. Create, reconfigure, reload, and delete the affected plant objects through the UI.
4. Confirm entity IDs, unique IDs, topology preview, and diagnostics match the configured synthetic plant.
5. Drive the synthetic sensor values through the chunk's named scenario.
6. Confirm the visible explanations and virtual actuator sequence match the automated scenario.
7. Confirm logs contain no unexpected exceptions or repeated warnings.
8. Confirm Dry run issued no physical service calls.

Record the Home Assistant version, commit SHA, scenario name, result, and any log excerpt needed to explain a failure.

## Activation boundary

Do not use staging to exercise real actuator service calls until the implementation plan reaches the corresponding staged rollout and provides an immediate manual rollback.
Synthetic testing comes first, Dry run observation comes second, and any physical control requires an explicit rollout decision.
