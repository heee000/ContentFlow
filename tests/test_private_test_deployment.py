from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

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
        "image": "sha256:4be5e87c7d2a194edfb33d03fffb6e0a48c33d98e9a4c430cbe2fe7bf5c7f661",
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


def test_private_app_only_exposes_loopback_and_preserves_production_guards() -> None:
    document = yaml.safe_load((DEPLOY / "compose.app.yml").read_text("utf-8"))
    services = document["services"]
    assert set(services) == {"api", "worker", "web", "caddy"}
    assert document["networks"]["app"]["internal"] is True
    for name, service in services.items():
        assert service["pull_policy"] == "never"
        assert service["mem_limit"] and service["pids_limit"]
        if name != "caddy":
            assert not service.get("ports")
    assert services["caddy"]["ports"] == ["127.0.0.1:3800:8080"]
    for name in ("api", "worker"):
        environment = services[name]["environment"]
        assert environment["CONTENTFLOW_ENVIRONMENT"] == "production"
        for setting in ("ALLOW_REGISTRATION", "ALLOW_MOCK_PROVIDERS"):
            assert environment[f"CONTENTFLOW_{setting}"] == "false"
        for setting in ("REQUIRE_GOVERNED_PROMPTS", "METRICS_ENABLED", "AUTH_RATE_LIMIT_ENABLED"):
            assert environment[f"CONTENTFLOW_{setting}"] == "true"
        assert environment["CONTENTFLOW_ACCESS_COOKIE_NAME"] == "contentflow_private_access"
        assert environment["CONTENTFLOW_REFRESH_COOKIE_NAME"] == "contentflow_private_refresh"
        assert "data" in services[name]["networks"]
        assert "outbound" in services[name]["networks"]
        assert services[name]["env_file"] == [{"path": "./runtime.env", "format": "raw"}]
    assert services["web"]["networks"] == ["app"]
    assert services["caddy"]["networks"] == ["app"]


def test_runtime_preparation_is_exclusive_and_copies_no_root_keys(tmp_path) -> None:
    infra = tmp_path / "infra.env"
    infra.write_text(
        "POSTGRES_PASSWORD=test-db\nMINIO_ROOT_PASSWORD=do-not-copy-root-secret\n"
        "CONTENTFLOW_S3_ENDPOINT_URL=http://minio:9000\n"
        "CONTENTFLOW_S3_BUCKET=contentflow-private-test-objects\n"
        "CONTENTFLOW_S3_ACCESS_KEY=test-app\nCONTENTFLOW_S3_SECRET_KEY=test-app-secret\n",
        encoding="utf-8",
    )
    providers = tmp_path / "providers.json"
    providers.write_text(json.dumps({"CONTENTFLOW_EMBEDDING_API_KEY": "test-only-$literal"}), encoding="utf-8")
    target = tmp_path / "runtime.env"
    command = [sys.executable, str(DEPLOY / "prepare-runtime.py"), "prepare",
               "--infra-env", str(infra), "--providers", str(providers), "--output", str(target)]
    first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert first.returncode == 0, first.stderr
    content = target.read_text("utf-8")
    assert "do-not-copy-root-secret" not in content
    assert "test-only-$literal" in content
    assert "test-only-$literal" not in first.stdout + first.stderr
    values = dict(line.split("=", 1) for line in content.splitlines())
    keys = [values[key] for key in (
        "CONTENTFLOW_SECRET_KEY", "CONTENTFLOW_CREDENTIAL_ENCRYPTION_KEY", "CONTENTFLOW_METRICS_BEARER_TOKEN")]
    assert len(set(keys)) == 3 and all(len(key) == 64 for key in keys)
    again = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert again.returncode != 0
    assert target.read_text("utf-8") == content
    forbidden = tmp_path / "forbidden.env"
    providers.write_text(json.dumps({"CONTENTFLOW_ALLOW_REGISTRATION": "true"}), encoding="utf-8")
    command[-1] = str(forbidden)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0 and not forbidden.exists()
