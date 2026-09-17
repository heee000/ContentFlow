"""Explicit, no-secret-output provider export and independent private runtime keys."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess

PROVIDER_KEYS = {
    "CONTENTFLOW_TEXT_PROVIDER", "CONTENTFLOW_MODEL_API_BASE",
    "CONTENTFLOW_MODEL_API_KEY", "CONTENTFLOW_TEXT_MODEL",
    "CONTENTFLOW_MODEL_REQUEST_TIMEOUT_SECONDS", "CONTENTFLOW_IMAGE_PROVIDER",
    "CONTENTFLOW_VIDEO_PROVIDER", "CONTENTFLOW_MEDIA_API_BASE",
    "CONTENTFLOW_MEDIA_API_KEY", "CONTENTFLOW_IMAGE_MODEL", "CONTENTFLOW_VIDEO_MODEL",
    "CONTENTFLOW_MEDIA_DOWNLOAD_ALLOWED_HOSTS", "CONTENTFLOW_IMAGE_SEARCH_PROVIDER",
    "CONTENTFLOW_OPENVERSE_API_BASE", "CONTENTFLOW_IMAGE_SEARCH_DOWNLOAD_ALLOWED_HOSTS",
    "CONTENTFLOW_EMBEDDING_PROVIDER", "CONTENTFLOW_EMBEDDING_API_BASE",
    "CONTENTFLOW_EMBEDDING_API_KEY", "CONTENTFLOW_EMBEDDING_MODEL",
    "CONTENTFLOW_EMBEDDING_DIMENSIONS", "CONTENTFLOW_EMBEDDING_SEND_DIMENSIONS",
}


def read_simple_env(path: Path) -> dict[str, str]:
    # Only for this bootstrap's generated, unquoted one-line files; no eval/source.
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, value = line.split("=", 1)
            values[name] = value
    return values


def exclusive_write(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--container", required=True)
    export.add_argument("--embedding-file", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--infra-env", type=Path, required=True)
    prepare.add_argument("--providers", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("Refusing to overwrite existing runtime configuration")
    if args.command == "export":
        result = subprocess.run(
            ["docker", "inspect", "--type=container", args.container],
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=20,
        )
        entries = json.loads(result.stdout)[0]["Config"]["Env"]
        values = dict(entry.split("=", 1) for entry in entries)
        providers = {key: value for key, value in values.items() if key in PROVIDER_KEYS}
        embedding = read_simple_env(args.embedding_file)
        providers.update({key: value for key, value in embedding.items()
                          if key in PROVIDER_KEYS and key.startswith("CONTENTFLOW_EMBEDDING_")})
        if providers.get("CONTENTFLOW_TEXT_PROVIDER") != "openai-compatible":
            parser.error("Source text provider is not a real API; choose it explicitly")
        for key in ("CONTENTFLOW_MODEL_API_BASE", "CONTENTFLOW_MODEL_API_KEY",
                    "CONTENTFLOW_TEXT_MODEL", "CONTENTFLOW_EMBEDDING_API_KEY"):
            if not providers.get(key):
                parser.error(f"Required provider setting missing: {key}")
        for key in ("CONTENTFLOW_IMAGE_PROVIDER", "CONTENTFLOW_VIDEO_PROVIDER"):
            if providers.get(key) not in {"manual", "http"}:
                parser.error(f"Explicit non-mock provider required: {key}")
        exclusive_write(args.output, json.dumps(providers, ensure_ascii=False))
        print("Exported only selected provider settings; no database or app signing keys.")
    else:
        infra = read_simple_env(args.infra_env)
        providers = json.loads(args.providers.read_text(encoding="utf-8"))
        if not isinstance(providers, dict) or set(providers) - PROVIDER_KEYS:
            parser.error("Provider input has unexpected fields")
        values = {
            "CONTENTFLOW_DATABASE_URL": "postgresql+psycopg://contentflow:"
            + infra["POSTGRES_PASSWORD"] + "@postgres:5432/contentflow",
            **{key: infra[key] for key in (
                "CONTENTFLOW_S3_ENDPOINT_URL", "CONTENTFLOW_S3_BUCKET",
                "CONTENTFLOW_S3_ACCESS_KEY", "CONTENTFLOW_S3_SECRET_KEY",
            )},
            "CONTENTFLOW_SECRET_KEY": secrets.token_hex(32),
            "CONTENTFLOW_CREDENTIAL_ENCRYPTION_KEY": secrets.token_hex(32),
            "CONTENTFLOW_METRICS_BEARER_TOKEN": secrets.token_hex(32),
            **providers,
        }
        if any(not isinstance(value, str) or any(char in value for char in "\r\n\0")
               for value in values.values()):
            parser.error("Runtime values must be single-line strings")
        exclusive_write(args.output, "".join(f"{key}={value}\n" for key, value in values.items()))
        print("Created independent private runtime.env; secrets were not printed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise SystemExit("Runtime preparation failed; check inputs and permissions (details suppressed).") from None
