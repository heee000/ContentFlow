#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 0 ]]; then
  printf '%s\n' 'Run without arguments from the prepared private-test directory.' >&2
  exit 2
fi
cf_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -e "$cf_dir/.env" || -L "$cf_dir/.env" ]]; then
  printf '%s\n' 'Refusing to overwrite existing .env; preserve it for this deployment.' >&2
  exit 1
fi
if [[ "$(stat -c %a "$cf_dir")" != 700 || ! -O "$cf_dir" ]]; then
  printf '%s\n' 'Use a dedicated directory owned by the current user with mode 700.' >&2
  exit 1
fi
command -v openssl >/dev/null
umask 077
set -o noclobber
# Generate all values before exclusively creating the file. Nothing is echoed.
cf_db_password="$(openssl rand -hex 32)"
cf_root_user="cfroot_$(openssl rand -hex 8)"
cf_root_password="$(openssl rand -hex 32)"
cf_app_user="cfapp_$(openssl rand -hex 8)"
cf_app_password="$(openssl rand -hex 32)"
printf '%s\n' \
  "POSTGRES_PASSWORD=$cf_db_password" \
  "MINIO_ROOT_USER=$cf_root_user" \
  "MINIO_ROOT_PASSWORD=$cf_root_password" \
  'CONTENTFLOW_S3_ENDPOINT_URL=http://minio:9000' \
  'CONTENTFLOW_S3_BUCKET=contentflow-private-test-objects' \
  "CONTENTFLOW_S3_ACCESS_KEY=$cf_app_user" \
  "CONTENTFLOW_S3_SECRET_KEY=$cf_app_password" > "$cf_dir/.env"
printf '%s\n' 'Created private .env (mode 600); secrets were not printed.'
