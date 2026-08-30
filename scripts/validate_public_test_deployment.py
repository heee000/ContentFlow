from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


IMMUTABLE_IMAGE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")


def _render_compose(compose_file: Path, env_file: Path) -> dict:
    try:
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "--profile",
                "maintenance",
                "config",
                "--format",
                "json",
            ],
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as error:
        message = (error.stderr or error.stdout or "Docker Compose render failed").strip()
        raise RuntimeError(message) from error
    return json.loads(completed.stdout)


def validate_document(document: dict, *, caddyfile: str) -> list[str]:
    errors: list[str] = []
    services = document.get("services") or {}
    required = {
        "postgres",
        "api",
        "worker",
        "web",
        "caddy",
        "embedding-bootstrap",
        "backup-db",
        "restic",
    }
    missing = sorted(required - set(services))
    if missing:
        errors.append("missing services: " + ", ".join(missing))
    if "minio" in services or "minio-init" in services:
        errors.append("public-test stack must not run MinIO")

    for name, service in services.items():
        if service.get("build") is not None:
            errors.append(f"{name} must use a prebuilt image")
        image = str(service.get("image") or "")
        if not IMMUTABLE_IMAGE.fullmatch(image):
            errors.append(f"{name} image is not pinned by sha256 digest")
        ports = service.get("ports") or []
        if name != "caddy" and ports:
            errors.append(f"{name} unexpectedly publishes host ports")

    caddy_ports = services.get("caddy", {}).get("ports") or []
    published = {int(item.get("published", 0)) for item in caddy_ports}
    if published != {80, 443}:
        errors.append("Caddy must be the only service publishing ports 80 and 443")

    backend_images = {
        str(services.get(name, {}).get("image") or "") for name in ("api", "worker")
    }
    if len(backend_images) != 1:
        errors.append("API and Worker must use the same backend image digest")

    environment = services.get("api", {}).get("environment") or {}
    expected = {
        "CONTENTFLOW_ENVIRONMENT": "production",
        "CONTENTFLOW_ALLOW_REGISTRATION": "false",
        "CONTENTFLOW_ALLOW_MOCK_PROVIDERS": "false",
        "CONTENTFLOW_REQUIRE_GOVERNED_PROMPTS": "true",
        "CONTENTFLOW_METRICS_ENABLED": "true",
        "CONTENTFLOW_STORAGE_BACKEND": "s3",
        "CONTENTFLOW_TEXT_PROVIDER": "openai-compatible",
    }
    for key, value in expected.items():
        if str(environment.get(key, "")).lower() != value:
            errors.append(f"{key} must be {value}")
    if environment.get("CONTENTFLOW_EMBEDDING_PROVIDER") in {"hash", "mock"}:
        errors.append("public-test stack must not use hash/mock embeddings")
    for key in (
        "CONTENTFLOW_S3_ENDPOINT_URL",
        "CONTENTFLOW_MODEL_API_BASE",
        "CONTENTFLOW_PUBLIC_BASE_URL",
    ):
        if not str(environment.get(key) or "").startswith("https://"):
            errors.append(f"{key} must use HTTPS")
    cors = str(environment.get("CONTENTFLOW_CORS_ORIGINS") or "")
    if "*" in cors or "http://" in cors:
        errors.append("public-test CORS must contain only an exact HTTPS origin")
    release_sha = str(environment.get("CONTENTFLOW_RELEASE_SHA") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        errors.append("CONTENTFLOW_RELEASE_SHA must be a 40-character Git SHA")

    if "/metrics" in caddyfile:
        errors.append("Caddyfile must not expose the protected metrics endpoint")
    if "reverse_proxy api:8000" not in caddyfile:
        errors.append("Caddyfile does not proxy API health/application routes")
    if "reverse_proxy web:3000" not in caddyfile:
        errors.append("Caddyfile does not proxy the Web application")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render and fail-closed validate the public-test Compose stack."
    )
    parser.add_argument(
        "--compose",
        type=Path,
        default=Path("deploy/public-test/compose.yml"),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("deploy/public-test/env.example"),
    )
    parser.add_argument(
        "--caddyfile",
        type=Path,
        default=Path("deploy/public-test/Caddyfile"),
    )
    args = parser.parse_args()
    document = _render_compose(args.compose, args.env_file)
    errors = validate_document(
        document,
        caddyfile=args.caddyfile.read_text(encoding="utf-8"),
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Public-test deployment configuration passed fail-closed validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
