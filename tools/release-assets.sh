#!/usr/bin/env bash
# The GitHub release: one file per host, named and labelled for it, and notes
# that open with "Which file do I want?".
#
#   tools/release-assets.sh 0.9.2            build dist/release-0.9.2/ and its NOTES.md
#   tools/release-assets.sh 0.9.2 --publish  and create the GitHub release v0.9.2 from them
#
# Run it after the tag is pushed and the npm and Python builds exist
# (PACKAGING.md §"GitHub release"). The source bundles are `git archive` of the
# tag, cut down to one host each; the npm tarball, the browser bundles and the
# Python files are copied from dist/, where the registry steps built them.
# Every archive is a .tar.gz: the npm tarball is renamed to say so, and npm
# installs it by path under any name. The Python files keep their standard
# names, because pip reads the version and tags from the file name; their
# labels say what they are instead.

set -euo pipefail
cd "$(dirname "$0")/.."

V="${1:?usage: tools/release-assets.sh VERSION [--publish]}"
PUBLISH="${2:-}"
TAG="v$V"
OUT="dist/release-$V"
REPO="nathanjel/sel"

git rev-parse -q --verify "refs/tags/$TAG" >/dev/null \
  || { echo "release-assets: no tag $TAG; tag the release first" >&2; exit 1; }
for f in "dist/npm/sel-lang-$V.tgz" dist/sel.mjs dist/sel.min.mjs \
         "dist/python/sel_lang-$V-py3-none-any.whl" "dist/python/sel_lang-$V.tar.gz"; do
  [ -f "$f" ] || { echo "release-assets: $f is missing; run the npm and PyPI build steps first" >&2; exit 1; }
done
# The copied builds come from the working tree: it must hold what the tag holds.
if ! git diff --quiet "$TAG" -- js/src python/sel package.json pyproject.toml; then
  echo "release-assets: js/src, python/sel or a manifest differs from $TAG; build from the tag" >&2
  exit 1
fi

mkdir -p "$OUT"
cpp_files="$(git ls-tree -r --name-only "$TAG" cpp | grep -v '^cpp/bin/\|^cpp/tests/\|^cpp/Makefile$')"
common="LICENSE README.md CHANGELOG.md"
# shellcheck disable=SC2086
git archive --format=tar.gz --prefix="sel-lang-$V-cpp/" -o "$OUT/sel-lang-$V-cpp-source.tar.gz" "$TAG" $common $cpp_files
# shellcheck disable=SC2086
git archive --format=tar.gz --prefix="sel-lang-$V-php/" -o "$OUT/sel-lang-$V-php-source.tar.gz" "$TAG" $common composer.json php/src
# shellcheck disable=SC2086
git archive --format=tar.gz --prefix="sel-lang-$V-lisp/" -o "$OUT/sel-lang-$V-lisp-source.tar.gz" "$TAG" $common lisp/sel-lang.asd lisp/src
cp "dist/npm/sel-lang-$V.tgz" "$OUT/sel-lang-$V-js-npm.tar.gz"
cp dist/sel.mjs "$OUT/sel-lang-$V-js-bundle.mjs"
cp dist/sel.min.mjs "$OUT/sel-lang-$V-js-bundle.min.mjs"
cp "dist/python/sel_lang-$V-py3-none-any.whl" "dist/python/sel_lang-$V.tar.gz" "$OUT/"

php_min="$(sed -n 's/.*"php": *">=\([0-9.]*\)".*/\1/p' composer.json)"
py_min="$(sed -n 's/^requires-python = ">=\([0-9.]*\)"/\1/p' pyproject.toml)"
node_min="$(node -p 'require("./package.json").engines.node.replace(">=", "")')"

# file|label, in the order the release page lists them.
ASSETS=(
  "sel-lang-$V-js-npm.tar.gz|JavaScript (Node): npm package — npm install ./sel-lang-$V-js-npm.tar.gz"
  "sel-lang-$V-js-bundle.min.mjs|JavaScript (browser): standalone ES module, minified"
  "sel-lang-$V-js-bundle.mjs|JavaScript (browser): standalone ES module"
  "sel_lang-$V-py3-none-any.whl|Python ≥ $py_min: wheel — pip install sel_lang-$V-py3-none-any.whl"
  "sel_lang-$V.tar.gz|Python ≥ $py_min: source distribution (sdist)"
  "sel-lang-$V-php-source.tar.gz|PHP ≥ $php_min: sources — require php/src/bootstrap.php"
  "sel-lang-$V-cpp-source.tar.gz|C++23: library sources with CMake, Conan and vcpkg manifests"
  "sel-lang-$V-lisp-source.tar.gz|Common Lisp: ASDF system sel-lang"
)

{
cat <<EOF
## Which file do I want?

SEL is one language implemented five times; every host evaluates every rule identically. The package is \`sel-lang\` everywhere, so most people want their registry rather than a file below.

| Language | From a registry | From this page |
|---|---|---|
| **JavaScript (Node ≥ $node_min)** | \`npm install sel-lang\` | \`sel-lang-$V-js-npm.tar.gz\`: the same package, for \`npm install ./sel-lang-$V-js-npm.tar.gz\` |
| **JavaScript (browser)** | \`import { evaluate } from 'https://cdn.jsdelivr.net/npm/sel-lang@$V/dist/sel.min.mjs'\` (or [unpkg](https://unpkg.com/sel-lang@$V/dist/sel.min.mjs)) | \`sel-lang-$V-js-bundle.min.mjs\` / \`sel-lang-$V-js-bundle.mjs\`: one standalone ES module, no dependencies; without the SQL layer |
| **Python ≥ $py_min** | \`pip install sel-lang\` | \`sel_lang-$V-py3-none-any.whl\` (\`pip install\` it directly) or \`sel_lang-$V.tar.gz\`, the source distribution |
| **PHP ≥ $php_min** | \`composer require nathanjel/sel-lang\` | \`sel-lang-$V-php-source.tar.gz\`: \`require 'php/src/bootstrap.php';\` (no autoloader, no dependencies) |
| **C++23** | \`vcpkg install sel-lang\` or \`conan install --requires sel-lang/$V\` | \`sel-lang-$V-cpp-source.tar.gz\`: the library sources with their CMake, Conan and vcpkg manifests; \`cmake -S cpp -B build && cmake --install build\`, then \`find_package(sel-lang)\` |
| **Common Lisp** | \`(ql:quickload :sel-lang)\` (Quicklisp / Ultralisp) | \`sel-lang-$V-lisp-source.tar.gz\`: the ASDF system \`sel-lang\` (needs \`cl-ppcre\`); push \`lisp/\` onto \`asdf:*central-registry*\` |

The source tarballs are \`git archive\` of the \`$TAG\` tag, cut down to one host each. GitHub's own "Source code" archives are the whole repository.

---

EOF
# The CHANGELOG entry for this version, without its heading.
awk -v v="$V" '$0 ~ "^## "v" " {f=1; next} f && /^## [0-9]/ {exit} f' CHANGELOG.md
} > "$OUT/NOTES.md"

grep -q . <(awk -v v="$V" '$0 ~ "^## "v" " {f=1; next} f && /^## [0-9]/ {exit} f' CHANGELOG.md) \
  || { echo "release-assets: CHANGELOG.md has no entry for $V" >&2; exit 1; }

echo "release-assets: $OUT"
for a in "${ASSETS[@]}"; do printf '  %-40s %s\n' "${a%%|*}" "${a#*|}"; done

if [ "$PUBLISH" = "--publish" ]; then
  args=()
  for a in "${ASSETS[@]}"; do args+=("$OUT/${a%%|*}#${a#*|}"); done
  gh release create "$TAG" --repo "$REPO" --title "SEL $V" --notes-file "$OUT/NOTES.md" --verify-tag "${args[@]}"
fi
