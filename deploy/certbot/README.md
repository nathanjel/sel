# TLS for the documentation site

The site at https://sel.spelnianiemarzenspzoo.com (`deploy/k8s/sel-docs.yaml`)
serves a Let's Encrypt certificate issued and renewed by **certbot**, running
as the maintainer's user — no root:

- certbot lives in a virtualenv under `~/.local/share/sel-certbot/` (its
  account, certificates and logs are there too):
  `python3 -m venv ~/.local/share/sel-certbot/venv && ~/.local/share/sel-certbot/venv/bin/pip install certbot`
- HTTP-01 challenges go through the cluster: `auth-hook.sh` puts the token into
  the `sel-acme` ConfigMap, served by `deploy/k8s/sel-acme.yaml` at
  `/.well-known/acme-challenge/` (the only path not redirected to HTTPS), and
  waits until the public URL answers; `cleanup-hook.sh` withdraws it.
- `deploy-hook.sh` loads each new certificate into the `sel-docs-tls` secret the
  Ingress serves; Traefik picks it up by itself.

```sh
deploy/certbot/certbot.sh issue                  # once
deploy/certbot/certbot.sh renew --dry-run        # exercise the hooks against staging
cp deploy/certbot/sel-certbot-renew.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now sel-certbot-renew.timer
loginctl enable-linger "$USER"                   # so the timer runs while logged out
```

The hooks are recorded by absolute path in certbot's renewal configuration, so
this checkout must stay where it is (or re-run `issue` from its new place).
