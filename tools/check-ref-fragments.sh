#!/usr/bin/env bash
# Runs the builtin fragments of the reference examples against their cases, in
# the hosts where a fragment can be dropped into a copy of the source tree
# without a compiler: JS, Python, PHP and Lisp. Go has its own lane
# (tools/check-go-fragments.sh), which also vets and gofmt-checks the fragment.
#
#   tools/check-ref-fragments.sh [js|python|php|lisp ...]
#
# examples/fn-simple and examples/fn-complex are REFERENCE categories: per-host
# code that adds a function to SEL itself, quoted by docs/extending.md and
# docs/contributing.md, which runs only inside the host's builtin table, so
# tools/check-examples.sh skips them. Unrun, the JS FIRST fragment shipped
# reading `list.size` (a method) as a number, so it iterated nothing and failed
# its own cases. For each category and host this copies the host's sources
# aside, adds the quoted EXAMPLE region as one more builtin module (with the
# imports the region's comment says it goes beside), and runs
# examples/<cat>/cases.selt with that host's conformance runner -- which the
# stock tree fails, because the function does not exist there.
#
# Not covered here: C++ and Rust, whose fragments compile only inside the
# library's own translation unit / crate; adding them means a rebuild of the
# library per category and is left to those hosts' own lanes.
set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

ROOT="$PWD"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ "$#" -gt 0 ]; then
  HOSTS="$*"
else
  HOSTS=""
  for impl in $(available_impls); do
    case "$impl" in js|python|php|lisp) HOSTS="$HOSTS $impl" ;; esac
  done
fi

region() { awk '/EXAMPLE-END/{f=0} f; /EXAMPLE-BEGIN/{f=1}' "$1"; }

# inject <host> <cat> <dir>: copy the host's sources into <dir> with the region
# registered, and print the conformance command that uses the copy.
inject() {
  local host="$1" cat="$2" dir="$3" src body
  case "$host" in
    js)
      src="examples/$cat/js.mjs"; body="$(region "$src")" || return 1
      cp -r js "$dir/js"
      { grep -q '^import .*\bdefine\b' <<<"$body" || echo "import { define } from '../registry.mjs';"
        grep -q '^import .*\bValue\b' <<<"$body" || echo "import { Value } from '../value.mjs';"
        printf '%s\n' "$body"; } > "$dir/js/src/builtins/zz-example.mjs"
      echo "import './zz-example.mjs';" >> "$dir/js/src/builtins/index.mjs"
      echo "node $dir/js/bin/conformance.mjs" ;;
    python)
      src="examples/$cat/python.py"; body="$(region "$src")" || return 1
      cp -r python "$dir/python"
      find "$dir/python" -name __pycache__ -prune -exec rm -rf {} +
      { echo "from ..registry import define"
        echo "from ..value import NONE, Value, elements"
        printf '%s\n' "$body"; } > "$dir/python/sel/builtins/zz_example.py"
      echo "from . import zz_example  # noqa: F401" >> "$dir/python/sel/builtins/__init__.py"
      echo "env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$dir/python python3 $dir/python/bin/conformance.py" ;;
    php)
      src="examples/$cat/php.php"; body="$(region "$src")" || return 1
      cp -r php "$dir/php"
      { printf '<?php\ndeclare(strict_types=1);\nnamespace Sel\\Builtins;\n'
        printf 'use Sel\\Args;\nuse Sel\\Context;\nuse Sel\\Registry;\nuse Sel\\Value;\n'
        printf 'use function Sel\\fail;\n'
        printf '(static function (): void {\n%s\n})();\n' "$body"; } > "$dir/php/src/Builtins/ZzExample.php"
      echo "require_once __DIR__ . '/Builtins/ZzExample.php';" >> "$dir/php/src/bootstrap.php"
      echo "sel_php $dir/php/bin/conformance" ;;
    lisp)
      src="examples/$cat/lisp.lisp"; body="$(region "$src")" || return 1
      { echo "(in-package #:sel)"; printf '%s\n' "$body"; } > "$dir/zz-example.lisp"
      echo "sbcl --dynamic-space-size 4GB --noinform --disable-debugger --non-interactive" \
           "--load $ROOT/lisp/bin/boot.lisp --load $ROOT/lisp/bin/conformance.lisp" \
           "--load $dir/zz-example.lisp --eval (sel-cli:main) --end-toplevel-options" ;;
    *) echo "no fragment lane for host: $host" >&2; return 2 ;;
  esac
}

status=0
for host in $HOSTS; do
  for cat in fn-simple fn-complex; do
    dir="$WORK/$host-$cat"
    mkdir -p "$dir"
    if ! cmd="$(inject "$host" "$cat" "$dir")" || [ -z "$cmd" ]; then
      echo "FAIL $host $cat: could not take the EXAMPLE region"; status=1; continue
    fi
    # shellcheck disable=SC2086  # the command is a word list built above
    if out="$(sel_slot $cmd "examples/$cat/cases.selt" 2>&1)"; then
      echo "fragments: $host $cat $(echo "$out" | tail -1)"
    else
      echo "FAIL $host $cat: the fragment's cases fail"
      echo "$out" | sed 's/^/       /' | tail -20
      status=1
    fi
  done
done
[ -n "${HOSTS// }" ] || echo "fragments: none of js, python, php, lisp is on the roster; nothing run"
exit "$status"
