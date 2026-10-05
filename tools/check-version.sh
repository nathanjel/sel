#!/usr/bin/env bash
# Every manifest must declare the same version.
#
# They are the seven manifests (rust/Cargo.toml the latest), the version
# constants the hosts report at run time -- python/sel/__init__.py's
# __version__ (published in the wheel metadata), PHP's Sel::VERSION and Go's
# version.Version (both what `sel --version` prints) -- and the top heading of
# CHANGELOG.md, so that a release
# whose notes were never written fails here rather than at the tag. Nothing but
# this script relates them. composer.json is deliberately absent: Packagist
# infers the version from the git tag, and the check below fails if a version
# field ever appears there. A release where one has drifted publishes a package
# whose metadata disagrees with its siblings — which is the sort of thing
# nobody notices until a downstream resolver does.
#
#   tools/check-version.sh                 check they agree
#   tools/check-version.sh 0.6.0           check they all equal 0.6.0
#   tools/check-version.sh 0.6.0 --tags    and that tags v0.6.0 and go/v0.6.0 exist
#                                          on one commit (the Go module is go/, and
#                                          the Go toolchain reads its version from
#                                          a tag with that prefix)

set -uo pipefail
cd "$(dirname "$0")/.."

extract() {
  case "$1" in
    package.json)      sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' package.json | head -1 ;;
    pyproject.toml)    sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' pyproject.toml | head -1 ;;
    cpp/conanfile.py)  sed -n 's/.*version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' cpp/conanfile.py | head -1 ;;
    cpp/vcpkg.json)    sed -n 's/.*"version-semver"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' cpp/vcpkg.json | head -1 ;;
    cpp/CMakeLists.txt) sed -n 's/.*project(sel-lang VERSION \([0-9.]*\).*/\1/p' cpp/CMakeLists.txt | head -1 ;;
    # Exactly one :version form, outside comments: a second system with a
    # version of its own could disagree, and `head -1` would never notice.
    lisp/sel-lang.asd)
      v="$(sed -e 's/;.*//' lisp/sel-lang.asd | sed -n 's/^[[:space:]]*:version[[:space:]]*"\([^"]*\)".*/\1/p')"
      if [ "$(printf '%s' "$v" | grep -c .)" -eq 1 ]; then echo "$v"; fi ;;
    php/src/Sel.php)   sed -n "s/^[[:space:]]*public const VERSION[[:space:]]*=[[:space:]]*'\([^']*\)'.*/\1/p" php/src/Sel.php | head -1 ;;
    go/internal/version/version.go) sed -n 's/^const Version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' go/internal/version/version.go | head -1 ;;
    rust/Cargo.toml)   sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' rust/Cargo.toml | head -1 ;;
    python/sel/__init__.py) sed -n "s/^__version__[[:space:]]*=[[:space:]]*'\([^']*\)'.*/\1/p" python/sel/__init__.py | head -1 ;;
    php/src/Sel.php)   sed -n "s/.*public const VERSION[[:space:]]*=[[:space:]]*'\([^']*\)'.*/\1/p" php/src/Sel.php | head -1 ;;
    go/internal/version/version.go) sed -n 's/^const Version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' go/internal/version/version.go | head -1 ;;
    CHANGELOG.md)      sed -n 's/^## \([0-9][0-9.]*\) .*/\1/p' CHANGELOG.md | head -1 ;;
  esac
}

FILES="package.json pyproject.toml cpp/conanfile.py cpp/vcpkg.json
       cpp/CMakeLists.txt lisp/sel-lang.asd rust/Cargo.toml python/sel/__init__.py
       php/src/Sel.php go/internal/version/version.go CHANGELOG.md"

want="${1:-}"
tags="${2:-}"
status=0
first=""

for f in $FILES; do
  got="$(extract "$f")"
  if [ -z "$got" ]; then
    echo "$f: no single version found — the extractor needs updating, or the file declares two" >&2
    status=1
    continue
  fi
  [ -z "$first" ] && first="$got"
  printf '  %-32s %s\n' "$f" "$got"
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
    printf '  %-32s %s\n' "composer.json dev-main" "$actual_alias"
    echo "    ^ expected $expected_alias for release series $major_minor" >&2
    status=1
  else
    printf '  %-32s %s\n' "composer.json dev-main" "$actual_alias"
  fi
fi

# The install instructions pin the version where a reader copies it: the CDN
# URL (sel-lang@X), the Conan reference (sel-lang/X), the Go module and command
# (…/sel/go@vX, …/go/bin/sel@vX) and the Rust dependency line
# (sel-lang = { version = "X" … }). A release that bumped the manifests and not
# these would send a reader to the previous version.
if [ -n "$first" ]; then
  for f in README.md docs/usage/README.md docs/usage/repl.md rust/README.md go/README.md; do
    for pinned in $(grep -o 'sel-lang[@/][0-9][0-9.]*[0-9]' "$f" | sort -u); do
      if [ "${pinned#sel-lang?}" != "$first" ]; then
        printf '  %-32s %s\n' "$f" "$pinned"
        echo "    ^ expected sel-lang${pinned:8:1}$first" >&2
        status=1
      fi
    done
    for pinned in $(grep -o 'nathanjel/sel/go[a-z/]*@v[0-9][0-9.]*[0-9]' "$f" | sort -u); do
      if [ "${pinned##*@v}" != "$first" ]; then
        printf '  %-32s %s\n' "$f" "$pinned"
        echo "    ^ expected @v$first" >&2
        status=1
      fi
    done
    for pinned in $(grep -o 'sel-lang = { version = "[0-9][0-9.]*[0-9]"' "$f" | grep -o '[0-9][0-9.]*[0-9]' | sort -u); do
      if [ "$pinned" != "$first" ]; then
        printf '  %-32s sel-lang = { version = "%s" }\n' "$f" "$pinned"
        echo "    ^ expected $first" >&2
        status=1
      fi
    done
  done
fi

# --tags: the release is tagged, and the Go module with it. A missing go/vX
# leaves `go get …@vX` unresolvable however right everything else is.
if [ "$tags" = "--tags" ] && [ -n "$want" ]; then
  main_tag="$(git rev-parse -q --verify "refs/tags/v$want^{commit}" 2>/dev/null)"
  go_tag="$(git rev-parse -q --verify "refs/tags/go/v$want^{commit}" 2>/dev/null)"
  if [ -z "$main_tag" ] || [ -z "$go_tag" ]; then
    echo "tags: v$want and go/v$want must both exist" >&2
    status=1
  elif [ "$main_tag" != "$go_tag" ]; then
    echo "tags: v$want and go/v$want are on different commits" >&2
    status=1
  else
    printf '  %-32s %s\n' "tags v$want, go/v$want" "${main_tag:0:12}"
  fi
fi

# One description, every registry. npm, Packagist, PyPI, crates.io, Conan,
# vcpkg and Quicklisp each show the package's description on its page, and the
# copies were hand-edited apart until most of them named the hosts of an older
# roster and one named the current one. package.json's is the one; the others must equal it (vcpkg's is
# the first line of its description list, Conan's the concatenated string).
if ! python3 - <<'PY'
import ast, json, re, sys
def toml_description(path):
    for line in open(path, encoding='utf-8'):
        m = re.match(r'description\s*=\s*"(.*)"\s*$', line)
        if m:
            return m.group(1)
want = json.load(open('package.json', encoding='utf-8'))['description']
got = {
    'composer.json': json.load(open('composer.json', encoding='utf-8')).get('description'),
    'pyproject.toml': toml_description('pyproject.toml'),
    'rust/Cargo.toml': toml_description('rust/Cargo.toml'),
    'cpp/vcpkg.json': (json.load(open('cpp/vcpkg.json', encoding='utf-8')).get('description') or [None])[0],
}
conan = ast.parse(open('cpp/conanfile.py', encoding='utf-8').read())
got['cpp/conanfile.py'] = next((n.value.value for n in ast.walk(conan) if isinstance(n, ast.Assign)
                                and any(getattr(t, 'id', None) == 'description' for t in n.targets)
                                and isinstance(n.value, ast.Constant)), None)
asd = open('lisp/sel-lang.asd', encoding='utf-8').read()
m = re.search(r'\(defsystem "sel-lang".*?:description "((?:[^"\\]|\\.)*)"', asd, re.S)
got['lisp/sel-lang.asd'] = m.group(1) if m else None
bad = 0
for path, value in got.items():
    if value != want:
        print(f'  {path:24} description differs from package.json\'s:\n    {value!r}', file=sys.stderr)
        bad = 1
if not bad:
    print(f'  {"descriptions":24} {len(got) + 1} manifests, one text')
sys.exit(bad)
PY
then
  status=1
fi

if [ "$status" -eq 0 ]; then
  echo "versions agree: $first"
else
  echo "VERSION MISMATCH" >&2
fi
exit "$status"
