from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import uuid
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote


MAX_SBOM_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_FILES = 5_000
SUPPORTED_CYCLONEDX_VERSIONS = {"1.4", "1.5", "1.6"}
SOURCE_REQUIRED_FILES = {
    ".github/workflows/ci.yml",
    "Dockerfile",
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "web/package-lock.json",
    "web/package.json",
}
PRIVATE_KEY_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
SHA256_LINE = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")


class SupplyChainError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _merge_unique(left: list[Any], right: list[Any]) -> list[Any]:
    result = copy.deepcopy(left)
    seen = {_canonical(item) for item in result}
    for item in right:
        marker = _canonical(item)
        if marker not in seen:
            result.append(copy.deepcopy(item))
            seen.add(marker)
    return result


def _merge_properties(left: list[Any], right: list[Any]) -> list[dict[str, str]]:
    values: dict[str, set[str]] = {}
    for item in [*left, *right]:
        if not isinstance(item, dict):
            raise SupplyChainError("CycloneDX component property must be an object")
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise SupplyChainError("CycloneDX component property must be text")
        values.setdefault(name, set()).add(value)

    merged: list[dict[str, str]] = []
    for name in sorted(values):
        current = sorted(values[name])
        if len(current) == 1:
            merged.append({"name": name, "value": current[0]})
        elif name == "cdx:npm:package:path":
            merged.append(
                {
                    "name": "contentflow:npm:package:paths",
                    "value": json.dumps(
                        current, ensure_ascii=False, separators=(",", ":")
                    ),
                }
            )
        else:
            raise SupplyChainError(
                f"duplicate CycloneDX property has conflicting values: {name}"
            )
    return merged


def _merge_component(base: dict[str, Any], incoming: dict[str, Any]) -> None:
    identity_fields = ("type", "name", "version", "group", "purl")
    for field in identity_fields:
        if base.get(field) != incoming.get(field):
            raise SupplyChainError(
                f"duplicate bom-ref has conflicting {field}: {base.get('bom-ref')}"
            )

    scope_priority = {"excluded": 0, "optional": 1, "required": 2}
    scopes = [value for value in (base.get("scope"), incoming.get("scope")) if value]
    if scopes:
        if any(value not in scope_priority for value in scopes):
            raise SupplyChainError("duplicate component has an unsupported scope")
        base["scope"] = max(scopes, key=scope_priority.__getitem__)

    collection_fields = ("externalReferences", "hashes", "licenses")
    for field in collection_fields:
        if field in incoming:
            base[field] = _merge_unique(base.get(field, []), incoming[field])

    if "properties" in base or "properties" in incoming:
        base["properties"] = _merge_properties(
            base.get("properties", []), incoming.get("properties", [])
        )

    handled = {
        "bom-ref",
        "scope",
        "properties",
        *identity_fields,
        *collection_fields,
    }
    for field, value in incoming.items():
        if field in handled:
            continue
        if field not in base:
            base[field] = copy.deepcopy(value)
        elif base[field] != value:
            raise SupplyChainError(
                f"duplicate bom-ref has conflicting {field}: {base.get('bom-ref')}"
            )


def normalize_cyclonedx(
    document: dict[str, Any], *, root_name: str | None = None
) -> dict[str, Any]:
    if document.get("bomFormat") != "CycloneDX":
        raise SupplyChainError("only CycloneDX documents can be normalized")

    normalized = copy.deepcopy(document)
    components = normalized.get("components")
    if not isinstance(components, list):
        raise SupplyChainError("CycloneDX components must be a list")

    by_ref: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            raise SupplyChainError("CycloneDX component must be an object")
        reference = component.get("bom-ref")
        if not isinstance(reference, str) or not reference:
            raise SupplyChainError("CycloneDX component is missing bom-ref")
        if reference not in by_ref:
            by_ref[reference] = copy.deepcopy(component)
            order.append(reference)
        else:
            _merge_component(by_ref[reference], component)
    normalized["components"] = [by_ref[reference] for reference in order]

    dependencies = normalized.get("dependencies", [])
    if not isinstance(dependencies, list):
        raise SupplyChainError("CycloneDX dependencies must be a list")
    dependency_map: dict[str, list[str]] = {}
    dependency_order: list[str] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise SupplyChainError("CycloneDX dependency must be an object")
        reference = dependency.get("ref")
        depends_on = dependency.get("dependsOn", [])
        if not isinstance(reference, str) or not isinstance(depends_on, list):
            raise SupplyChainError("CycloneDX dependency has an invalid shape")
        if not all(isinstance(item, str) for item in depends_on):
            raise SupplyChainError("CycloneDX dependency references must be text")
        if reference not in dependency_map:
            dependency_map[reference] = []
            dependency_order.append(reference)
        dependency_map[reference] = sorted(
            set(dependency_map[reference]).union(depends_on)
        )
    normalized["dependencies"] = [
        {"ref": reference, "dependsOn": dependency_map[reference]}
        for reference in dependency_order
    ]

    if root_name:
        metadata = normalized.get("metadata")
        root = metadata.get("component") if isinstance(metadata, dict) else None
        if not isinstance(root, dict):
            raise SupplyChainError("CycloneDX metadata.component is required")
        root["name"] = root_name

    return normalized


def _git(repository_root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    command = [
        "git",
        "-c",
        f"safe.directory={repository_root.as_posix()}",
        "-C",
        str(repository_root),
        *arguments,
    ]
    try:
        return subprocess.run(command, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", b"")
        message = detail.decode("utf-8", errors="replace").strip()
        raise SupplyChainError(f"git command failed: {message or error}") from error


def repository_commit(repository_root: Path) -> str:
    return _git(repository_root, "rev-parse", "HEAD").stdout.decode("ascii").strip()


def tracked_files(repository_root: Path, commit: str) -> set[str]:
    output = _git(repository_root, "ls-tree", "-r", "--name-only", "-z", commit).stdout
    return {item.decode("utf-8") for item in output.split(b"\0") if item}


def _source_archive_bytes(repository_root: Path, commit: str) -> bytes:
    prefix = f"contentflow-{commit}/"
    tar_bytes = _git(
        repository_root,
        "archive",
        "--format=tar",
        f"--prefix={prefix}",
        commit,
    ).stdout
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0
    ) as compressed:
        compressed.write(tar_bytes)
    return output.getvalue()


def build_source_archive(
    repository_root: Path, expected_commit: str, output_path: Path
) -> None:
    repository_root = repository_root.resolve()
    if repository_commit(repository_root) != expected_commit:
        raise SupplyChainError("checked-out commit does not match expected commit")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_source_archive_bytes(repository_root, expected_commit))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_hash_manifest(
    directory: Path, names: Iterable[str], output_name: str
) -> None:
    directory = directory.resolve()
    selected = sorted(set(names))
    if not selected or output_name in selected:
        raise SupplyChainError(
            "manifest inputs are empty or include the manifest itself"
        )
    lines: list[str] = []
    for name in selected:
        if Path(name).name != name:
            raise SupplyChainError("manifest entries must be plain file names")
        path = directory / name
        if not path.is_file():
            raise SupplyChainError(f"manifest input is missing: {name}")
        lines.append(f"{_hash_file(path)}  {name}")
    (directory / output_name).write_text("\n".join(lines) + "\n", encoding="ascii")


def verify_hash_manifest(manifest_path: Path, expected_paths: Iterable[Path]) -> None:
    expected = {path.name: path for path in expected_paths}
    actual: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="ascii").splitlines():
        match = SHA256_LINE.fullmatch(line)
        if not match:
            raise SupplyChainError("SHA256SUMS contains an invalid line")
        digest, name = match.groups()
        if Path(name).name != name or name in actual:
            raise SupplyChainError("SHA256SUMS contains an unsafe or duplicate name")
        actual[name] = digest
    if set(actual) != set(expected):
        raise SupplyChainError("SHA256SUMS does not name exactly the expected files")
    for name, path in expected.items():
        if _hash_file(path) != actual[name]:
            raise SupplyChainError(f"SHA-256 mismatch: {name}")


def _load_json(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    if size <= 0 or size > MAX_SBOM_BYTES:
        raise SupplyChainError(f"SBOM size is outside the allowed range: {path.name}")
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SupplyChainError(f"SBOM is not valid UTF-8 JSON: {path.name}") from error
    if not isinstance(document, dict):
        raise SupplyChainError("SBOM root must be an object")
    return document


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def validate_cyclonedx(
    document: dict[str, Any],
    *,
    required_name: str,
    required_version: str,
    minimum_components: int,
    require_metadata_root: bool,
) -> int:
    if document.get("bomFormat") != "CycloneDX":
        raise SupplyChainError("SBOM must use the CycloneDX format")
    if document.get("specVersion") not in SUPPORTED_CYCLONEDX_VERSIONS:
        raise SupplyChainError("SBOM uses an unsupported CycloneDX version")
    try:
        serial = document["serialNumber"]
        if not isinstance(serial, str) or not serial.startswith("urn:uuid:"):
            raise ValueError
        uuid.UUID(serial.removeprefix("urn:uuid:"))
    except (KeyError, ValueError, AttributeError) as error:
        raise SupplyChainError("SBOM serialNumber must be a UUID URN") from error

    components = document.get("components")
    if not isinstance(components, list) or len(components) < minimum_components:
        raise SupplyChainError("SBOM component inventory is unexpectedly small")

    references: set[str] = set()
    purls: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for component in components:
        if not isinstance(component, dict):
            raise SupplyChainError("SBOM component must be an object")
        if not all(
            isinstance(component.get(field), str) and component[field]
            for field in ("type", "name", "version", "bom-ref")
        ):
            raise SupplyChainError("SBOM component identity is incomplete")
        reference = component["bom-ref"]
        if reference in references:
            raise SupplyChainError(f"duplicate CycloneDX bom-ref: {reference}")
        references.add(reference)
        purl = component.get("purl")
        if purl:
            if not isinstance(purl, str) or purl in purls:
                raise SupplyChainError(f"duplicate or invalid CycloneDX purl: {purl}")
            purls.add(purl)
        candidates.append(component)

    metadata = document.get("metadata")
    root = metadata.get("component") if isinstance(metadata, dict) else None
    if root is not None:
        if not isinstance(root, dict):
            raise SupplyChainError("SBOM metadata.component must be an object")
        root_reference = root.get("bom-ref")
        if not isinstance(root_reference, str) or not root_reference:
            raise SupplyChainError("SBOM root component is missing bom-ref")
        references.add(root_reference)
        candidates.append(root)
    elif require_metadata_root:
        raise SupplyChainError("SBOM metadata.component is required")

    if not any(
        item.get("name") == required_name and item.get("version") == required_version
        for item in candidates
    ):
        raise SupplyChainError(
            f"SBOM is missing {required_name} version {required_version}"
        )

    dependency_refs: set[str] = set()
    dependencies = document.get("dependencies", [])
    if not isinstance(dependencies, list):
        raise SupplyChainError("SBOM dependencies must be a list")
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise SupplyChainError("SBOM dependency must be an object")
        reference = dependency.get("ref")
        depends_on = dependency.get("dependsOn", [])
        if not isinstance(reference, str) or reference in dependency_refs:
            raise SupplyChainError("SBOM dependency ref is invalid or duplicated")
        if not isinstance(depends_on, list) or len(depends_on) != len(set(depends_on)):
            raise SupplyChainError("SBOM dependsOn must be a unique list")
        if reference not in references or any(
            item not in references for item in depends_on
        ):
            raise SupplyChainError("SBOM dependency graph contains an unknown bom-ref")
        dependency_refs.add(reference)

    vulnerabilities = document.get("vulnerabilities", [])
    if vulnerabilities not in (None, []):
        raise SupplyChainError("SBOM contains known vulnerabilities")
    for value in _iter_strings(document):
        normalized = value.replace("\\", "/")
        if WINDOWS_ABSOLUTE_PATH.match(value) or "/home/runner/work/" in normalized:
            raise SupplyChainError("SBOM leaks an absolute build workspace path")
    return len(components)


def _is_forbidden_archive_path(path: str) -> bool:
    pure = PurePosixPath(path)
    if path == ".env" or (
        pure.name.startswith(".env.") and pure.name != ".env.example"
    ):
        return True
    if pure.parts and pure.parts[0] == ".contentflow":
        return True
    return pure.suffix.lower() in PRIVATE_KEY_SUFFIXES


def validate_source_archive(
    archive_path: Path, repository_root: Path, expected_commit: str
) -> int:
    repository_root = repository_root.resolve()
    if repository_commit(repository_root) != expected_commit:
        raise SupplyChainError("checked-out commit does not match expected commit")
    archive_bytes = archive_path.read_bytes()
    if not archive_bytes or len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise SupplyChainError("source archive size is outside the allowed range")
    expected_bytes = _source_archive_bytes(repository_root, expected_commit)
    if (
        not hashlib.sha256(archive_bytes).digest()
        == hashlib.sha256(expected_bytes).digest()
    ):
        raise SupplyChainError(
            "source archive is not reproducible from the checked-out commit"
        )

    prefix = f"contentflow-{expected_commit}/"
    archived_files: set[str] = set()
    total_size = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_FILES:
                raise SupplyChainError("source archive contains too many entries")
            for member in members:
                if member.name == prefix.rstrip("/") and member.isdir():
                    continue
                if not member.name.startswith(prefix):
                    raise SupplyChainError(
                        "source archive contains an unexpected prefix"
                    )
                relative = member.name.removeprefix(prefix).rstrip("/")
                if not relative:
                    continue
                pure = PurePosixPath(relative)
                if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
                    raise SupplyChainError("source archive contains an unsafe path")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise SupplyChainError("source archive contains a non-regular file")
                if relative in archived_files or _is_forbidden_archive_path(relative):
                    raise SupplyChainError(
                        "source archive contains a forbidden or duplicate file"
                    )
                total_size += member.size
                if total_size > MAX_ARCHIVE_BYTES:
                    raise SupplyChainError(
                        "source archive expands beyond the allowed size"
                    )
                archived_files.add(relative)
    except (tarfile.TarError, OSError) as error:
        raise SupplyChainError("source archive is not a valid gzip tar file") from error

    expected_files = tracked_files(repository_root, expected_commit)
    if archived_files != expected_files:
        raise SupplyChainError(
            "source archive does not exactly match tracked Git files"
        )
    if not SOURCE_REQUIRED_FILES.issubset(archived_files):
        raise SupplyChainError("source archive is missing required delivery files")
    return len(archived_files)


def project_versions(repository_root: Path) -> tuple[str, str]:
    pyproject = tomllib.loads(
        (repository_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    package = json.loads(
        (repository_root / "web" / "package.json").read_text(encoding="utf-8")
    )
    return pyproject["project"]["version"], package["version"]


def verify_materials(
    *,
    repository_root: Path,
    expected_commit: str,
    archive_path: Path,
    python_sbom_path: Path,
    frontend_sbom_path: Path,
    manifest_path: Path,
) -> dict[str, int]:
    python_version, frontend_version = project_versions(repository_root)
    python_components = validate_cyclonedx(
        _load_json(python_sbom_path),
        required_name="contentflow",
        required_version=python_version,
        minimum_components=20,
        require_metadata_root=False,
    )
    frontend_components = validate_cyclonedx(
        _load_json(frontend_sbom_path),
        required_name="contentflow-web",
        required_version=frontend_version,
        minimum_components=100,
        require_metadata_root=True,
    )
    source_files = validate_source_archive(
        archive_path, repository_root, expected_commit
    )
    verify_hash_manifest(
        manifest_path, [archive_path, python_sbom_path, frontend_sbom_path]
    )
    return {
        "python_components": python_components,
        "frontend_components": frontend_components,
        "source_files": source_files,
    }


def normalize_python_audit_requirements(
    requirements: str,
    installed_torch_version: str,
) -> tuple[str, str | None]:
    """Map an official CPU wheel local version to its public advisory identity."""
    if "+" not in installed_torch_version:
        return requirements, None
    if not installed_torch_version.endswith("+cpu"):
        raise SupplyChainError(
            "unsupported local PyTorch version for advisory normalization"
        )
    pinned = f"torch=={installed_torch_version}"
    if requirements.count(pinned) != 1:
        raise SupplyChainError(
            "locked export does not contain exactly one installed CPU PyTorch pin"
        )
    advisory_version = installed_torch_version.removesuffix("+cpu")
    return requirements.replace(
        pinned, f"torch=={advisory_version}", 1
    ), advisory_version


def restore_python_sbom_local_version(
    document: dict[str, Any],
    *,
    advisory_version: str,
    installed_version: str,
) -> dict[str, Any]:
    """Restore the exact installed wheel identity after advisory lookup."""
    restored = copy.deepcopy(document)
    components = restored.get("components")
    if not isinstance(components, list):
        raise SupplyChainError("Python CycloneDX components must be a list")
    matches = [
        component
        for component in components
        if isinstance(component, dict)
        and component.get("name") == "torch"
        and component.get("version") == advisory_version
    ]
    if len(matches) != 1:
        raise SupplyChainError(
            "Python CycloneDX does not contain exactly one advisory PyTorch component"
        )
    component = matches[0]
    component["version"] = installed_version
    purl = component.get("purl")
    if isinstance(purl, str):
        encoded_advisory = quote(advisory_version, safe="")
        marker = f"@{encoded_advisory}"
        if marker not in purl:
            raise SupplyChainError("Python CycloneDX PyTorch purl is inconsistent")
        component["purl"] = purl.replace(
            marker,
            f"@{quote(installed_version, safe='')}",
            1,
        )
    properties = component.setdefault("properties", [])
    if not isinstance(properties, list):
        raise SupplyChainError("Python CycloneDX PyTorch properties must be a list")
    properties.append(
        {
            "name": "contentflow:audit:advisory-version",
            "value": advisory_version,
        }
    )
    return restored


def add_python_project_to_sbom(
    document: dict[str, Any],
    *,
    project_version: str,
) -> dict[str, Any]:
    enriched = copy.deepcopy(document)
    components = enriched.get("components")
    dependencies = enriched.setdefault("dependencies", [])
    if not isinstance(components, list) or not isinstance(dependencies, list):
        raise SupplyChainError("Python CycloneDX inventory has an invalid shape")
    if any(
        isinstance(component, dict) and component.get("name") == "contentflow"
        for component in components
    ):
        raise SupplyChainError(
            "Python CycloneDX unexpectedly contains the local project"
        )
    project_ref = f"pkg:pypi/contentflow@{quote(project_version, safe='')}"
    dependency_refs = [
        component.get("bom-ref")
        for component in components
        if isinstance(component, dict) and isinstance(component.get("bom-ref"), str)
    ]
    if len(dependency_refs) != len(components):
        raise SupplyChainError("Python CycloneDX dependency identity is incomplete")
    components.append(
        {
            "bom-ref": project_ref,
            "type": "application",
            "name": "contentflow",
            "version": project_version,
            "purl": project_ref,
        }
    )
    dependencies.append({"ref": project_ref, "dependsOn": sorted(dependency_refs)})
    return enriched


def audit_python_dependencies(
    *,
    repository_root: Path,
    output: Path | None = None,
) -> int:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    with tempfile.TemporaryDirectory(prefix="contentflow-python-audit-") as temp_dir:
        temp_root = Path(temp_dir)
        requirements_path = temp_root / "requirements.txt"
        export = subprocess.run(
            [
                "uv",
                "export",
                "--quiet",
                "--locked",
                "--all-extras",
                "--no-hashes",
                "--no-emit-project",
                "--output-file",
                str(requirements_path),
            ],
            cwd=repository_root,
            env=environment,
            check=False,
        )
        if export.returncode:
            return export.returncode

        try:
            installed_torch_version = importlib.metadata.version("torch")
        except importlib.metadata.PackageNotFoundError as error:
            raise SupplyChainError(
                "locked audit environment does not contain PyTorch"
            ) from error
        requirements, advisory_version = normalize_python_audit_requirements(
            requirements_path.read_text(encoding="utf-8"),
            installed_torch_version,
        )
        requirements_path.write_text(requirements, encoding="utf-8")

        raw_sbom_path = temp_root / "python.raw.cdx.json"
        command = [
            sys.executable,
            "-m",
            "pip_audit",
            "--strict",
            "--no-deps",
            "-r",
            str(requirements_path),
        ]
        if output is not None:
            command.extend(
                [
                    "--format",
                    "cyclonedx-json",
                    "--output",
                    str(raw_sbom_path),
                ]
            )
        audit = subprocess.run(
            command,
            cwd=repository_root,
            env=environment,
            check=False,
        )
        if audit.returncode or output is None:
            return audit.returncode

        document = _load_json(raw_sbom_path)
        if advisory_version is not None:
            document = restore_python_sbom_local_version(
                document,
                advisory_version=advisory_version,
                installed_version=installed_torch_version,
            )
        project_version, _ = project_versions(repository_root)
        document = add_python_project_to_sbom(
            document,
            project_version=project_version,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0


def _read_document(path: Path) -> dict[str, Any]:
    return _load_json(path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and verify supply-chain evidence"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_python = subparsers.add_parser("audit-python")
    audit_python.add_argument("--repository-root", type=Path, default=Path("."))
    audit_python.add_argument("--output", type=Path)

    normalize = subparsers.add_parser("normalize")
    normalize.add_argument("--input", type=Path, required=True)
    normalize.add_argument("--output", type=Path, required=True)
    normalize.add_argument("--root-name")

    build = subparsers.add_parser("build")
    build.add_argument("--repository-root", type=Path, default=Path("."))
    build.add_argument("--expected-commit", required=True)
    build.add_argument("--output", type=Path, required=True)

    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--directory", type=Path, required=True)
    manifest.add_argument("--file", action="append", required=True)
    manifest.add_argument("--output", default="SHA256SUMS")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--repository-root", type=Path, default=Path("."))
    verify.add_argument("--expected-commit", required=True)
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--python-sbom", type=Path, required=True)
    verify.add_argument("--frontend-sbom", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _build_parser().parse_args()
    try:
        if arguments.command == "audit-python":
            result = audit_python_dependencies(
                repository_root=arguments.repository_root.resolve(),
                output=arguments.output,
            )
            if result:
                return result
            if arguments.output is not None:
                print(f"wrote audited Python CycloneDX: {arguments.output}")
        elif arguments.command == "normalize":
            normalized = normalize_cyclonedx(
                _read_document(arguments.input), root_name=arguments.root_name
            )
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(
                json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            print(f"normalized CycloneDX: {len(normalized['components'])} components")
        elif arguments.command == "build":
            build_source_archive(
                arguments.repository_root, arguments.expected_commit, arguments.output
            )
            print(f"built reproducible source archive: {arguments.output}")
        elif arguments.command == "manifest":
            write_hash_manifest(arguments.directory, arguments.file, arguments.output)
            print(f"wrote SHA-256 manifest: {arguments.directory / arguments.output}")
        else:
            result = verify_materials(
                repository_root=arguments.repository_root,
                expected_commit=arguments.expected_commit,
                archive_path=arguments.archive,
                python_sbom_path=arguments.python_sbom,
                frontend_sbom_path=arguments.frontend_sbom,
                manifest_path=arguments.manifest,
            )
            print(
                "verified supply-chain materials: "
                f"{result['source_files']} source files, "
                f"{result['python_components']} Python components, "
                f"{result['frontend_components']} frontend components"
            )
    except (OSError, KeyError, SupplyChainError) as error:
        print(f"supply-chain verification failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
