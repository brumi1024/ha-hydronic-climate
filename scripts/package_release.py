"""Validate Hydronicus release metadata and build its HACS archive."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = "brumi1024/ha-hydronicus"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"
DOMAIN = "hydronicus"
INTEGRATION_NAME = "Hydronicus"
MINIMUM_HOME_ASSISTANT = "2026.7.0"
ARCHIVE_NAME = "hydronicus.zip"
INTEGRATION_ROOT = Path("custom_components") / DOMAIN
FRONTEND_PACKAGE = Path("frontend") / "package.json"
FRONTEND_BUNDLE = INTEGRATION_ROOT / "frontend" / "hydronicus-plant-card.js"
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
IGNORED_PARTS = {"__pycache__"}


class ReleaseValidationError(ValueError):
    """Raised when repository release metadata or an archive is invalid."""


@dataclass(frozen=True)
class ReleaseMetadata:
    """Release values shared by the manifest, HACS metadata, and archive."""

    version: str
    minimum_home_assistant: str


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(f"Could not read JSON metadata from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseValidationError(f"Expected a JSON object in {path}")
    return value


def _string_field(data: dict[str, object], key: str, source: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ReleaseValidationError(f"{source} must define a non-empty string {key!r}")
    return value


def normalize_version(value: str, *, source: str = "version") -> str:
    """Return a SemVer value without an optional Git tag prefix."""

    normalized = value.removeprefix("v")
    if not SEMVER_PATTERN.fullmatch(normalized):
        raise ReleaseValidationError(f"{source} must be a semantic version, got {value!r}")
    return normalized


def _readme_states_minimum_version(readme: str, version: str) -> bool:
    """Return whether README prose states the supported minimum version."""

    pattern = re.compile(
        rf"(?i)(?:minimum|requires|support(?:ed)?)\S*(?:\s+\S+){{0,12}}"
        rf"\s*{re.escape(version)}"
    )
    return pattern.search(readme) is not None


def validate_repository(root: Path) -> ReleaseMetadata:
    """Validate the repository metadata used by a Hydronicus release."""

    manifest_path = root / INTEGRATION_ROOT / "manifest.json"
    hacs_path = root / "hacs.json"
    readme_path = root / "README.md"
    manifest = _load_json(manifest_path)
    hacs = _load_json(hacs_path)

    if _string_field(manifest, "domain", "manifest.json") != DOMAIN:
        raise ReleaseValidationError(f"manifest.json domain must be {DOMAIN!r}")
    if _string_field(manifest, "name", "manifest.json") != INTEGRATION_NAME:
        raise ReleaseValidationError(f"manifest.json name must be {INTEGRATION_NAME!r}")
    if _string_field(manifest, "documentation", "manifest.json") != REPOSITORY_URL:
        raise ReleaseValidationError(
            "manifest.json documentation URL does not match the repository"
        )
    if _string_field(manifest, "issue_tracker", "manifest.json") != f"{REPOSITORY_URL}/issues":
        raise ReleaseValidationError(
            "manifest.json issue tracker URL does not match the repository"
        )
    version = normalize_version(
        _string_field(manifest, "version", "manifest.json"), source="manifest version"
    )

    frontend_package_path = root / FRONTEND_PACKAGE
    if not frontend_package_path.is_file():
        raise ReleaseValidationError(f"Frontend package metadata is missing: {FRONTEND_PACKAGE}")
    frontend_package = _load_json(frontend_package_path)
    frontend_version = normalize_version(
        _string_field(frontend_package, "version", "frontend/package.json"),
        source="frontend version",
    )
    if frontend_version != version:
        raise ReleaseValidationError(
            "frontend/package.json version does not match manifest version"
        )
    frontend_bundle_path = root / FRONTEND_BUNDLE
    if not frontend_bundle_path.is_file():
        raise ReleaseValidationError(f"Built frontend bundle is missing: {FRONTEND_BUNDLE}")
    frontend_bundle = frontend_bundle_path.read_text(encoding="utf-8")
    if re.search(rf'version\s*:\s*"{re.escape(version)}"', frontend_bundle) is None:
        raise ReleaseValidationError(
            "Built frontend bundle does not contain the manifest version marker"
        )

    if _string_field(hacs, "name", "hacs.json") != INTEGRATION_NAME:
        raise ReleaseValidationError(f"hacs.json name must be {INTEGRATION_NAME!r}")
    if hacs.get("content_in_root") is not False:
        raise ReleaseValidationError("hacs.json content_in_root must be false")
    if hacs.get("zip_release") is not True:
        raise ReleaseValidationError("hacs.json zip_release must be true")
    if _string_field(hacs, "filename", "hacs.json") != ARCHIVE_NAME:
        raise ReleaseValidationError(f"hacs.json filename must be {ARCHIVE_NAME!r}")
    minimum_home_assistant = _string_field(hacs, "homeassistant", "hacs.json")
    if minimum_home_assistant != MINIMUM_HOME_ASSISTANT:
        raise ReleaseValidationError(
            "hacs.json homeassistant minimum does not match the repository release contract"
        )
    readme = readme_path.read_text(encoding="utf-8")
    if not _readme_states_minimum_version(readme, minimum_home_assistant):
        raise ReleaseValidationError(
            "README.md does not state the HACS minimum Home Assistant version"
        )

    files = list(_integration_files(root))
    required = {
        INTEGRATION_ROOT / "__init__.py",
        INTEGRATION_ROOT / "manifest.json",
        INTEGRATION_ROOT / "strings.json",
        INTEGRATION_ROOT / "translations" / "en.json",
    }
    missing = sorted(path.as_posix() for path in required if path not in files)
    if missing:
        raise ReleaseValidationError(
            f"Integration package is missing required files: {', '.join(missing)}"
        )
    return ReleaseMetadata(version=version, minimum_home_assistant=minimum_home_assistant)


def _integration_files(root: Path) -> Iterator[Path]:
    integration_path = root / INTEGRATION_ROOT
    if not integration_path.is_dir():
        raise ReleaseValidationError(f"Missing integration directory: {INTEGRATION_ROOT}")
    for path in sorted(integration_path.rglob("*")):
        if any(part in IGNORED_PARTS for part in path.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ReleaseValidationError(f"Symlinks are not allowed in the release package: {path}")
        if path.is_file():
            yield path.relative_to(root)


def _archive_info(path: Path) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def build_archive(root: Path, output: Path, expected_version: str | None = None) -> list[str]:
    """Build a deterministic archive containing only the Hydronicus integration."""

    metadata = validate_repository(root)
    if expected_version is not None and normalize_version(expected_version) != metadata.version:
        raise ReleaseValidationError(
            f"Release version {expected_version!r} does not match "
            f"manifest version {metadata.version!r}"
        )
    files = list(_integration_files(root))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative_path in files:
            source = root / relative_path
            archive.writestr(_archive_info(relative_path), source.read_bytes())
    return [path.as_posix() for path in files]


def inspect_archive(
    root: Path, archive_path: Path, expected_version: str | None = None
) -> list[str]:
    """Verify archive paths and metadata against the repository package contract."""

    metadata = validate_repository(root)
    if expected_version is not None and normalize_version(expected_version) != metadata.version:
        raise ReleaseValidationError(
            f"Release version {expected_version!r} does not match "
            f"manifest version {metadata.version!r}"
        )
    expected_files = [path.as_posix() for path in _integration_files(root)]
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ReleaseValidationError("Release archive contains duplicate paths")
            if names != expected_files:
                missing = sorted(set(expected_files) - set(names))
                unexpected = sorted(set(names) - set(expected_files))
                details = []
                if missing:
                    details.append(f"missing={missing}")
                if unexpected:
                    details.append(f"unexpected={unexpected}")
                raise ReleaseValidationError(
                    "Release archive contents differ: " + ", ".join(details)
                )
            if any(not name.startswith(f"{INTEGRATION_ROOT.as_posix()}/") for name in names):
                raise ReleaseValidationError(
                    "Release archive contains files outside custom_components/hydronicus"
                )
            packaged_manifest = json.loads(
                archive.read(f"{INTEGRATION_ROOT.as_posix()}/manifest.json")
            )
            if not isinstance(packaged_manifest, dict):
                raise ReleaseValidationError("Packaged manifest must be a JSON object")
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(
            f"Could not inspect release archive {archive_path}: {exc}"
        ) from exc
    packaged_version = normalize_version(
        _string_field(packaged_manifest, "version", "packaged manifest")
    )
    if packaged_version != metadata.version:
        raise ReleaseValidationError("Packaged manifest version does not match repository metadata")
    return names


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--version", help="Release version or v-prefixed release tag to validate")
    parser.add_argument("--output", type=Path, default=Path("dist") / ARCHIVE_NAME)
    parser.add_argument(
        "--inspect", type=Path, help="Inspect an existing archive instead of building one"
    )
    parser.add_argument(
        "--print-version", action="store_true", help="Print the manifest version and exit"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and inspect a temporary archive without writing the requested output",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        metadata = validate_repository(root)
        expected_version = args.version or metadata.version
        if args.print_version:
            if args.version:
                raise ReleaseValidationError("--print-version cannot be combined with --version")
            print(metadata.version)
            return 0
        if args.inspect and args.dry_run:
            raise ReleaseValidationError("--inspect cannot be combined with --dry-run")
        if args.inspect:
            names = inspect_archive(root, args.inspect.resolve(), expected_version)
            print(f"Validated Hydronicus {metadata.version} archive: {args.inspect}")
        elif args.dry_run:
            with tempfile.TemporaryDirectory(prefix="hydronicus-release-") as temporary_directory:
                temporary_archive = Path(temporary_directory) / ARCHIVE_NAME
                names = build_archive(root, temporary_archive, expected_version)
                inspect_archive(root, temporary_archive, expected_version)
            print(f"Dry-run validated Hydronicus {metadata.version} archive")
        else:
            names = build_archive(root, args.output.resolve(), expected_version)
            inspect_archive(root, args.output.resolve(), expected_version)
            print(f"Built and inspected Hydronicus {metadata.version} archive: {args.output}")
        print("Package contents:")
        print("\n".join(names))
        return 0
    except (OSError, ReleaseValidationError) as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
