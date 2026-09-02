from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path

from contentflow.migrate import validate_public_restore_contract


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


def validate_worker_runtime_environment(environment: dict) -> list[str]:
    errors: list[str] = []
    positive_integers: dict[str, int] = {}
    for key in (
        "CONTENTFLOW_WORKER_LEASE_SECONDS",
        "CONTENTFLOW_WORKER_MAX_ATTEMPTS",
        "CONTENTFLOW_WORKER_HEARTBEAT_SECONDS",
        "CONTENTFLOW_WORKER_STALE_SECONDS",
        "CONTENTFLOW_WORKER_QUEUE_STALL_SECONDS",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_ATTEMPTS",
    ):
        value = str(environment.get(key) or "")
        if not value.isdigit() or int(value) <= 0:
            errors.append(f"{key} must be explicitly passed as a positive integer")
        else:
            positive_integers[key] = int(value)

    positive_numbers: dict[str, float] = {}
    for key in (
        "CONTENTFLOW_WORKER_POLL_SECONDS",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_INITIAL_SECONDS",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_SECONDS",
    ):
        try:
            value = float(environment.get(key))
        except (TypeError, ValueError):
            value = 0
        if not math.isfinite(value) or value <= 0:
            errors.append(f"{key} must be explicitly passed as a positive number")
        else:
            positive_numbers[key] = value

    try:
        jitter_ratio = float(
            environment.get("CONTENTFLOW_WORKER_DATABASE_RETRY_JITTER_RATIO")
        )
    except (TypeError, ValueError):
        jitter_ratio = -1
    if not math.isfinite(jitter_ratio) or not 0 <= jitter_ratio <= 1:
        errors.append(
            "CONTENTFLOW_WORKER_DATABASE_RETRY_JITTER_RATIO must be between 0 and 1"
        )

    retry_initial = positive_numbers.get(
        "CONTENTFLOW_WORKER_DATABASE_RETRY_INITIAL_SECONDS"
    )
    retry_max = positive_numbers.get("CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_SECONDS")
    if retry_initial is not None and retry_max is not None and retry_max < retry_initial:
        errors.append("worker database retry maximum must not be less than initial delay")
    heartbeat = positive_integers.get("CONTENTFLOW_WORKER_HEARTBEAT_SECONDS")
    stale = positive_integers.get("CONTENTFLOW_WORKER_STALE_SECONDS")
    if heartbeat is not None and stale is not None and stale <= heartbeat * 2:
        errors.append("worker stale threshold must exceed two heartbeat intervals")
    return errors


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
    for key in (
        "CONTENTFLOW_WORKSPACE_STORAGE_MAX_BYTES",
        "CONTENTFLOW_WORKSPACE_STORAGE_MAX_OBJECTS",
        "CONTENTFLOW_STORAGE_RECONCILE_INTERVAL_HOURS",
        "CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_BATCH_SIZE",
        "CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_POLL_SECONDS",
        "CONTENTFLOW_PUBLISH_RECONCILIATION_INITIAL_DELAY_SECONDS",
        "CONTENTFLOW_PUBLISH_RECONCILIATION_MAX_ATTEMPTS",
        "CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_POLL_SECONDS",
        "CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_BATCH_SIZE",
    ):
        value = str(environment.get(key) or "")
        if not value.isdigit() or int(value) <= 0:
            errors.append(f"{key} must be explicitly passed as a positive integer")
    errors.extend(validate_worker_runtime_environment(environment))
    if str(environment.get("CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_ENABLED", "")).lower() != "true":
        errors.append("public-test storage reconciliation schedule must be enabled")
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
    parser.add_argument(
        "--verify-backup",
        type=Path,
        default=Path("deploy/public-test/verify-backup.sh"),
    )
    args = parser.parse_args()
    document = _render_compose(args.compose, args.env_file)
    errors = validate_document(
        document,
        caddyfile=args.caddyfile.read_text(encoding="utf-8"),
    )
    errors.extend(
        validate_public_restore_contract(
            args.verify_backup.read_text(encoding="utf-8")
        )
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Public-test deployment configuration passed fail-closed validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
