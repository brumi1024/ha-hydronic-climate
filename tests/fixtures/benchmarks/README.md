# Synthetic benchmark fixtures

The benchmark fixtures describe deterministic, household-agnostic Plant profiles.

The profile is expanded by `tests/benchmarks/plant_factory.py` into persisted config-entry data so the topology remains readable without checking in thousands of repetitive JSON lines.

Every generated name and entity binding is synthetic.
The benchmark runs in shadow mode and never authorizes a Home Assistant service call.

The performance thresholds are intentionally broad enough for deterministic CI and macOS runs.
If a regression crosses a threshold, keep the fixture and metrics, investigate the first changed phase, and adjust a threshold only with a documented reason and a new measurement.
