#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE=${1:-"${SCRIPT_DIR}/.env"}
COMPOSE_FILE="${SCRIPT_DIR}/compose.yml"
RELEASE_ENV="${SCRIPT_DIR}/release.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing environment file: $ENV_FILE" >&2
  exit 1
fi

if [ ! -f "$RELEASE_ENV" ]; then
  echo "Missing deployed release coordinates: $RELEASE_ENV" >&2
  exit 1
fi

docker compose --env-file "$ENV_FILE" --env-file "$RELEASE_ENV" \
  -f "$COMPOSE_FILE" --profile maintenance run --rm restic init
docker compose --env-file "$ENV_FILE" --env-file "$RELEASE_ENV" \
  -f "$COMPOSE_FILE" --profile maintenance run --rm restic snapshots

echo "Encrypted restic repository initialized and readable."
