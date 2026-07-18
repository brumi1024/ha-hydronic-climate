"""Repository-local public-beta release and rollback contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.hydronicus.migration import ConfigEntryMigrationError, migrate_entry_data
from scripts.public_beta_smoke import validate_public_beta

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_public_beta_package_and_documentation_contract() -> None:
    """The HACS archive, upgrade matrix, and public rollback guide agree."""
    checks = validate_public_beta(REPOSITORY_ROOT)

    assert len(checks) == 5


def test_public_beta_rollback_rejects_in_place_downgrade() -> None:
    """The forward-only migration contract prevents unsafe storage downgrades."""
    with pytest.raises(ConfigEntryMigrationError, match="newer than supported"):
        migrate_entry_data(
            {"name": "Synthetic plant", "plant_id": "synthetic-plant"},
            version=2,
            minor_version=1,
            target_version=(1, 1),
        )
