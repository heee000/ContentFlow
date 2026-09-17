from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "private-test"


def test_private_infra_is_isolated_and_bounded() -> None:
    document = yaml.safe_load((DEPLOY / "compose.infra.yml").read_text("utf-8"))
    assert document["name"] == "contentflow-private-test"
    assert document["networks"] == {"data": {"internal": True}}
    services = document["services"]
    assert set(services) == {"postgres", "minio", "minio-init"}
    for name, service in services.items():
        assert not service.get("ports"), name
        assert not service.get("privileged"), name
        assert not service.get("network_mode"), name
        assert service["networks"] == ["data"]
        assert service["mem_limit"] and service["pids_limit"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["logging"]["options"]["max-size"] == "10m"
        assert "@sha256:" in service["image"]
        assert not any("docker.sock" in item for item in service.get("volumes", []))
    assert services["minio-init"]["profiles"] == ["setup"]
    assert services["minio-init"]["restart"] == "no"
    assert "postgres_data:/var/lib/postgresql/data" in services["postgres"]["volumes"]
    assert "minio_data:/data" in services["minio"]["volumes"]
    assert "MINIO_ROOT_USER" not in services["postgres"]["environment"]


def test_private_object_policy_has_only_one_bucket_and_no_admin_actions() -> None:
    policy = json.loads((DEPLOY / "bucket-policy.json").read_text("utf-8"))
    assert policy["Version"] == "2012-10-17"
    assert len(policy["Statement"]) == 2
    for statement in policy["Statement"]:
        assert statement["Effect"] == "Allow"
        assert all(action.startswith("s3:") and "*" not in action
                   for action in statement["Action"])
        assert statement["Resource"] in (
            ["arn:aws:s3:::contentflow-private-test-objects"],
            ["arn:aws:s3:::contentflow-private-test-objects/*"],
        )


def test_private_bootstrap_preserves_existing_secrets_and_unix_newlines() -> None:
    script = (DEPLOY / "init-infra-secrets.sh").read_text("utf-8")
    assert '[[ -e "$cf_dir/.env" || -L "$cf_dir/.env" ]]' in script
    assert "set -o noclobber" in script
    assert "umask 077" in script
    assert "openssl rand -hex 32" in script
    for path in DEPLOY.glob("*.sh"):
        assert b"\r" not in path.read_bytes(), path.name
    assert "*.sh text eol=lf" in (ROOT / ".gitattributes").read_text("utf-8")


def test_offline_database_override_is_an_exact_local_image() -> None:
    document = yaml.safe_load((DEPLOY / "compose.offline-pg.yml").read_text("utf-8"))
    assert document == {"services": {"postgres": {
        "image": "sha256:5fa1d4c74299c466a1a051ed66ce7a44b69cf27b66444a202f7e5592963ed596",
        "pull_policy": "never",
    }}}


def test_backend_build_context_allowlist_and_optional_local_embeddings() -> None:
    ignored = (ROOT / ".dockerignore").read_text("utf-8").splitlines()
    includes = {line for line in ignored if line.startswith("!")}
    assert includes == {
        "!Dockerfile", "!pyproject.toml", "!uv.lock", "!alembic.ini",
        "!contentflow/", "!contentflow/**", "!migrations/", "!migrations/**",
    }
    assert "*" in ignored and "**/.env*" in ignored
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    assert "ARG INCLUDE_LOCAL_EMBEDDINGS=true" in dockerfile
    assert "uv sync --locked --no-dev --extra s3 --no-install-project" in dockerfile
    assert "uv sync --locked --no-dev --extra s3 --no-editable" in dockerfile
