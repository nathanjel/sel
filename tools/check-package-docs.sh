#!/usr/bin/env bash
# Only the user documentation ships.
#
# docs/ also holds the contributor guide, the internal design documents, the
# site's assets and its build files; none of it is for someone who installed
# the package, and a whole-directory entry once put 4.5 MB of it into the npm
# tarball. Three configurations decide what a release carries —
# package.json's `files` (npm), pyproject.toml's sdist `include` (PyPI) and
# .gitattributes' export-ignore (git archive: Packagist's dist and GitHub's tag
# tarballs) — and nothing but this script relates them. Each must ship exactly
# the documents below, and none of the maintainer files.
#
# The wheel ships python/sel/ alone and Conan exports cpp/ sources alone, so
# neither can carry docs. Quicklisp and Ultralisp clone the repository and
# cannot be told otherwise.
#
#   tools/check-package-docs.sh

set -uo pipefail
cd "$(dirname "$0")/.."

USER_DOCS="docs/README.md docs/extending.md docs/functions.md docs/operators.md docs/overview.md docs/parity.md docs/reference/builtins.md docs/reference/limits.md docs/sql.md docs/syntax.md docs/usage/README.md docs/usage/in-memory.md docs/usage/repl.md docs/usage/scripting.md docs/usage/sql-3nf.md docs/usage/sql-conditions.md docs/usage/sql-eav.md docs/usage/sql-flat.md docs/usage/sql-functions.md docs/usage/sql-pipelines.md docs/usage/sql-star.md docs/usage/validation.md"
NEVER="PACKAGING.md CLAUDE.md"

want="$(printf '%s\n' $USER_DOCS | sort)"
status=0

# $1 channel name, stdin: every path that channel ships
check() {
  local shipped docs bad
  shipped="$(sort -u)"
  docs="$(printf '%s\n' "$shipped" | grep '^docs/.' | grep -v '/$')"
  if [ "$docs" != "$want" ]; then
    echo "$1: ships the wrong documents" >&2
    diff <(printf '%s\n' "$want") <(printf '%s\n' "$docs") | sed -n 's/^</    missing /p; s/^>/    extra   /p' >&2
    status=1
  fi
  for f in $NEVER; do
    if printf '%s\n' "$shipped" | grep -qx "$f"; then
      echo "$1: ships $f" >&2
      status=1
    fi
  done
  [ "$status" -eq 0 ] && printf '  %-10s %s user documents\n' "$1" "$(printf '%s\n' "$docs" | wc -l)"
}

if ! command -v npm >/dev/null; then
  echo "npm: not found — the npm package list cannot be checked" >&2
  status=1
else
  # Process substitution, not a pipe: the last command of a pipeline runs in a
  # subshell, and a `status=1` set there never reached the verdict below.
  check npm < <(npm pack --dry-run --json --ignore-scripts 2>/dev/null \
    | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{for(const f of JSON.parse(s)[0].files)console.log(f.path)})')
fi

# The sdist is read from its include list rather than built, because a build
# needs hatchling and, isolated, the network. Every docs entry must be a file
# (a directory would take what is beside the user documents), and nothing may
# include the whole tree.
sed -n '/^\[tool\.hatch\.build\.targets\.sdist\]/,/^\[/{/^include/,/^\]/p}' pyproject.toml \
  | sed -n 's/^[[:space:]]*"\/\{0,1\}\([^"]*\)".*/\1/p' > "${TMPDIR:-/tmp}/sel-sdist-include.$$"
if grep -qx -e '' -e 'docs/' -e 'docs' "${TMPDIR:-/tmp}/sel-sdist-include.$$"; then
  echo "sdist: pyproject.toml includes a directory that holds non-user documents" >&2
  status=1
fi
check sdist < "${TMPDIR:-/tmp}/sel-sdist-include.$$"
rm -f "${TMPDIR:-/tmp}/sel-sdist-include.$$"

# --worktree-attributes, so an edit to .gitattributes is checked before it is
# committed; the tree archived is HEAD's, which is what a tag would carry.
check archive < <(git archive --worktree-attributes HEAD | tar -t)

[ "$status" -eq 0 ] && echo "packages ship the user documents only"
exit "$status"
