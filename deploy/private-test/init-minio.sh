#!/bin/sh
set -eu
# Print only controlled diagnostics: mc failures can include request details.
cf_step() {
  cf_label="$1"
  shift
  if ! "$@" >/dev/null 2>&1; then
    printf 'Private object-store setup failed at: %s\n' "$cf_label" >&2
    exit 1
  fi
}
cf_step admin-alias mc alias set admin http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
cf_step bucket mc mb --ignore-existing admin/contentflow-private-test-objects
cf_step private-bucket mc anonymous set none admin/contentflow-private-test-objects
cf_step app-user mc admin user add admin "$CONTENTFLOW_S3_ACCESS_KEY" "$CONTENTFLOW_S3_SECRET_KEY"
cf_step app-policy mc admin policy create admin contentflow-private-test-app /bootstrap/bucket-policy.json
cf_step policy-binding mc admin policy attach admin contentflow-private-test-app --user "$CONTENTFLOW_S3_ACCESS_KEY"
cf_step app-alias mc alias set app http://minio:9000 "$CONTENTFLOW_S3_ACCESS_KEY" "$CONTENTFLOW_S3_SECRET_KEY"
cf_step app-bucket-list mc ls app/contentflow-private-test-objects
# Application credentials must not have server administrator permissions.
if mc admin info app >/dev/null 2>&1; then
  printf '%s\n' 'Application credentials unexpectedly have administrator access.' >&2
  exit 1
fi
printf '%s\n' 'Private bucket and bucket-scoped application access verified.'
