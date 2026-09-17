#!/usr/bin/env bash
# Reviewed, manual bootstrap for a user-owned Ubuntu test machine.
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Usage: sudo bash install-docker.sh DEPLOY_USER --grant-docker-access' \
    'Installs docker.io and docker-compose-v2 from configured Ubuntu repositories.' \
    'Starts Docker and grants DEPLOY_USER Docker access (root-equivalent).' \
    'Does not change SSH or desktop services, or start application containers.' \
    'Docker may initialize its own networking and firewall rules when started.'
  exit 0
fi
if [[ $# != 2 || "$2" != "--grant-docker-access" ]]; then
  printf '%s\n' 'Use --help and explicitly select the deployment user and access grant.' >&2
  exit 2
fi
cf_deploy_user="$1"
if [[ ! "$cf_deploy_user" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
  printf '%s\n' 'Invalid deployment user.' >&2
  exit 2
fi
if (( EUID != 0 )); then
  printf '%s\n' 'Run this reviewed script with sudo in your own terminal.' >&2
  exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
if [[ "$ID" != "ubuntu" || "$VERSION_ID" != "24.04" ]]; then
  printf '%s\n' 'This bootstrap is scoped to Ubuntu 24.04 LTS.' >&2
  exit 1
fi
cf_deploy_uid="$(id -u "$cf_deploy_user")"
if [[ "$cf_deploy_uid" == "0" ]]; then
  printf '%s\n' 'Choose an existing non-root deployment user.' >&2
  exit 1
fi
for cf_package in docker-ce docker-ce-cli containerd.io podman-docker; do
  if dpkg-query -W -f='${Status}' "$cf_package" 2>/dev/null | grep -qx 'install ok installed'; then
    printf 'Conflicting package already installed: %s. Review it before continuing.\n' "$cf_package" >&2
    exit 1
  fi
done

printf 'Installing Ubuntu Docker packages; granting root-equivalent Docker access to %s.\n' "$cf_deploy_user"
apt-get -o DPkg::Lock::Timeout=120 update
apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends \
  docker.io docker-compose-v2
systemctl enable --now docker.service
getent group docker >/dev/null || groupadd --system docker
usermod -aG docker "$cf_deploy_user"
docker version --format '{{.Server.Version}}'
docker compose version
printf '%s\n' \
  'Docker is ready. Open a NEW SSH session to pick up Docker group membership.' \
  'No ContentFlow containers have been started. Send the result back to continue.'
