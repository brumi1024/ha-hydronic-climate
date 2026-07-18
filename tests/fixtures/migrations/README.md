# Migration fixtures

Each fixture uses the `hydronicus-config-entry` wrapper and declares its `format_version` and stored schema.

The nested `entry` object contains the stored config-entry version tuple, title, and data payload.

`public-beta-matrix.json` is the source of truth for the predecessor releases and schema fixtures that the public beta promises to migrate.

The matrix currently covers the supported `1.0` and `1.1` stored schemas.
The roadmap labels `0.3.0-beta.1` and `0.4.0-beta.1` are intentionally not listed because those releases were never distributed artifacts supported by this repository.

Fixtures use synthetic entity IDs only.
