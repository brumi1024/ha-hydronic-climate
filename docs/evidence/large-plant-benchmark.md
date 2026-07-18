# Large synthetic Plant benchmark

The benchmark was recorded on 2026-07-18 in the isolated worktree on macOS arm64 (`Darwin 27.0.0`).

The environment used Python 3.14.6, Home Assistant 2026.7.2, pytest 9.0.3, and `pytest-homeassistant-custom-component` 0.13.346.

The command was `/Users/benjaminteke/.cache/uv/archive-v0/1DnAqMaMGcCSHYZRevXYy/bin/pytest tests/benchmarks/test_large_synthetic_plant.py -q -s`.

The topology contained 48 zones, 24 circuits, 12 shared valves, 6 shared pumps, 3 sources, and 96 routes.

The topology also contained 96 temperature sensors, 48 humidity sensors, 48 supply or surface interlock references, and a shadow-only source selector.

The measured compile time was 8.572 ms.

The measured evaluation time was 24.758 ms.

The measured peak traced allocation during compile and evaluation was 0.218 MiB.

The controller produced 48 cooling interlock results.

The shadow runtime performed 2 reconciliations, of which 0 changed actuator observations and 2 were unchanged.

The runtime performed 3 refreshes and 3 evaluations.

The registered entity-update listener was called 3 times.

The intercepted Home Assistant service-call count was 0.

The checked-in thresholds are 1000 ms for compile, 2000 ms for evaluation, 128 MiB for traced memory, at least 2 reconciliations, at least 1 entity update, and 0 service calls.

GitHub-hosted Linux runs measured pure evaluation at 1109.536 ms and 1127.982 ms, while the recorded macOS run measured 24.758 ms.

Those runs isolated the variation to evaluation timing, so the 2000 ms budget accommodates the slower hosted runner while retaining a bounded check.

If a future run crosses a threshold, retain the topology and emitted metrics, identify whether the regression is in compilation, evaluation, reconciliation, or publication, and document the reason before changing the threshold.

The benchmark is synthetic and shadow-only evidence, not authorization for physical Home Assistant service execution.
