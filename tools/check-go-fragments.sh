#!/usr/bin/env bash
# Compiles the Go builtin fragments of the reference examples in place and runs
# their cases.
#
#   tools/check-go-fragments.sh
#
# examples/fn-simple and examples/fn-complex are REFERENCE categories: per-host
# code that adds a function to SEL itself, which compiles only inside the host's
# builtin table, so tools/check-examples.sh cannot run it. The Go host can do the
# next best thing cheaply. For each category this copies go/ aside, wraps the
# quoted region of examples/<cat>/go.go in `package sel` and an init() -- where
# go/sel/builtins_*.go put theirs -- checks it is gofmt-clean, vets and builds
# the conformance runner with it, and runs examples/<cat>/cases.selt, which the
# stock build fails (the function does not exist there). The fragment the docs
# quote is therefore a fragment that compiles and does what its cases say.
set -uo pipefail
cd "$(dirname "$0")/.."
command -v go >/dev/null || { echo "go fragments: no Go toolchain" >&2; exit 1; }
ROOT="$PWD"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
status=0
for cat in fn-simple fn-complex; do
  rm -rf "$WORK/go"
  mkdir "$WORK/go"
  # The sources only: go/build also holds the usage lane's module cache.
  tar -C go --exclude=./build -cf - . | tar -C "$WORK/go" -xf -
  file="$WORK/go/sel/zz_example_${cat//-/_}.go"
  region="$(awk '/EXAMPLE-END/{f=0} f; /EXAMPLE-BEGIN/{f=1}' "examples/$cat/go.go")"
  if [ -z "$region" ]; then
    echo "FAIL $cat: examples/$cat/go.go has no EXAMPLE region"; status=1; continue
  fi
  { printf 'package sel\n\nimport "fmt"\n\nvar _ = fmt.Sprint\n\nfunc init() {\n'
    printf '%s\n' "$region" | sed 's/^./\t&/'
    printf '}\n'; } > "$file"
  if [ -n "$(gofmt -l "$file")" ]; then
    echo "FAIL $cat: the region is not gofmt-clean"
    gofmt -d "$file" | sed 's/^/       /'
    status=1
  fi
  if ! (cd "$WORK/go" && go vet ./sel && go build -o "$WORK/conformance" ./bin/conformance) > "$WORK/build.log" 2>&1; then
    echo "FAIL $cat: the fragment does not compile in go/sel"
    sed 's/^/       /' "$WORK/build.log" | tail -20
    status=1
    continue
  fi
  if out="$("$WORK/conformance" "examples/$cat/cases.selt" 2>&1)"; then
    echo "go fragments: $cat $(echo "$out" | tail -1)"
  else
    echo "FAIL $cat: its cases fail"
    echo "$out" | sed 's/^/       /' | tail -20
    status=1
  fi
done
exit "$status"
