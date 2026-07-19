"""Validate the public-beta package and installation documentation."""

from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from pathlib import Path

try:
    from scripts.package_release import (
        INTEGRATION_ROOT,
        ReleaseValidationError,
        build_archive,
        inspect_archive,
        validate_repository,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from package_release import (
        INTEGRATION_ROOT,
        ReleaseValidationError,
        build_archive,
        inspect_archive,
        validate_repository,
    )

PUBLIC_BETA_VERSION = "0.1.0"


def _validate_documentation(root: Path) -> None:
    readme = (root / "README.md").read_text(encoding="utf-8")
    rollback = (root / "docs" / "upgrade-and-rollback.md").read_text(encoding="utf-8")
    required_readme = (
        "Custom repositories",
        "First simulated Plant",
        "Dry run",
        "docs/configuration.md",
    )
    required_rollback = (
        "hydronic_climate",
        "Home Assistant backup",
    )
    for phrase in required_readme:
        if phrase not in readme:
            raise ReleaseValidationError(f"README is missing public-install guidance: {phrase}")
    for phrase in required_rollback:
        if phrase not in rollback:
            raise ReleaseValidationError(f"Rollback guide is missing: {phrase}")


def validate_public_beta(root: Path) -> list[str]:
    """Run all repository-local public-beta checks and return passed checks."""
    metadata = validate_repository(root)
    if metadata.version != PUBLIC_BETA_VERSION:
        raise ReleaseValidationError(
            f"Manifest version {metadata.version} is not public beta {PUBLIC_BETA_VERSION}"
        )
    if (root / "custom_components" / "hydronic_climate").exists():
        raise ReleaseValidationError("Legacy hydronic_climate package exists")
    with tempfile.TemporaryDirectory(prefix="hydronicus-public-beta-") as temporary_directory:
        archive_path = Path(temporary_directory) / "hydronicus.zip"
        files = build_archive(root, archive_path, PUBLIC_BETA_VERSION)
        inspected_files = inspect_archive(root, archive_path, PUBLIC_BETA_VERSION)
        if files != inspected_files:
            raise ReleaseValidationError("Release archive inspection is not deterministic")
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read(f"{INTEGRATION_ROOT}/manifest.json"))
            if manifest.get("domain") != "hydronicus":
                raise ReleaseValidationError("Packaged manifest does not use domain hydronicus")
            if any(
                name.startswith("custom_components/hydronic_climate/")
                for name in archive.namelist()
            ):
                raise ReleaseValidationError("Release archive contains the legacy domain")

    _validate_documentation(root)
    return [
        "HACS archive contains only custom_components/hydronicus",
        "packaged manifest loads domain hydronicus",
        "legacy hydronic_climate package is absent",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        for check in validate_public_beta(args.root.resolve()):
            print(f"PASS {check}")
    except (OSError, json.JSONDecodeError, ReleaseValidationError) as error:
        print(f"FAIL public-beta validation: {error}")
        return 1
    print("PASS public-beta repository checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
