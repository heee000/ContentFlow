#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE=${1:-"${SCRIPT_DIR}/.env"}
COMPOSE_FILE="${SCRIPT_DIR}/compose.yml"
RELEASE_ENV="${SCRIPT_DIR}/release.env"
VERIFY_ID="verify-$(date -u +%Y%m%d%H%M%S)-$$"
VERIFY_DATABASE="contentflow_$(printf '%s' "$VERIFY_ID" | tr '-' '_')"

case "$VERIFY_ID" in
  *[!a-z0-9-]*) echo "Unsafe verification identifier" >&2; exit 1 ;;
esac

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing environment file: $ENV_FILE" >&2
  exit 1
fi
if [ ! -f "$RELEASE_ENV" ]; then
  echo "Missing deployed release coordinates: $RELEASE_ENV" >&2
  exit 1
fi

compose() {
  docker compose --env-file "$ENV_FILE" --env-file "$RELEASE_ENV" \
    -f "$COMPOSE_FILE" \
    --profile maintenance "$@"
}

cleanup() {
  compose run --rm --no-deps -e VERIFY_ID="$VERIFY_ID" \
    --entrypoint /bin/sh restic -euc '
      case "$VERIFY_ID" in *[!a-z0-9-]*) exit 2;; esac
      rm -rf -- "/restore/${VERIFY_ID}"
    ' >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

compose run --rm restic check --read-data-subset=10%
compose run --rm -e VERIFY_ID="$VERIFY_ID" restic restore latest \
  --host contentflow-public-test --tag postgres \
  --target "/restore/${VERIFY_ID}"
compose run --rm --no-deps \
  -e VERIFY_ID="$VERIFY_ID" -e VERIFY_DATABASE="$VERIFY_DATABASE" \
  backup-db sh -euc '
    case "$VERIFY_ID" in *[!a-z0-9-]*) exit 2;; esac
    case "$VERIFY_DATABASE" in contentflow_verify_*) ;; *) exit 2;; esac
    dump="/restore/${VERIFY_ID}/backup/contentflow.dump"
    test -s "$dump"
    pg_restore --list "$dump" >/dev/null
    createdb "$VERIFY_DATABASE"
    trap '\''dropdb --if-exists --force "$VERIFY_DATABASE" >/dev/null 2>&1 || true'\'' EXIT
    pg_restore --exit-on-error --dbname "$VERIFY_DATABASE" "$dump"
    tables=$(psql --dbname "$VERIFY_DATABASE" -tA -c \
      "SELECT count(*) FROM information_schema.tables WHERE table_schema='\''public'\'';")
    revision=$(psql --dbname "$VERIFY_DATABASE" -tA -c \
      "SELECT version_num FROM alembic_version;")
    test "$tables" -ge 28
    test "$revision" = "6d4e8f9a0b1c"
    printf "Isolated restore passed: tables=%s alembic=%s\n" "$tables" "$revision"
  '
