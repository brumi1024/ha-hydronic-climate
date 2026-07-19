# Hydronicus implementation plan

Status: Version `0.1.0` control-mode implementation is complete in the working tree; release is not ready.

This document is the current implementation and evidence snapshot for main.

It supersedes the obsolete implementation-wave snapshot and must be reconciled against the repository, live issues, checked-in evidence, and the current main commit before new work is launched.

## Current baseline

- Repository: `brumi1024/ha-hydronicus`.
- Main commit: `6a8bb45e5ce1be423a4e39e6b0fff18a94331252`.
- `origin/main` matches the assessed main commit.
- Integration name: Hydronicus.
- Domain: `hydronicus`.
- Package: `custom_components/hydronicus`.
- Candidate version: `0.1.0`.
- The manifest, project metadata, README, packaging script, and HACS metadata agree on version `0.1.0`.
- The historical merged baseline passed `make verify` with 352 tests.
- The historical merged baseline reports 91.29 percent core coverage.
- The current working tree requires a fresh verification pass after the control-mode implementation.
- The current working tree preserves the simplified topology and removes the obsolete migration-only package and tests.
- The previous focused release, actuator, cooling, source, and operating-scenario suite passed before the migration cleanup.
- The current `scripts/public_beta_smoke.py` run passed all public-beta repository checks.
- Ruff, formatting, mypy, compileall, JSON validation, package validation, and public-beta validation pass in the merged evidence.
- GitHub Actions checks named `syntax`, `test`, `hassfest`, and `hacs` are green for the assessed main commit.
- Pull request #27 merged the M4-M7 safety implementation.
- Pull request #28 merged the public-beta installation and benchmark implementation; its speculative migration machinery has been removed before the first public release.
- No Git tag or GitHub Release currently exists for `v0.1.0`.
- The working tree contains the uncommitted `0.1.0` candidate preparation, including this reconciled implementation-plan edit, and the protected untracked directory `docs/research/`.
- `docs/research/` belongs to the user and must not be staged, modified, moved, deleted, or committed.
- Existing worktrees are retained and must not be deleted or repurposed by this plan.

## Product and safety contract

Hydronicus is a generic Home Assistant custom integration for dynamically configured hydronic heating and cooling plants.

The integration must contain no household-specific entity IDs, topology, names, credentials, or physical assumptions.

One Home Assistant config entry represents one Plant.

Plant topology is configured through the Home Assistant UI and relationships use generated UUIDs rather than display names.

Zones request comfort and never directly own shared physical actuators.

The deterministic core compiles topology and evaluates a snapshot into an idempotent control plan and structured diagnostics.

Home Assistant observation, entity publication, configuration flows, service execution, Repairs, and diagnostics remain adapter responsibilities.

Every new Plant starts in Dry run.

The Plant UI exposes one supported path for changing the Dry run setting.

Dry run observes and calculates heating, cooling, and source behavior without dispatching physical service calls.
With Dry run off, heating valves, pumps, and configured direct source demand may execute after confirmation and path validation.

The tested heating executor must be placed behind the Dry run setting below before release.

Cooling starts remain Dry run only until the cooling pilot is accepted.

Source-selection starts remain explicitly Dry run only in the runtime.

Source-demand starts may execute only when Dry run is off and a valid pump path exists.

Unknown, unavailable, invalid, or stale physical observations fail closed.

A source demand request requires a valid pump path.

Shared actuators are controlled from the complete active-consumer set and never through unconditional toggle behavior.

Heat-cool changeover waits for safe idle before a conflicting mode is requested.

Independent physical safety controls remain mandatory and are not replaced by Home Assistant logic.

No staging or automated evidence authorizes physical equipment control.

## Version 0.1.0 control-mode contract

The release target is no longer Dry run-only.

Version `0.1.0` supports actual heating control and retains a safe Dry run mode that shows the complete proposed behavior without dispatching Home Assistant service calls.
The implementation is present in this working tree and requires fresh automated and disposable staging evidence before publication.

### One simple setting

The Plant has one `dry_run` boolean configured through the Home Assistant UI.

`dry_run` defaults to `true`.

When Dry run is on, Hydronicus uses the real configured sensors and reads the real actuator states.
It calculates the same heating plan it would calculate during active control, but dispatches no Home Assistant service calls.

The UI and diagnostics show the proposed operations, including which valves would open, which pumps would start, whether source demand would turn on, and why an operation is blocked.

When Dry run is off, Hydronicus executes the heating plan against the configured entities.

With Dry run off, heating control includes:

- Heating valve open and close operations.
- Heating pump start and stop operations.
- A configured direct heat-source demand on and off operation after a valid pump path exists.
- The existing ordered safe-shutdown behavior.

Cooling starts remain Dry run only in version `0.1.0`.

Automatic source-selector operations remain Dry run only in version `0.1.0`.

The source recommendation remains visible in both modes.

### UI behavior

Initial Plant setup includes a **Dry run** switch that defaults to on and explains that no equipment will be controlled.

The Plant configuration page allows the same setting to be changed later.

Turning Dry run off requires one confirmation that the displayed valve, pump, and optional source-demand entities may be controlled.

This is a normal configuration action, not a separate rollout framework, approval packet, or multi-stage activation system.

The Plant exposes its current Dry run state and the latest proposed or executed operations.

### Runtime behavior

The existing actuator executor remains the single execution seam.

In Dry run, it records commands as proposed and dispatches none.

With Dry run off, it dispatches allowed heating commands idempotently.

Cooling starts and source-selector commands remain forced to Dry run regardless of the Plant setting.

Turning Dry run on while heating equipment is active first uses the existing safe-shutdown sequence, then prevents further commands.
It must not simply stop dispatching and leave equipment running.

Reload and restart reconstruct actuator state conservatively from Home Assistant observations.

Independent hardware controls must leave the physical plant safe if Home Assistant or Hydronicus disappears.

Because there is no public installed version, the pre-release schema uses the final `dry_run` model without a migration layer.

### Safety invariants

- Dry run dispatches zero Home Assistant actuator service calls.
- Turning Dry run off never executes a cooling start or source-selector operation.
- A pump never starts before every required valve is ready.
- Direct source demand never starts without a valid running pump path.
- Unknown, unavailable, invalid, or stale required observations fail closed.
- Shared actuators remain requested until their complete active-consumer set is empty.
- Repeated evaluation does not dispatch duplicate or toggle operations.
- Turning Dry run on cannot leave Hydronicus claiming inactivity while an ordered shutdown is incomplete.

### Implementation slices

The three control-mode slices are implemented in the current working tree.

1. Replace the legacy mode flag with the user-facing `dry_run` setting and show proposed versus executed operations. Complete.
2. Add the simple setup and reconfiguration control, direct source-demand execution, and safe transition back to Dry run. Complete.
3. Verify with synthetic and intercepted entities, then stop for the explicitly authorized physical heating check before any physical release decision. In progress.

The affected public test seams are `tests/core/` for execution invariants, `tests/integration/` for Home Assistant configuration and dispatch, and `tests/scenarios/` for demand, shutdown, reload, and failure timelines.

## Evidence status model

Implementation complete means the source and tests for the acceptance criterion are merged and reviewed.

Automated evidence complete means the applicable local and hosted checks are green for the assessed commit.

Disposable staging complete means the named behavior was observed in an isolated Home Assistant configuration with synthetic, intercepted, or Dry run-only entities.

Human approval complete means the required human signed off the exact scope and evidence.

Release complete means the approved public artifact exists and the required fresh-install, upgrade, and rollback evidence was observed against that artifact.

These states are independent and must not be collapsed into one completion label.

## M0-M3 foundation status

The dynamic topology, deterministic controller, Home Assistant adapter seam, UI configuration model, UUID relationships, shadow-first setup, and zone climate foundation are implemented in the current package.

The current source of truth for those behaviors is the code and tests under `custom_components/hydronicus`, `tests/core`, `tests/integration`, and `tests/scenarios`.

The original M0-M3 design details remain useful as architecture, but their historical implementation-wave launcher is retired.

The remaining work is evidence and governance work ordered by the dependency graph below.

## M4-M7 implementation status

### M4: Heating actuator execution

M4 implementation is complete in PR #27.

Issue #8, valve readiness before pump execution, is closed with merged code and automated evidence.

Issue #9, actuator feedback, mismatch handling, and safe shutdown, is closed with merged code and automated evidence.

Issue #12 remains open as the human review required before the project adds or uses a supported partial-heating activation path.

The remaining M4 gates are a disposable synthetic actuator transcript, an exact proposed valve, pump, circuit, observer, rollback action, and explicit human approval before any physical service call.

### M5: Operational hardening

M5 implementation is complete in PR #27.

Issue #11, startup reconciliation and delayed or failed command recovery, is closed with merged code and automated evidence.

Issue #13, actionable Repairs for unresolved bindings, is closed with merged code and automated evidence.

Issue #14, bounded redacted diagnostics, is closed with merged code and automated evidence.

The remaining M5 gate is disposable-instance observation of reload, unload, restart, Repairs, diagnostics, and unexpected-log behavior as part of the release and pilot evidence.

### M6: Cooling and condensation protection

M6 implementation is complete in PR #27.

Issue #10, cooling-enabled circuit enforcement and shared-equipment arbitration, is closed with merged code and automated evidence.

Issue #15, safe-idle heat-cool changeover, is closed with merged code and automated evidence.

Issue #21 remains open as the human-reviewed cooling shadow pilot.

Cooling must remain Dry run-only during all staging and pilot preparation.

### M7: Heat sources and changeover

M7 implementation is complete in PR #27.

Issue #16, safe source selection with break-before-make behavior, is closed with merged code and automated evidence.

Issue #17, guarded heat-pump demand and source diagnostics, is closed with merged code and automated evidence.

Issue #22 remains open as the human-reviewed source shadow and intercepted-command review.

Source-selection starts must remain Dry run only during all staging and pilot preparation.

Direct source-demand execution may be included only in the exact heating scope reviewed under issues #12 and #20.

## M8 public-beta status

M8 implementation and release automation are merged through PR #28.

The candidate version is `0.1.0` and the configured release asset is `hydronicus.zip`.

Version `0.1.0` is the initial public release with Dry run enabled by default and configurable through the UI.

The version number does not reduce the implemented topology, diagnostics, Repairs, cooling-safety, or source-reasoning capabilities.

Issue #18 is still open because a public release does not yet exist and the current candidate has not completed genuine HACS-style installation, upgrade, and rollback testing.

The obsolete synthetic installation transcript was removed during the documentation cleanup because automated output must not be mistaken for live staging evidence.

A new redacted transcript may be added only after the current candidate has actually been observed in the disposable Home Assistant environment.

Issue #19 is a separate non-author installation gate and cannot be self-approved by an agent.

## M9 stable-release status

M9 is not release-ready.

Issue #20 is the supervised physical heating pilot and waits for explicit #12 approval, accepted #18 evidence, and exact human authorization of the physical scope.

Issue #21 is the Dry run-only cooling pilot and requires representative humidity scenarios, reviewed traces, and human signoff.

Issue #22 is the shadow or intercepted-command source review and requires reviewed traces, guarded-demand evidence, and human signoff.

Issue #23 contains benchmark evidence, but its migration requirements are obsolete because no predecessor release was installed or distributed.

Issue #24 is the final human stable-release audit and must not self-approve `v1.0.0`.

## Current issue and evidence matrix

| Issue | Live state | Implementation | Automated evidence | Disposable staging | Human approval | Release dependency |
|---|---|---|---|---|---|---|
| #8 | Closed | Complete in PR #27 | Merged | Required for rollout confidence | Covered by #12 | Does not authorize physical control |
| #9 | Closed | Complete in PR #27 | Merged | Required for rollout confidence | Covered by #12 | Does not authorize physical control |
| #10 | Closed | Complete in PR #27 | Merged | Cooling shadow pilot required | Covered by #21 | Blocked from physical cooling |
| #11 | Closed | Complete in PR #27 | Merged | Reload and reconciliation observation required | No separate approval | Supports #12 and #18 |
| #12 | Open | Safety gate prepared | Required checks green | Synthetic actuator transcript required | Explicit human approval required | Blocks #20 |
| #13 | Closed | Complete in PR #27 | Merged | Repairs observation required | No separate approval | Supports #18 and #20 |
| #14 | Closed | Complete in PR #27 | Merged | Diagnostics observation required | No separate approval | Supports #20 |
| #15 | Closed | Complete in PR #27 | Merged | Cooling changeover shadow observation required | Covered by #21 | Blocks stable cooling acceptance |
| #16 | Closed | Complete in PR #27 | Merged | Source transition observation required | Covered by #22 | Blocks stable source acceptance |
| #17 | Closed | Complete in PR #27 | Merged | Guarded source-demand observation required | Covered by #22 | Blocks stable source acceptance |
| #18 | Open | Release implementation merged | Prior evidence merged but current release evidence incomplete | Required after public publication | Release acceptance is evidence-based | Primary release gate |
| #19 | Open | Packet to be prepared | Not applicable until #18 | Non-author installation required | Non-author signoff required | Depends on #18 |
| #20 | Open | Pilot procedure to be prepared | Required checks green | Supervised physical pilot only after approval | Exact human authorization required | Depends on #12 and #18 |
| #21 | Open | Cooling implementation merged | Scenario tests merged | Dry run-only pilot required | Human signoff required | Depends on #18 |
| #22 | Open | Source implementation merged | Scenario tests merged | Shadow or intercepted review required | Human signoff required | Depends on #18 |
| #23 | Open | Migration and benchmark implementation merged | Substantial evidence merged | Candidate rerun and release linkage required | No separate physical approval | Blocked by #18 |
| #24 | Open | Audit preparation allowed | Depends on accepted evidence | Depends on #19, #20, #21, #22, and #23 | Human stable-release approval required | Final gate |

## Merged automated evidence

PR #27 reports 344 tests and 91.10 percent core coverage for the M4-M7 implementation wave.

PR #28 reports 352 tests and 91.29 percent core coverage for the current public-beta candidate.

The merged public-beta evidence validates package layout, version metadata, benchmark limits, and documentation contracts.

The large synthetic benchmark covers 48 zones, 24 circuits, 12 shared valves, 6 shared pumps, 3 sources, and 96 routes.

The recorded benchmark results are approximately 8.6 milliseconds for compile, 25.2 milliseconds for evaluation, 0.218 MiB peak traced memory, 2 reconciliations, and 3 entity updates.

The recorded benchmark observed zero intercepted Home Assistant service calls and zero physical Home Assistant service calls.

These results are automated or synthetic evidence and do not satisfy non-author installation, physical pilot, cooling signoff, source review, or stable-release approval.

The first focused command stopped at the sandbox permission boundary for the existing `uv` cache path, and the first permitted retry referenced obsolete scenario filenames and collected no tests.

The corrected focused suite and full `make verify` run both passed, so neither setup error is evidence of a code failure.

## Disposable Home Assistant staging gate

The development Home Assistant instance is a shared disposable environment at the currently approved Home Assistant release.

Only one staging owner may operate the shared instance at a time.

Every pre-existing integration entry, device, entity, and test Plant is protected.

The protected Plant named `Hydronic plant` must not be deleted or destructively reconfigured.

Any legacy test Plants that appear must also remain protected unless the user explicitly approves a reversible operation.

Deploy only `config/custom_components/hydronicus`.

Never create a superseded package directory or domain.

Use synthetic entities, intercepted services, and Dry run only.

Do not bind or operate physical valves, pumps, cooling equipment, source selectors, or heat-pump demand outputs.

The staging pass must record the exact candidate SHA before deployment.

The staging pass must establish a reversible baseline before changing the disposable instance.

Python, manifest, or translation changes require a Home Assistant restart.

The staging pass must confirm that Hydronicus reports version `0.1.0`.

The staging owner must create a new issue-specific synthetic Plant without modifying existing Plants.

The staging owner must exercise creation, reconfiguration, reload, and removal.

The staging owner must exercise valve readiness, pump sequencing, required and optional actuator feedback, safe shutdown, Repairs creation and removal, and redacted diagnostics.

The staging owner must exercise condensation blocking and safe-idle heat-cool changeover without physical cooling calls.

The staging owner must exercise source recommendation, break-before-make reasoning, and guarded source demand in shadow or intercepted mode.

The staging owner must confirm that no physical service call occurred.

The staging owner must inspect Home Assistant logs for unexpected exceptions or repeated warnings.

The evidence document may be updated only after these observations occur.

Automated pytest output must not be described as live Home Assistant staging evidence.

An authorized browser session subsequently confirmed Home Assistant Core `2026.7.2`, Hydronicus `0.5.0`, the protected synthetic `Hydronic plant`, its idle shadow state, no pending Repairs, and no current log issues.

The observed Plant contained one Zone, one Circuit, one valve, and one pump backed by synthetic helpers.

The browser-control path could inspect the runtime but could not reliably activate the Home Assistant custom action controls needed for issue-specific creation, helper driving, reload, diagnostics download, or removal.

Those actions remain live-unverified and must not be recorded as product failures without a manual reproduction.

Historical logs contained an earlier translation-format error, but the recovered current runtime showed no new issue.

No Home Assistant state was mutated during that inspection.

The `0.1.0` candidate still requires deployment from an exact committed SHA and the complete issue-specific staging procedure above.

## Release candidate procedure for #18

The intended release is a normal public GitHub Release tagged `v0.1.0` and labeled as the initial Hydronicus release with configurable Dry run.

The release must contain the asset `hydronicus.zip`.

The archive must contain only `custom_components/hydronicus` and its files.

The archive must not contain cache files, test files, repository metadata, secrets, or the superseded package.

The repository-supported local preparation commands are:

```text
make verify
uv run python scripts/package_release.py --version v0.1.0 --output dist/hydronicus.zip
uv run python scripts/package_release.py --version v0.1.0 --inspect dist/hydronicus.zip
```

This audit built and inspected `dist/hydronicus.zip` successfully with the package script.

The simplified inspected archive contained 24 files and every path was under `custom_components/hydronicus`.

The GitHub release workflow is `.github/workflows/release.yml`.

The release workflow builds and inspects the archive before uploading it.

HACS and Hassfest checks must remain green for the exact release commit.

The public release action is external publication and requires explicit user approval immediately before creating the tag or release.

This prompt does not grant approval to publish `v0.1.0`.

After approval, the release evidence must include a fresh HACS-style install in a disposable configuration.

The fresh-install test must use the public repository and public documentation only.

The test must create and exercise a simulated Dry run Plant.

There is no predecessor upgrade test for the first public release.

The rollback test must confirm that the disposable configuration is recoverable from its Home Assistant backup.

The redacted transcript and workflow links must be attached to issue #18 before it is considered complete.

Official release behavior references are [HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/), [HACS publishing](https://www.hacs.dev/docs/publish/start/), [HACS integrations](https://www.hacs.dev/docs/publish/integration/), [HACS updates](https://www.hacs.xyz/docs/use/update/), and the [Home Assistant integration manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/).

HACS release selection and prerelease behavior must follow the current HACS documentation rather than an assumed tag-only workflow.

## Evidence dependency graph

The old implementation-wave launcher is retired.

The current graph is:

```text
main 6a8bb45
  |
  +--> reversible synthetic Home Assistant staging
  |       |
  |       +--> #12 approval packet, stopping before physical control
  |       |
  |       +--> release candidate package and redacted staging transcript
  |
  +--> explicit human approval to publish v0.1.0
          |
          +--> #18 public release, fresh HACS-style install, upgrade, rollback
                  |
                  +--> #19 non-author installation and signoff
                  |
                  +--> #21 cooling shadow pilot and signoff
                  |
                  +--> #22 source shadow review and signoff
                  |
                  +--> #23 final benchmark evidence audit after removing obsolete migration criteria
                  |
                  +--> #12 remains a separate human heating approval gate
                  |       |
                  |       +--> #20 supervised physical heating pilot after exact authorization
                  |
                  +--> #24 final stable-release audit after #19, #20, #21, #22, and #23
```

Preparation for #21 and #22 may occur in parallel after #18 is accepted.

Shared Home Assistant execution remains serialized under one staging owner.

Issue #23 may close only after its merged evidence is audited against the final #18 candidate and all acceptance criteria are explicitly evidenced.

Issue #20 must not begin until #12 has explicit human approval, #18 is complete, #14 remains accepted, and the user authorizes the exact supervised physical pilot.

Issue #24 must not self-approve a stable release.

## #12 heating activation review

Issue #12 is not a runtime switch and does not make version `0.1.0` control heating.

It is the human decision gate before a later change may expose or use the existing internal heating executor on one tightly scoped physical circuit.

The packet must contain the exact candidate SHA.

The packet must contain the automated results and the disposable-instance actuator transcript.

The packet must identify the exact proposed valve, pump, circuit, and observer.

The packet must identify the immediate manual rollback action.

The packet must include Plant-level Dry run fallback and ordered safe-shutdown evidence.

The packet must confirm that independent physical safety controls remain active.

The packet must list every remaining risk and unresolved observation.

The packet must stop before approving the gate, changing a real actuator, disabling Dry run for physical equipment, or issuing a physical service call.

Approval of one circuit must never be generalized to another circuit.

## #19 non-author installation packet

The packet must contain the public release URL after #18 is published.

The packet must contain public instructions only.

The packet must contain a simulated Plant checklist covering creation, reload, and removal.

The packet must contain a redacted feedback template.

The packet must request explicit success or failure signoff from the non-author installer.

An agent must not impersonate the installer or self-approve #19.

## #21 cooling shadow scenarios

The scenario set must include low, typical, and high humidity.

The scenario set must include unavailable and stale required observations.

The scenario set must include condensation-threshold crossing.

The scenario set must include shared-equipment heat-cool conflict.

The scenario set must include safe-idle changeover.

All cooling scenarios must remain Dry run-only.

The resulting traces, Home Assistant version, candidate SHA, visible explanations, and human signoff are required.

## #22 source shadow scenarios

The scenario set must include availability changes, buffer temperature, stale temperature, fallback, deterministic ties, dwell, hysteresis, and hydraulic transitions.

Source selection must remain Dry run-only or use intercepted commands.

Guarded heat-pump demand must show a valid pump path.

Reviewed traces and human signoff are required.

## #23 benchmark audit

The issue must be updated to remove predecessor migration requirements because no public predecessor exists.

The audit must confirm configured thresholds, environment, timing, memory, reconciliation counts, and entity-update evidence.

The audit must confirm `make verify` for the final #18 candidate.

The audit must not add imaginary predecessor releases or migration fixtures.

## #24 stable-release audit

The audit may prepare the release checklist, Bronze alignment report, release notes, known limitations, and open-bug review.

The audit must link accepted #19, #20, #21, #22, and #23 evidence.

The audit must confirm no open critical topology, actuator, cooling, source, or security bug remains.

The audit must be human-authored at approval time.

The audit must not request HACS default inclusion before stable release and independent use.

## Verification commands

Use `make test-core` for deterministic controller and topology behavior.

Use `make test-integration` for Home Assistant configuration, entity, lifecycle, and adapter behavior.

Use `make test-scenarios` for time-ordered plant behavior.

Use `make verify` before declaring a code or release-evidence chunk complete.

Use `git diff --check` after editing this plan or any evidence document.

Keep code complete, automated evidence complete, disposable staging complete, human approval complete, and release complete as separate claims in every handoff.

## Immediate next actions

The primary writer must implement the control-mode slices above without enabling or operating physical outputs.

The primary writer must complete core, integration, and scenario verification before requesting a candidate commit.

After implementation is committed, the primary writer must record the exact SHA and finish reversible synthetic Home Assistant staging.

The relevant evidence document may be created or updated only from observed staging results.

After synthetic and intercepted staging is complete, issue #12 requires the user's explicit approval of the exact hardware scope before any supervised physical heating pilot begins.

After the physical heating pilot and release evidence are complete, the release procedure must stop again for explicit approval immediately before publishing `v0.1.0` with asset `hydronicus.zip`.

No physical actuator, cooling device, source selector, or heat-pump demand output may be operated during this run.
