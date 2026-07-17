# Test layout

`core/` contains pure deterministic controller tests.
`integration/` contains Home Assistant adapter tests.
`scenarios/` contains named, time-ordered operating scenarios over the pure controller seam.
Root-level tests cover isolated regressions that do not require the Home Assistant test harness.
Property-based tests in `core/` exercise safety invariants across generated topology and timing inputs.
