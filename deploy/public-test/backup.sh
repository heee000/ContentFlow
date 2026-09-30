#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE=${1:-"${SCRIPT_DIR}/.env"}
COMPOSE_FILE="${SCRIPT_DIR}/compose.yml"
RELEASE_ENV="${SCRIPT_DIR}/release.env"
BACKUP_FILENAME=contentflow.dump

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing environment file: $ENV_FILE" >&2
  exit 1
fi

compose() {
  if [ -f "$RELEASE_ENV" ]; then
    docker compose --env-file "$ENV_FILE" --env-file "$RELEASE_ENV" \
      -f "$COMPOSE_FILE" --profile maintenance "$@"
  else
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" \
      --profile maintenance "$@"
  fi
}

cleanup() {
  compose run --rm --no-deps -e BACKUP_FILENAME="$BACKUP_FILENAME" \
    backup-db sh -euc 'rm -f -- "/backup/${BACKUP_FILENAME}" "/backup/${BACKUP_FILENAME}.partial"' \
    >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

compose exec -T postgres pg_isready -U contentflow -d contentflow >/dev/null
compose run --rm --no-deps -e BACKUP_FILENAME="$BACKUP_FILENAME" \
  backup-db sh -euc '
    umask 077
    pg_dump --format=custom --compress=9 --file="/backup/${BACKUP_FILENAME}.partial"
    pg_restore --list "/backup/${BACKUP_FILENAME}.partial" >/dev/null
    mv "/backup/${BACKUP_FILENAME}.partial" "/backup/${BACKUP_FILENAME}"
  '

compose run --rm restic backup "/backup/${BACKUP_FILENAME}" \
  --host contentflow-public-test --tag postgres
compose run --rm restic check --read-data-subset=5%
compose run --rm restic forget --host contentflow-public-test \
  --path "/backup/${BACKUP_FILENAME}" --keep-daily 7 --keep-weekly 4 --prune

echo "Encrypted PostgreSQL backup uploaded and retention applied."
