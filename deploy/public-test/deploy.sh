#!/usr/bin/env sh
set -eu

if [ "$#" -ne 4 ]; then
  echo "usage: deploy.sh ENV_FILE BACKEND_IMAGE WEB_IMAGE RELEASE_SHA" >&2
  exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)
ENV_FILE=$1
CONTENTFLOW_BACKEND_IMAGE=$2
CONTENTFLOW_WEB_IMAGE=$3
CONTENTFLOW_RELEASE_SHA=$4
COMPOSE_FILE="${SCRIPT_DIR}/compose.yml"

if ! printf '%s' "$CONTENTFLOW_BACKEND_IMAGE" | grep -Eq '^.+@sha256:[0-9a-f]{64}$'; then
  echo "Backend image must use an immutable digest" >&2
  exit 2
fi
if ! printf '%s' "$CONTENTFLOW_WEB_IMAGE" | grep -Eq '^.+@sha256:[0-9a-f]{64}$'; then
  echo "Web image must use an immutable digest" >&2
  exit 2
fi
if ! printf '%s' "$CONTENTFLOW_RELEASE_SHA" | grep -Eq '^[0-9a-f]{40}$'; then
  echo "Release SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
fi

export CONTENTFLOW_BACKEND_IMAGE CONTENTFLOW_WEB_IMAGE CONTENTFLOW_RELEASE_SHA

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing shared environment file: $ENV_FILE" >&2
  exit 1
fi
if [ "$(stat -c %a "$ENV_FILE")" != "600" ]; then
  echo "Environment file permissions must be exactly 600" >&2
  exit 1
fi

available_kb=$(df -Pk "$SCRIPT_DIR" | awk 'NR==2 {print $4}')
if [ "${available_kb:-0}" -lt 8388608 ]; then
  echo "At least 8 GiB free disk is required before deployment" >&2
  exit 1
fi

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

python3 "${REPOSITORY_ROOT}/scripts/validate_public_test_deployment.py" \
  --compose "$COMPOSE_FILE" --env-file "$ENV_FILE" \
  --caddyfile "${SCRIPT_DIR}/Caddyfile"

if compose ps --status running --services | grep -Eq '^(api|worker)$'; then
  "${SCRIPT_DIR}/backup.sh" "$ENV_FILE"
fi

compose pull postgres api worker web caddy
if ! compose --profile maintenance run --rm embedding-bootstrap \
  contentflow-prepare-embedding-cache verify; then
  echo "Pinned BGE-M3 cache is absent or invalid; preparing it once."
  compose --profile maintenance run --rm embedding-bootstrap \
    contentflow-prepare-embedding-cache prepare
  compose --profile maintenance run --rm embedding-bootstrap \
    contentflow-prepare-embedding-cache verify
fi
compose up -d postgres
compose run --rm --no-deps --entrypoint alembic api upgrade head
compose up -d --remove-orphans postgres api worker web caddy

attempt=0
until compose exec -T api python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=5)"; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "API readiness did not recover; release was not promoted" >&2
    exit 1
  fi
  sleep 5
done

attempt=0
while :; do
  active_workers=$(compose --profile maintenance run --rm --no-deps backup-db \
    psql -tA -c "SELECT count(*) FROM worker_nodes WHERE status='online' AND heartbeat_at > now() - interval '90 seconds';")
  if [ "${active_workers:-0}" -ge 1 ]; then
    break
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 18 ]; then
    echo "Worker heartbeat did not become active; release was not promoted" >&2
    exit 1
  fi
  sleep 5
done

umask 077
{
  printf 'CONTENTFLOW_BACKEND_IMAGE=%s\n' "$CONTENTFLOW_BACKEND_IMAGE"
  printf 'CONTENTFLOW_WEB_IMAGE=%s\n' "$CONTENTFLOW_WEB_IMAGE"
  printf 'CONTENTFLOW_RELEASE_SHA=%s\n' "$CONTENTFLOW_RELEASE_SHA"
} > "${SCRIPT_DIR}/release.env"
printf '%s\n' "$CONTENTFLOW_RELEASE_SHA" > "${SCRIPT_DIR}/release-success.txt"
echo "Public-test release promoted: ${CONTENTFLOW_RELEASE_SHA}"
