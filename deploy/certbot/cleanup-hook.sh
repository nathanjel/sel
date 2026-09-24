#!/usr/bin/env bash
# certbot --manual-cleanup-hook: withdraw the token.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
printf 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: sel-acme\ndata: {}\n' \
  | "$HERE/kube.sh" apply -f - >/dev/null
