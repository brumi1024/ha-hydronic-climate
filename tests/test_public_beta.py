"""Repository-local public-beta release contract tests."""

from __future__ import annotations

from pathlib import Path

from scripts.public_beta_smoke import validate_public_beta

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_public_beta_package_and_documentation_contract() -> None:
    """The HACS archive and public installation documentation agree."""
    checks = validate_public_beta(REPOSITORY_ROOT)

    assert len(checks) == 3
