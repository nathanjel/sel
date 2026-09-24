#!/usr/bin/env bash
# certbot --deploy-hook: load the issued certificate into the TLS secret the
# sel-docs Ingress serves. Traefik picks the change up by itself.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/kube.sh" create secret tls sel-docs-tls \
    --cert="$RENEWED_LINEAGE/fullchain.pem" --key="$RENEWED_LINEAGE/privkey.pem" \
    --dry-run=client -o yaml \
  | "$HERE/kube.sh" apply -f - >/dev/null
echo "deploy-hook: sel-docs-tls updated from $RENEWED_LINEAGE"
