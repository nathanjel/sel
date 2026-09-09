#!/usr/bin/env bash
# Every manifest must declare the same version.
#
# There are eight of them now — six manifests, python/sel/__init__.py's
# __version__ (not a manifest, but published in the wheel metadata and just as
# wrong if it drifted), and the top heading of CHANGELOG.md, so that a release
# whose notes were never written fails here rather than at the tag. Nothing but
# this script relates them. composer.json is deliberately absent: Packagist
# infers the version from the git tag, and the check below fails if a version
# field ever appears there. A release where one has drifted publishes a package
# whose metadata disagrees with its siblings — which is the sort of thing
# nobody notices until a downstream resolver does.
#
#   tools/check-version.sh            check they agree
#   tools/check-version.sh 0.6.0      check they all equal 0.6.0

set -uo pipefail
cd "$(dirname "$0")/.."

extract() {
  case "$1" in
    package.json)      sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' package.json | head -1 ;;
    pyproject.toml)    sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' pyproject.toml | head -1 ;;
    cpp/conanfile.py)  sed -n 's/.*version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' cpp/conanfile.py | head -1 ;;
    cpp/vcpkg.json)    sed -n 's/.*"version-semver"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' cpp/vcpkg.json | head -1 ;;
    cpp/CMakeLists.txt) sed -n 's/.*project(sel-lang VERSION \([0-9.]*\).*/\1/p' cpp/CMakeLists.txt | head -1 ;;
    lisp/sel-lang.asd) sed -n 's/.*:version[[:space:]]*"\([^"]*\)".*/\1/p' lisp/sel-lang.asd | head -1 ;;
    python/sel/__init__.py) sed -n "s/^__version__[[:space:]]*=[[:space:]]*'\([^']*\)'.*/\1/p" python/sel/__init__.py | head -1 ;;
    CHANGELOG.md)      sed -n 's/^## \([0-9][0-9.]*\) .*/\1/p' CHANGELOG.md | head -1 ;;
  esac
}

FILES="package.json pyproject.toml cpp/conanfile.py cpp/vcpkg.json
       cpp/CMakeLists.txt lisp/sel-lang.asd python/sel/__init__.py
       CHANGELOG.md"

want="${1:-}"
status=0
first=""

for f in $FILES; do
  got="$(extract "$f")"
  if [ -z "$got" ]; then
    echo "$f: no version found — the extractor needs updating" >&2
    status=1
    continue
  fi
  [ -z "$first" ] && first="$got"
  printf '  %-24s %s\n' "$f" "$got"
  if [ -n "$want" ] && [ "$got" != "$want" ]; then
    echo "    ^ expected $want" >&2
    status=1
  elif [ "$got" != "$first" ]; then
    echo "    ^ disagrees with $first" >&2
    status=1
  fi
done

# composer.json deliberately carries no version: Packagist infers it from the
# git tag, and hard-coding it there is a known way to publish a lie.
if grep -q '"version"' composer.json 2>/dev/null; then
  echo "composer.json has a version field — it must not (see PACKAGING.md)" >&2
  status=1
fi

# composer.json carries extra.branch-alias.dev-main so Packagist knows what
# version dev-main represents. It must match the current release series (<major>.<minor>.x-dev).
if [ -n "$first" ]; then
  major_minor="$(echo "$first" | cut -d. -f1,2)"
  expected_alias="${major_minor}.x-dev"
  actual_alias="$(sed -n 's/.*"dev-main"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' composer.json | head -1)"
  if [ -z "$actual_alias" ]; then
    echo "composer.json: missing extra.branch-alias.dev-main" >&2
    status=1
  elif [ "$actual_alias" != "$expected_alias" ]; then
    printf '  %-24s %s\n' "composer.json dev-main" "$actual_alias"
    echo "    ^ expected $expected_alias for release series $major_minor" >&2
    status=1
  else
    printf '  %-24s %s\n' "composer.json dev-main" "$actual_alias"
  fi
fi

if [ "$status" -eq 0 ]; then
  echo "versions agree: $first"
else
  echo "VERSION MISMATCH" >&2
fi
exit "$status"
