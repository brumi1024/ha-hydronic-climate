---
name: Diagnostic bug report
about: Report a reproducible Hydronicus problem with redacted diagnostic context
title: "[Bug]: "
labels: bug
assignees: ""
---

## Before submitting

- [ ] I reproduced this with a synthetic or shadow-mode Plant.
- [ ] I removed credentials, tokens, private addresses, hostnames, and household-specific entity details.
- [ ] I confirmed that the Plant was not intended to issue physical actuator service calls.
- [ ] I searched existing issues for the same symptom.

## Summary

Describe the observed problem in one or two sentences.

## Versions

- Hydronicus version or commit:
- Home Assistant version:
- HACS version, if applicable:
- Installation method: `HACS custom repository` / `source checkout` / `other`

## Reproduction

Describe the smallest topology that reproduces the problem.
Use logical names such as `Zone A`, `Circuit A`, `Valve A`, and `Pump A`.

1.
2.
3.

## Expected behavior

Describe what Hydronicus should calculate or show.

## Actual behavior

Describe what Hydronicus calculated or showed instead.
Include the relevant Zone, Circuit, actuator, topology-preview, or explanation state.

## Topology shape

```text
Zone A -> Circuit A -> Valve A -> Pump A
```

List any shared component or route that matters to the problem.

## Sensor and virtual state

Provide only generic, redacted values.

```text
temperature: 18.0 °C
target: 21.0 °C
aggregation: mean
zone demand: on
valve request: opening
pump request: off
shadow mode: on
```

## Logs

Paste the smallest relevant redacted log excerpt.
Include the first exception and a short traceback when available.

```text
Paste redacted logs here.
```

## Additional context

Mention whether the problem survived a config-entry reload and whether it reproduces with a clean synthetic Plant.
Do not attach full backups, raw diagnostics, private configuration files, or unredacted screenshots.
