#!/usr/bin/env bash
# The six top-level host examples run to completion.
#
#   tools/check-host-examples.sh
#
# examples/host-{js.mjs,php.php,python.py} and examples/integration-{js.mjs,
# php.php,python.py} ship in the packages (package.json `files`, the pyproject
# sdist, the Packagist archive) and are the first code a reader copies. They are
# programs rather than worked-example categories, so tools/check-examples.sh does
# not reach them, and until this lane existed nothing did: two of them died on
# the first error they meant to demonstrate (they caught the host's own
# exception type after SPEC §8 made the refusal a SelError) and nobody noticed.
#
# The contract is the exit status and a clean stderr; their output is prose for a
# reader and is not compared.
set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

IMPLS=" $(available_impls) "
status=0
ran=0
run() {
  local host="$1" file="$2"; shift 2
  case "$IMPLS" in *" $host "*) ;; *) echo "skip $file ($host not available)"; return ;; esac
  ran=$((ran + 1))
  if sel_slot "$@" "$file" > "$WORK/out" 2> "$WORK/err" < /dev/null; then
    if [ -s "$WORK/err" ]; then
      echo "FAIL $file: exit 0 but wrote to stderr:"
      head -5 "$WORK/err" | sed 's/^/       /'
      status=1
    else
      echo "ok   $file"
    fi
  else
    echo "FAIL $file: exit $?"
    tail -5 "$WORK/err" | sed 's/^/       /'
    status=1
  fi
}
for f in examples/host-js.mjs examples/integration-js.mjs; do run js "$f" node; done
for f in examples/host-php.php examples/integration-php.php; do run php "$f" sel_php; done
for f in examples/host-python.py examples/integration-python.py; do
  run python "$f" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/python" python3
done
# A file added beside them without a line above would go unrun again.
for f in examples/host-* examples/integration-*; do
  case "$f" in
    examples/host-js.mjs|examples/integration-js.mjs|examples/host-php.php|examples/integration-php.php|\
    examples/host-python.py|examples/integration-python.py) ;;
    *) echo "FAIL $f: a top-level host example this lane does not run"; status=1 ;;
  esac
done
[ "$ran" -gt 0 ] || echo "host examples: none of js, php, python is on the roster; nothing run"
exit "$status"
