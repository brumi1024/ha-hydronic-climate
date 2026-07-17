"""Versioned, atomic migration of persisted Hydronicus topology data."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .const import CONFIG_ENTRY_MINOR_VERSION, CONFIG_ENTRY_VERSION
from .core.configuration import StoredTopologyError, plant_configuration_from_entry_data
from .core.topology import TopologyValidationError, compile_topology

type ConfigEntryVersion = tuple[int, int]
type StoredEntryData = dict[str, Any]
type MigrationFunction = Callable[[Mapping[str, Any]], StoredEntryData]

CURRENT_CONFIG_ENTRY_VERSION: ConfigEntryVersion = (
    CONFIG_ENTRY_VERSION,
    CONFIG_ENTRY_MINOR_VERSION,
)


class ConfigEntryMigrationError(ValueError):
    """Raised when a persisted entry cannot be migrated safely."""


@dataclass(frozen=True, slots=True)
class MigrationStep:
    """One deterministic edge in the persisted-schema migration graph."""

    source: ConfigEntryVersion
    target: ConfigEntryVersion
    migrate: MigrationFunction


def _validate_entry_data(data: Mapping[str, Any]) -> None:
    """Validate the migrated topology before it can be written to Home Assistant."""
    try:
        configuration = plant_configuration_from_entry_data(data)
        compile_topology(configuration)
    except (StoredTopologyError, TopologyValidationError) as error:
        raise ConfigEntryMigrationError(str(error)) from error


def _migrate_v1_to_v2(data: Mapping[str, Any]) -> StoredEntryData:
    """Adopt explicit config-entry versioning without changing topology identity."""
    # The v1 topology is already the current UUID-backed persisted shape.
    # Keeping this first edge data-preserving makes the version bump safe while
    # leaving a named seam for the next schema change.
    return deepcopy(dict(data))


MIGRATION_DISPATCH: dict[ConfigEntryVersion, MigrationStep] = {
    (1, 1): MigrationStep(
        source=(1, 1),
        target=CURRENT_CONFIG_ENTRY_VERSION,
        migrate=_migrate_v1_to_v2,
    ),
}


def migrate_entry_data(
    data: Mapping[str, Any],
    *,
    version: int,
    minor_version: int,
    target_version: ConfigEntryVersion = CURRENT_CONFIG_ENTRY_VERSION,
) -> StoredEntryData:
    """Migrate entry data through registered schema steps atomically.

    Each step receives a deep copy and its result is validated before it is
    passed to the next step.  The caller may therefore update the real config
    entry only after this function returns successfully.
    """
    if not isinstance(data, Mapping):
        raise ConfigEntryMigrationError("Config-entry data must be a mapping.")
    source_version = (version, minor_version)
    if source_version > target_version:
        raise ConfigEntryMigrationError(
            f"Config-entry version {source_version} is newer than supported {target_version}."
        )
    migrated = deepcopy(dict(data))
    while source_version < target_version:
        try:
            step = MIGRATION_DISPATCH[source_version]
        except KeyError as error:
            raise ConfigEntryMigrationError(
                f"No migration registered from config-entry version {source_version}."
            ) from error
        if step.source != source_version or step.target <= source_version:
            raise ConfigEntryMigrationError(
                f"Invalid migration dispatch for config-entry version {source_version}."
            )
        try:
            candidate = step.migrate(deepcopy(migrated))
        except Exception as error:
            raise ConfigEntryMigrationError(
                f"Migration from config-entry version {source_version} failed."
            ) from error
        if not isinstance(candidate, dict):
            raise ConfigEntryMigrationError(
                f"Migration from config-entry version {source_version} did not return an object."
            )
        _validate_entry_data(candidate)
        migrated = deepcopy(candidate)
        source_version = step.target
    if source_version != target_version:
        raise ConfigEntryMigrationError(
            f"Migration stopped at {source_version}, expected {target_version}."
        )
    return migrated
