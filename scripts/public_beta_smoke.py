"""Validate the public-beta package, upgrade matrix, and rollback contract."""

from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

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

PUBLIC_BETA_VERSION = "0.5.0"
MIGRATIONS_ROOT = Path("tests") / "fixtures" / "migrations"
MATRIX_PATH = MIGRATIONS_ROOT / "public-beta-matrix.json"
EXPECTED_ENTRY_VERSIONS = {(1, 0), (1, 1)}


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReleaseValidationError(f"Expected a JSON object in {path}")
    return value


def _assert_uuid(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise ReleaseValidationError(f"{label} is not a UUID")
    try:
        UUID(value)
    except ValueError as error:
        raise ReleaseValidationError(f"{label} is not a UUID") from error


def _validate_upgrade_matrix(root: Path) -> None:
    matrix = _load_json(root / MATRIX_PATH)
    if matrix.get("format") != "hydronicus-public-beta-upgrade-matrix":
        raise ReleaseValidationError("Upgrade matrix format is invalid")
    if matrix.get("format_version") != 1:
        raise ReleaseValidationError("Upgrade matrix version is invalid")
    if matrix.get("target_release") != PUBLIC_BETA_VERSION:
        raise ReleaseValidationError("Upgrade matrix target does not match the public beta")

    predecessors = matrix.get("supported_predecessors")
    if not isinstance(predecessors, list) or not predecessors:
        raise ReleaseValidationError("Upgrade matrix has no supported predecessor releases")
    seen_versions: set[tuple[int, int]] = set()
    for predecessor in predecessors:
        if not isinstance(predecessor, dict):
            raise ReleaseValidationError("Upgrade matrix predecessor must be an object")
        release = predecessor.get("release")
        schema = predecessor.get("schema")
        fixture_name = predecessor.get("fixture")
        raw_versions = predecessor.get("config_entry_versions")
        if not isinstance(release, str) or not release:
            raise ReleaseValidationError("Upgrade matrix predecessor has no release")
        if not isinstance(schema, str) or not schema:
            raise ReleaseValidationError(f"Upgrade matrix entry for {release} has no schema")
        if not isinstance(fixture_name, str) or not fixture_name:
            raise ReleaseValidationError(f"Upgrade matrix entry for {release} has no fixture")
        if not isinstance(raw_versions, list):
            raise ReleaseValidationError(f"Upgrade matrix entry for {release} has no versions")

        fixture = _load_json(root / MIGRATIONS_ROOT / fixture_name)
        if fixture.get("format") != "hydronicus-config-entry":
            raise ReleaseValidationError(f"Migration fixture {fixture_name} has invalid format")
        if fixture.get("release") != release:
            raise ReleaseValidationError(f"Migration fixture {fixture_name} has the wrong release")
        if fixture.get("schema") != schema:
            raise ReleaseValidationError(f"Migration fixture {fixture_name} has the wrong schema")
        if fixture.get("format_version") != 1:
            raise ReleaseValidationError(f"Migration fixture {fixture_name} has the wrong format")
        entry = fixture.get("entry")
        if not isinstance(entry, dict):
            raise ReleaseValidationError(f"Migration fixture {fixture_name} has no entry")
        data = entry.get("data")
        if not isinstance(data, dict):
            raise ReleaseValidationError(f"Migration fixture {fixture_name} has no data")
        entry_version = entry.get("version"), entry.get("minor_version")
        if entry_version not in {
            (raw_version[0], raw_version[1])
            for raw_version in raw_versions
            if isinstance(raw_version, list) and len(raw_version) == 2
        }:
            raise ReleaseValidationError(
                f"Migration fixture {fixture_name} declares an uncovered entry version"
            )
        topology = data.get("topology")
        if not isinstance(topology, dict):
            raise ReleaseValidationError(f"Migration fixture {fixture_name} has no topology")

        for key in ("plant_id",):
            _assert_uuid(data.get(key), f"{fixture_name} {key}")
        for object_key in ("zones", "valves", "pumps", "circuits", "routes"):
            objects = topology.get(object_key)
            if not isinstance(objects, list):
                raise ReleaseValidationError(f"{fixture_name} has no {object_key}")
            for index, item in enumerate(objects):
                if not isinstance(item, dict):
                    raise ReleaseValidationError(f"{fixture_name} {object_key}[{index}] is invalid")
                _assert_uuid(item.get("id"), f"{fixture_name} {object_key}[{index}].id")

        for raw_version in raw_versions:
            if (
                not isinstance(raw_version, list)
                or len(raw_version) != 2
                or not all(isinstance(value, int) for value in raw_version)
            ):
                raise ReleaseValidationError(f"Invalid config-entry version in {fixture_name}")
            seen_versions.add((raw_version[0], raw_version[1]))

        entity_ids = {
            value for value in _walk_values(data) if isinstance(value, str) and "." in value
        }
        if not entity_ids or not all(
            value.startswith(
                (
                    "binary_sensor.synthetic_",
                    "select.synthetic_",
                    "sensor.synthetic_",
                    "switch.synthetic_",
                    "valve.synthetic_",
                )
            )
            for value in entity_ids
        ):
            raise ReleaseValidationError(
                f"Migration fixture {fixture_name} contains a non-synthetic entity id"
            )

    if seen_versions != EXPECTED_ENTRY_VERSIONS:
        raise ReleaseValidationError(
            f"Upgrade matrix versions {sorted(seen_versions)} do not cover "
            f"{sorted(EXPECTED_ENTRY_VERSIONS)}"
        )


def _walk_values(value: object) -> list[object]:
    """Return all nested fixture values for household-binding checks."""
    if isinstance(value, dict):
        return [item for child in value.values() for item in _walk_values(child)]
    if isinstance(value, list):
        return [item for child in value for item in _walk_values(child)]
    return [value]


def _validate_documentation(root: Path) -> None:
    readme = (root / "README.md").read_text(encoding="utf-8")
    rollback = (root / "docs" / "upgrade-and-rollback.md").read_text(encoding="utf-8")
    required_readme = (
        "Custom repositories",
        "First simulated Plant",
        "shadow mode",
        "docs/configuration.md",
    )
    required_rollback = (
        "0.1.0-alpha.1",
        "Data-version limit",
        "forward-only",
        "hydronic_climate",
        "restore the complete Home Assistant backup",
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

    _validate_upgrade_matrix(root)
    _validate_documentation(root)
    return [
        "HACS archive contains only custom_components/hydronicus",
        "packaged manifest loads domain hydronicus",
        "supported predecessor upgrade matrix covers entry versions (1,0) and (1,1)",
        "UUID-bearing migration fixtures and rollback data-version guidance are present",
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
