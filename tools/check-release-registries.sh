#!/usr/bin/env bash
# Are the Rust crate and the Go module of a release really out there, and are
# they what this repository says they are?
#
#   tools/check-release-registries.sh VERSION [CRATE_FILE]
#
# tools/release-assets.sh runs this before it publishes the GitHub release,
# whose notes tell readers to `cargo add sel-lang` and `go get …@vVERSION`: a
# release page that names a version no registry has, or a .crate on it that is
# not the one crates.io serves, is a release that lies. So, with nothing taken
# on trust:
#
#   - crates.io has sel-lang VERSION, not yanked; and, given CRATE_FILE (the
#     .crate the release page will carry), its SHA-256 is the checksum
#     crates.io records for that version -- the same bytes;
#   - the Go module proxy has github.com/nathanjel/sel/go at vVERSION, and the
#     commit it was built from (the proxy reports its origin) is the commit the
#     local tag go/vVERSION names, under that ref; with `go` installed, the
#     download is also verified against the checksum database
#     (sum.golang.org), which is what every user's `go get` does.
#
# Network: crates.io's API, proxy.golang.org, sum.golang.org. Exit 0 only when
# every check passes.
#
# For testing against releases that exist, the names can be overridden:
#   SEL_RELEASE_CRATE=sel-lang  SEL_RELEASE_GO_MODULE=github.com/nathanjel/sel/go
#   SEL_RELEASE_GO_VERSION=VERSION  SEL_RELEASE_GO_TAG=go/vVERSION
#   SEL_RELEASE_GO_COMMIT=<sha>  (default: what the tag names)

set -uo pipefail
cd "$(dirname "$0")/.."

V="${1:?usage: tools/check-release-registries.sh VERSION [CRATE_FILE]}"
CRATE_FILE="${2:-}"
CRATE="${SEL_RELEASE_CRATE:-sel-lang}"
MODULE="${SEL_RELEASE_GO_MODULE:-github.com/nathanjel/sel/go}"
GV="${SEL_RELEASE_GO_VERSION:-$V}"
GO_TAG="${SEL_RELEASE_GO_TAG:-go/v$GV}"
UA="sel-release-check (https://github.com/nathanjel/sel)"   # crates.io requires one
status=0
fail() { echo "  FAIL $*"; status=1; }
ok() { echo "  ok   $*"; }

# --- crates.io -------------------------------------------------------------------
echo "crates.io: $CRATE $V"
if ! meta="$(curl -sf --max-time 30 -A "$UA" "https://crates.io/api/v1/crates/$CRATE/$V")"; then
  fail "crates.io has no $CRATE $V (publish it first: cargo publish -p $CRATE)"
else
  read -r num yanked checksum < <(printf '%s' "$meta" | python3 -c '
import json, sys
v = json.load(sys.stdin)["version"]
print(v["num"], "yes" if v["yanked"] else "no", v["checksum"])')
  [ "$num" = "$V" ] && ok "published" || fail "crates.io answered version $num"
  [ "$yanked" = "no" ] && ok "not yanked" || fail "$CRATE $V is yanked"
  if [ -n "$CRATE_FILE" ]; then
    local_sum="$(sha256sum "$CRATE_FILE" | cut -d' ' -f1)"
    if [ "$local_sum" = "$checksum" ]; then
      ok "$(basename "$CRATE_FILE") is byte for byte the published crate ($checksum)"
    else
      fail "$(basename "$CRATE_FILE") is not the published crate: sha256 $local_sum, crates.io has $checksum"
    fi
  fi
fi

# --- the Go module proxy -------------------------------------------------------
# Module paths are case-encoded in proxy URLs (an upper-case letter becomes
# '!' and its lower case).
escaped="$(printf '%s' "$MODULE" | sed 's/[A-Z]/!\L&/g')"
echo "proxy.golang.org: $MODULE v$GV"
want_commit="${SEL_RELEASE_GO_COMMIT:-$(git rev-parse -q --verify "refs/tags/$GO_TAG^{commit}" 2>/dev/null)}"
[ -n "$want_commit" ] || fail "there is no local tag $GO_TAG to compare the proxy's module with"
if ! info="$(curl -sf --max-time 60 "https://proxy.golang.org/$escaped/@v/v$GV.info")"; then
  fail "the proxy has no $MODULE v$GV (push the tag $GO_TAG, then: GOPROXY=https://proxy.golang.org go list -m $MODULE@v$GV)"
else
  read -r version ref hash < <(printf '%s' "$info" | python3 -c '
import json, sys
i = json.load(sys.stdin)
o = i.get("Origin") or {}
print(i.get("Version", "-"), o.get("Ref", "-"), o.get("Hash", "-"))')
  [ "$version" = "v$GV" ] && ok "served as v$GV" || fail "the proxy answered version $version"
  if [ "$hash" = "-" ]; then
    fail "the proxy reports no origin for v$GV, so the commit cannot be compared"
  elif [ -n "$want_commit" ]; then
    [ "$hash" = "$want_commit" ] && ok "built from $hash, the commit $GO_TAG names" \
      || fail "the proxy built v$GV from $hash; $GO_TAG names $want_commit (was the tag moved?)"
    [ "$ref" = "refs/tags/$GO_TAG" ] && ok "from the ref $ref" || fail "the proxy took it from $ref, not refs/tags/$GO_TAG"
  fi
  # What every `go get` does: download through the proxy and check the module's
  # hash against the checksum database.
  if command -v go >/dev/null; then
    gotmp="$(mktemp -d)"
    if sum="$(cd "$gotmp" && GOMODCACHE="$gotmp/mod" GOFLAGS=-modcacherw GOPROXY=https://proxy.golang.org \
              GOSUMDB=sum.golang.org GONOSUMDB= GOPRIVATE= GONOSUMCHECK= GOTOOLCHAIN=local \
              go mod download -json "$MODULE@v$GV" 2> "$gotmp/err" \
              | python3 -c 'import json, sys; print(json.load(sys.stdin).get("Sum", ""))')" && [ -n "$sum" ]; then
      ok "verified against sum.golang.org ($sum)"
    else
      fail "go mod download $MODULE@v$GV did not verify: $(tail -1 "$gotmp/err")"
    fi
    chmod -R u+w "$gotmp" 2>/dev/null; rm -rf "$gotmp"
  fi
fi

[ "$status" -eq 0 ] && echo "registries: $CRATE $V and $MODULE v$GV are published and match this repository"
exit "$status"
