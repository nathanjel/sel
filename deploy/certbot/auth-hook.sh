#!/usr/bin/env bash
# certbot --manual-auth-hook: serve the HTTP-01 token through the sel-acme
# ConfigMap, then wait until the public URL answers with it -- a ConfigMap
# volume reaches the pod after the kubelet's next sync, which takes seconds.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
printf 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: sel-acme\ndata:\n  "%s": "%s"\n' \
  "$CERTBOT_TOKEN" "$CERTBOT_VALIDATION" | "$HERE/kube.sh" apply -f - >/dev/null
url="http://$CERTBOT_DOMAIN/.well-known/acme-challenge/$CERTBOT_TOKEN"
for _ in $(seq 1 90); do
  [ "$(curl -s "$url")" = "$CERTBOT_VALIDATION" ] && exit 0
  sleep 2
done
echo "auth-hook: $url never served the token" >&2
exit 1
