# Migration fixtures

Each fixture uses the `hydronicus-config-entry` wrapper and declares its `format_version`.

The nested `entry` object contains the stored config-entry version tuple, title, and data payload.

Future fixtures may add wrapper metadata without changing the entry payload contract.

Fixtures use synthetic entity IDs only.
