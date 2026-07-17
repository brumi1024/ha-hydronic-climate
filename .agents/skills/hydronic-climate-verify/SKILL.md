---
name: hydronic-climate-verify
description: Verify Hydronic Climate implementation chunks against docs/implementation-plan.md, safety invariants, Home Assistant adapter behavior, and named operating scenarios. Use when implementing, reviewing, or declaring a milestone chunk complete in this repository.
---

# Hydronic Climate Verify

Treat repository commands as the source of truth and produce explicit evidence for the selected implementation-plan acceptance criteria.

## Verification workflow

1. Read the relevant milestone and acceptance criteria in `docs/implementation-plan.md`.
2. Read `CONTEXT.md` and preserve the boundary between deterministic code in `core/` and Home Assistant adapters.
3. State which public test seams the chunk changes:
   - Use `tests/core/` for deterministic control and topology behavior.
   - Use `tests/integration/` for Home Assistant config, entity, lifecycle, and adapter behavior.
   - Use `tests/scenarios/` for time-ordered plant behavior across several evaluations.
4. Add or update the smallest tests that prove the selected criteria.
5. Add a property test when the change affects a safety invariant across many topology or timing combinations.
6. Run the narrowest applicable target while working: `make test-core`, `make test-integration`, or `make test-scenarios`.
7. Run `make verify` before declaring the chunk complete.
8. If Home Assistant UI or runtime behavior changed, follow `docs/home-server-staging.md` in synthetic or shadow mode and record the observed result.

## Completion report

Report the selected acceptance criteria, the automated evidence for each criterion, commands and outcomes, staging evidence when applicable, and any unverified gap.
Do not claim a milestone is complete while a required check is skipped or failing.
Do not enable physical service calls merely to complete a verification step.
