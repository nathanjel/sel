#!/usr/bin/env bash
# certbot for the documentation site, run as the maintainer's user (no root):
# certbot lives in a virtualenv under ~/.local/share/sel-certbot, and keeps its
# account, certificates and logs there too.
#
#   deploy/certbot/certbot.sh issue      first certificate (run once)
#   deploy/certbot/certbot.sh renew      renew if due (the systemd timer runs this)
#   deploy/certbot/certbot.sh <args...>  anything else, passed to certbot
#
# HTTP-01 through the cluster: auth-hook.sh publishes the token via the sel-acme
# ConfigMap (deploy/k8s/sel-acme.yaml), deploy-hook.sh loads the new certificate
# into the sel-docs-tls secret the Ingress serves.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="${SEL_CERTBOT_HOME:-$HOME/.local/share/sel-certbot}"
DOMAIN="sel.spelnianiemarzenspzoo.com"
EMAIL="${SEL_CERTBOT_EMAIL:-marcin@galczynski.pl}"
certbot() {
  "$BASE/venv/bin/certbot" --config-dir "$BASE/config" --work-dir "$BASE/work" \
    --logs-dir "$BASE/logs" "$@"
}
case "${1:-}" in
  issue)
    certbot certonly --non-interactive --agree-tos --email "$EMAIL" \
      --manual --preferred-challenges http \
      --manual-auth-hook "$HERE/auth-hook.sh" \
      --manual-cleanup-hook "$HERE/cleanup-hook.sh" \
      --deploy-hook "$HERE/deploy-hook.sh" \
      --cert-name "$DOMAIN" -d "$DOMAIN" ;;
  renew)
    # The hooks were recorded in the renewal configuration at issue time.
    shift
    certbot renew --non-interactive "$@" ;;
  *)
    certbot "$@" ;;
esac
