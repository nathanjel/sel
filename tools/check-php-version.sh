#!/usr/bin/env bash
# The oldest PHP that composer.json still allows (T12, PHP-C43 family).
#
# composer.json says `"php": ">=8.1"`; every other lane here runs whatever `php` the
# machine has (8.5 today), so a host feature added in 8.2+ or a memory footprint that
# grows on the older engine would ship unnoticed. This runs the conformance suite, the
# optimizer and runtime lanes and the API probes on the pinned `php:8.1-cli` image --
# which has no ext-gmp, so it is also the no-GMP configuration on the oldest engine.
#
#   tools/check-php-version.sh            fails if the image cannot be run
#   SEL_SKIP_PHP81=1 tools/check-php-version.sh    opt out (prints the skip)
#
# BLOCKED is not PASSED: without docker (or an 8.1 binary in SEL_PHP81_BIN) and without
# the opt-out, this exits non-zero.

set -uo pipefail
cd "$(dirname "$0")/.."

if [ "${SEL_SKIP_PHP81:-0}" = 1 ]; then
  echo "php 8.1: skipped (SEL_SKIP_PHP81=1)"; exit 0
fi

IMAGE="${SEL_PHP81_IMAGE:-php:8.1-cli}"
if [ -n "${SEL_PHP81_BIN:-}" ]; then
  run() { "$SEL_PHP81_BIN" -d memory_limit=-1 "$@"; }
elif command -v docker >/dev/null 2>&1 && docker image inspect "$IMAGE" >/dev/null 2>&1; then
  run() { docker run --rm -m 6g -v "$PWD":/w -w /w "$IMAGE" php -d memory_limit=-1 "$@"; }
elif command -v docker >/dev/null 2>&1 && docker pull "$IMAGE" >/dev/null 2>&1; then
  run() { docker run --rm -m 6g -v "$PWD":/w -w /w "$IMAGE" php -d memory_limit=-1 "$@"; }
else
  echo "php 8.1: BLOCKED — no docker image $IMAGE and no SEL_PHP81_BIN (SEL_SKIP_PHP81=1 opts out)" >&2
  exit 1
fi

status=0
v="$(run -r 'echo PHP_VERSION, " gmp=", extension_loaded("gmp") ? "yes" : "no";' 2>&1)"
echo "php 8.1 lane: $v"
case "$v" in 8.1.*) ;; *) echo "expected PHP 8.1.x, got: $v" >&2; exit 1 ;; esac

step() {
  local label="$1"; shift
  local out
  if out="$(run "$@" 2>&1)"; then
    echo "ok   $label: $(echo "$out" | tail -1 | cut -c1-100)"
  else
    echo "FAIL $label: $(echo "$out" | tail -3 | tr '\n' ' ' | cut -c1-300)"
    status=1
  fi
}
step "conformance (all files)" php/bin/conformance
step "optimizer" tools/check-php-optimizer.php
step "runtime" tools/check-php-runtime.php
step "plain vs optimised" tools/check-eval-equivalence.php
exit "$status"
