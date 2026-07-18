"""Tests for the public release package contract."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.package_release import (
    ReleaseValidationError,
    _readme_states_minimum_version,
    build_archive,
    inspect_archive,
    normalize_version,
)

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_archive_contains_only_hydronicus_integration_files(tmp_path: Path) -> None:
    """The release archive has the path shape HACS expects for content_in_root=false."""

    archive_path = tmp_path / "hydronicus.zip"
    files = build_archive(REPOSITORY_ROOT, archive_path, "v0.5.0")

    assert inspect_archive(REPOSITORY_ROOT, archive_path, "0.5.0") == files
    with ZipFile(archive_path) as archive:
        assert archive.namelist() == files
        assert all(path.startswith("custom_components/hydronicus/") for path in files)
        assert "custom_components/hydronicus/manifest.json" in archive.namelist()


@pytest.mark.parametrize("version", ["1.02.3", "1.2.3-alpha.01", "1.2", "v1.2.3.4"])
def test_normalize_version_rejects_invalid_semver(version: str) -> None:
    """Release tags must use strict semantic-version syntax."""

    with pytest.raises(ReleaseValidationError):
        normalize_version(version)


@pytest.mark.parametrize(
    "readme",
    [
        "Hydronicus requires Home Assistant 2026.7.0 or newer.",
        "The minimum Home Assistant version declared by this repository is `2026.7.0`.",
    ],
)
def test_readme_minimum_version_check_allows_clear_prose(readme: str) -> None:
    """Metadata validation must not depend on one exact documentation sentence."""

    assert _readme_states_minimum_version(readme, "2026.7.0")


def test_readme_minimum_version_check_rejects_unrelated_version() -> None:
    """A README must state the configured version, not merely mention Home Assistant."""

    assert not _readme_states_minimum_version(
        "This integration supports Home Assistant.", "2026.7.0"
    )


def test_public_beta_evidence_contract_is_documented_without_legacy_package(
    tmp_path: Path,
) -> None:
    """Release evidence names the disposable boundary and excludes the legacy package."""
    evidence = (
        REPOSITORY_ROOT / "docs" / "evidence" / "public-beta-installation-transcript.md"
    ).read_text(encoding="utf-8")
    files = build_archive(REPOSITORY_ROOT, tmp_path / "hydronicus.zip", "0.5.0")

    assert "synthetic" in evidence
    assert "shadow mode" in evidence
    assert "independent human installation" in evidence
    assert all("hydronic_climate" not in path for path in files)
