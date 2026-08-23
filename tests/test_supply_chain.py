from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "contentflow_supply_chain", ROOT / "scripts" / "supply_chain.py"
)
assert SPEC is not None and SPEC.loader is not None
SUPPLY_CHAIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUPPLY_CHAIN)
SupplyChainError = SUPPLY_CHAIN.SupplyChainError
add_python_project_to_sbom = SUPPLY_CHAIN.add_python_project_to_sbom
build_source_archive = SUPPLY_CHAIN.build_source_archive
normalize_cyclonedx = SUPPLY_CHAIN.normalize_cyclonedx
normalize_python_audit_requirements = SUPPLY_CHAIN.normalize_python_audit_requirements
restore_python_sbom_local_version = SUPPLY_CHAIN.restore_python_sbom_local_version
repository_commit = SUPPLY_CHAIN.repository_commit
validate_source_archive = SUPPLY_CHAIN.validate_source_archive
validate_cyclonedx = SUPPLY_CHAIN.validate_cyclonedx
verify_hash_manifest = SUPPLY_CHAIN.verify_hash_manifest
read_document = SUPPLY_CHAIN._read_document


def _cyclonedx_document() -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:6bb4cce3-a02d-46a1-a7a5-af400fed304f",
        "version": 1,
        "metadata": {
            "component": {
                "bom-ref": "contentflow-web@0.1.0",
                "type": "application",
                "name": "web",
                "version": "0.1.0",
            }
        },
        "components": [
            {
                "bom-ref": "example@1.0.0",
                "type": "library",
                "name": "example",
                "version": "1.0.0",
                "scope": "optional",
                "purl": "pkg:npm/example@1.0.0",
                "properties": [
                    {
                        "name": "cdx:npm:package:path",
                        "value": "node_modules/parent/node_modules/example",
                    }
                ],
            },
            {
                "bom-ref": "example@1.0.0",
                "type": "library",
                "name": "example",
                "version": "1.0.0",
                "scope": "required",
                "purl": "pkg:npm/example@1.0.0",
                "properties": [
                    {
                        "name": "cdx:npm:package:path",
                        "value": "node_modules/example",
                    }
                ],
            },
        ],
        "dependencies": [
            {"ref": "contentflow-web@0.1.0", "dependsOn": ["example@1.0.0"]},
            {"ref": "example@1.0.0", "dependsOn": []},
            {"ref": "example@1.0.0", "dependsOn": []},
        ],
    }


def test_cpu_torch_audit_uses_public_advisory_version_only_for_lookup():
    requirements = (
        "torch==2.13.0 ; sys_platform == 'darwin'\n"
        "torch==2.13.0+cpu ; sys_platform != 'darwin'\n"
    )
    normalized, advisory_version = normalize_python_audit_requirements(
        requirements,
        "2.13.0+cpu",
    )

    assert advisory_version == "2.13.0"
    assert "torch==2.13.0+cpu" not in normalized
    assert normalized.count("torch==2.13.0") == 2
    with pytest.raises(SupplyChainError, match="exactly one"):
        normalize_python_audit_requirements("torch==2.13.0\n", "2.13.0+cpu")
    with pytest.raises(SupplyChainError, match="unsupported local"):
        normalize_python_audit_requirements(requirements, "2.13.0+vendor")


def test_python_sbom_restores_exact_cpu_wheel_and_project_identity():
    document = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "bom-ref": "torch==2.13.0",
                "type": "library",
                "name": "torch",
                "version": "2.13.0",
                "purl": "pkg:pypi/torch@2.13.0",
            },
            {
                "bom-ref": "numpy==2.5.2",
                "type": "library",
                "name": "numpy",
                "version": "2.5.2",
                "purl": "pkg:pypi/numpy@2.5.2",
            },
        ],
        "dependencies": [],
    }
    restored = restore_python_sbom_local_version(
        document,
        advisory_version="2.13.0",
        installed_version="2.13.0+cpu",
    )
    torch_component = restored["components"][0]
    assert torch_component["version"] == "2.13.0+cpu"
    assert torch_component["purl"] == "pkg:pypi/torch@2.13.0%2Bcpu"
    assert torch_component["properties"] == [
        {
            "name": "contentflow:audit:advisory-version",
            "value": "2.13.0",
        }
    ]

    enriched = add_python_project_to_sbom(restored, project_version="0.1.0")
    assert enriched["components"][-1]["name"] == "contentflow"
    assert enriched["components"][-1]["version"] == "0.1.0"
    assert enriched["dependencies"][-1]["dependsOn"] == [
        "numpy==2.5.2",
        "torch==2.13.0",
    ]


def test_normalize_cyclonedx_deduplicates_npm_install_paths():
    normalized = normalize_cyclonedx(_cyclonedx_document(), root_name="contentflow-web")

    assert normalized["metadata"]["component"]["name"] == "contentflow-web"
    assert len(normalized["components"]) == 1
    component = normalized["components"][0]
    assert component["scope"] == "required"
    assert component["properties"] == [
        {
            "name": "contentflow:npm:package:paths",
            "value": (
                '["node_modules/example","node_modules/parent/node_modules/example"]'
            ),
        }
    ]
    assert normalized["dependencies"] == [
        {"ref": "contentflow-web@0.1.0", "dependsOn": ["example@1.0.0"]},
        {"ref": "example@1.0.0", "dependsOn": []},
    ]


def test_normalize_cyclonedx_rejects_conflicting_duplicate_identity():
    document = _cyclonedx_document()
    document["components"][1]["version"] = "2.0.0"

    with pytest.raises(SupplyChainError, match="conflicting version"):
        normalize_cyclonedx(document)


def test_normalize_cyclonedx_rejects_conflicting_evidence_object():
    document = _cyclonedx_document()
    document["components"][0]["evidence"] = {"identity": {"field": "purl"}}
    document["components"][1]["evidence"] = {"identity": {"field": "name"}}

    with pytest.raises(SupplyChainError, match="conflicting evidence"):
        normalize_cyclonedx(document)


def test_read_document_accepts_utf8_bom(tmp_path: Path):
    path = tmp_path / "frontend.cdx.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"bomFormat":"CycloneDX"}')

    assert read_document(path) == {"bomFormat": "CycloneDX"}


def test_read_document_reports_invalid_json_as_supply_chain_error(tmp_path: Path):
    path = tmp_path / "invalid.cdx.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(SupplyChainError, match="not valid UTF-8 JSON"):
        read_document(path)


def test_validate_cyclonedx_rejects_workspace_path_and_unknown_dependency():
    document = normalize_cyclonedx(_cyclonedx_document(), root_name="contentflow-web")
    validate_cyclonedx(
        document,
        required_name="contentflow-web",
        required_version="0.1.0",
        minimum_components=1,
        require_metadata_root=True,
    )

    leaked = copy.deepcopy(document)
    leaked["components"][0]["description"] = "C:\\Users\\runner\\secret"
    with pytest.raises(SupplyChainError, match="workspace path"):
        validate_cyclonedx(
            leaked,
            required_name="contentflow-web",
            required_version="0.1.0",
            minimum_components=1,
            require_metadata_root=True,
        )

    unresolved = copy.deepcopy(document)
    unresolved["dependencies"][0]["dependsOn"].append("missing@1")
    with pytest.raises(SupplyChainError, match="unknown bom-ref"):
        validate_cyclonedx(
            unresolved,
            required_name="contentflow-web",
            required_version="0.1.0",
            minimum_components=1,
            require_metadata_root=True,
        )


def test_verify_hash_manifest_fails_closed_on_tampering(tmp_path: Path):
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_bytes(b"original")
    manifest = tmp_path / "SHA256SUMS"
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest.write_text(f"{digest}  {artifact.name}\n", encoding="ascii")
    verify_hash_manifest(manifest, [artifact])

    artifact.write_bytes(b"tampered")
    with pytest.raises(SupplyChainError, match="SHA-256 mismatch"):
        verify_hash_manifest(manifest, [artifact])


def test_git_archive_excludes_untracked_files_and_is_reproducible():
    commit = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.as_posix()}",
            "-C",
            str(ROOT),
            "rev-parse",
            "HEAD",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    prefix = f"contentflow-{commit}/"
    first = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.as_posix()}",
            "-C",
            str(ROOT),
            "archive",
            "--format=tar",
            f"--prefix={prefix}",
            commit,
        ],
        check=True,
        capture_output=True,
    ).stdout
    second = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.as_posix()}",
            "-C",
            str(ROOT),
            "archive",
            "--format=tar",
            f"--prefix={prefix}",
            commit,
        ],
        check=True,
        capture_output=True,
    ).stdout
    assert first == second
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:") as archive:
        names = {
            member.name.removeprefix(prefix)
            for member in archive.getmembers()
            if member.isfile()
        }
    tracked = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.as_posix()}",
            "-C",
            str(ROOT),
            "ls-tree",
            "-r",
            "--name-only",
            commit,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    assert names == set(tracked)
    assert not any(name.startswith(".contentflow/") for name in names)


def test_source_archive_accepts_the_single_git_archive_root_directory(tmp_path: Path):
    commit = repository_commit(ROOT)
    archive = tmp_path / f"contentflow-source-{commit}.tar.gz"

    build_source_archive(ROOT, commit, archive)

    assert validate_source_archive(archive, ROOT, commit) > 100


def test_ci_isolates_attestation_permissions_and_pins_actions():
    workflow_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(workflow_text)
    jobs = workflow["jobs"]
    supply_chain = jobs["supply-chain"]
    attest = jobs["attest-supply-chain"]

    assert supply_chain.get("permissions", {"contents": "read"}) == {"contents": "read"}
    assert attest["if"] == "github.event_name != 'pull_request'"
    assert attest["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
        "artifact-metadata": "write",
    }
    assert attest["needs"] == ["backend", "frontend", "supply-chain"]

    uses = [
        step["uses"]
        for job in jobs.values()
        for step in job.get("steps", [])
        if "uses" in step
    ]
    assert all(
        "@" in item and len(item.rsplit("@", 1)[1].split()[0]) == 40 for item in uses
    )
    assert (
        workflow_text.count("actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6")
        == 3
    )
    assert "persist-credentials: false" in workflow_text
    assert workflow_text.count("scripts/supply_chain.py audit-python") == 2
    assert "gh attestation verify" in workflow_text
    assert "--predicate-type https://cyclonedx.org/bom" in workflow_text
    assert 'sbom_count="$(gh attestation verify' in workflow_text
    assert 'if ! [[ "${sbom_count}" =~ ^[0-9]+$ ]]' in workflow_text
