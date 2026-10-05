#!/usr/bin/env bash
# Every generated artifact a released package carries must be present and
# current.
#
# This is the rule that makes those files committed rather than built: a release
# ships them ALREADY GENERATED, so a C++, PHP, Python or Lisp user never needs
# Node installed to get a working library. sql/dialects/*.json is the one place
# the SEL->SQL map is authored, and tools/gen-sql-map.mjs renders it into each
# host's own source language. A release cut with a stale rendering would ship
# hosts that quietly disagree about the map, and nothing downstream could tell.
#
# Two signals, because they fail in different places:
#
#   presence   an artifact that is missing is always a failure.
#   content    `--check` re-runs the emitters and diffs. Authoritative, and
#              needs Node.
#   mtime      an artifact older than what it was generated from. Needs nothing,
#              so it still answers on a release machine with no Node — which is
#              exactly where a stale artifact would otherwise go unnoticed.
#
# When Node is present, content decides: an artifact whose bytes are what the
# generator produces today is current, whatever `git clone` did to its
# timestamp. When Node is absent, mtime is all there is, and it is treated as
# the answer rather than skipped.
#
#   tools/check-generated.sh          check them
#
# tools/check-sql-map.sh and tools/check-sql-cases.sh check the same content and
# SKIP when Node is missing, which is right for a working tree and wrong before
# a release. This is the one that does not skip.

set -uo pipefail
cd "$(dirname "$0")/.."

status=0

if command -v node >/dev/null 2>&1; then
  HAVE_NODE=yes
else
  HAVE_NODE=no
  echo "generated: node is not installed — falling back to timestamps alone"
fi

# Newest modification time among the arguments, in epoch seconds. Absent files
# do not count: presence is checked separately and reported for itself.
newest() {
  local best=0 t f
  for f in "$@"; do
    [ -e "$f" ] || continue
    t=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null)
    [ -n "${t:-}" ] && [ "$t" -gt "$best" ] && best="$t"
  done
  echo "$best"
}

# Which of the arguments is newest — for a message that names the file the
# author actually touched.
newest_name() {
  local best=0 bestf="" t f
  for f in "$@"; do
    [ -e "$f" ] || continue
    t=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null)
    [ -n "${t:-}" ] && [ "$t" -gt "$best" ] && { best="$t"; bestf="$f"; }
  done
  echo "$bestf"
}

# check_group <label> <command> <sources...> -- <outputs...>
check_group() {
  local label="$1" command="$2"; shift 2
  local sources=() outputs=() seen=no a
  for a in "$@"; do
    if [ "$a" = "--" ]; then seen=yes; continue; fi
    if [ "$seen" = no ]; then sources+=("$a"); else outputs+=("$a"); fi
  done

  local bad=0 out
  for out in "${outputs[@]}"; do
    if [ ! -e "$out" ]; then
      echo "generated: $label — $out is missing" >&2
      bad=1
    fi
  done

  if [ "$bad" -eq 0 ]; then
    if [ "$HAVE_NODE" = yes ]; then
      # Content is authoritative. The generator prints its own diagnosis.
      if ! $command --check >/dev/null 2>&1; then
        echo "generated: $label — the artifacts are not what the generator produces" >&2
        $command --check 2>&1 | sed 's/^/           /' >&2
        bad=1
      fi
    else
      local src_time; src_time=$(newest "${sources[@]}")
      local src_name; src_name=$(newest_name "${sources[@]}")
      for out in "${outputs[@]}"; do
        local out_time; out_time=$(newest "$out")
        if [ "$out_time" -lt "$src_time" ]; then
          echo "generated: $label — $out is older than $src_name" >&2
          bad=1
        fi
      done
    fi
  fi

  if [ "$bad" -ne 0 ]; then
    echo >&2
    echo "    $command" >&2
    echo >&2
    status=1
  else
    echo "generated: $label — ${#outputs[@]} artifact(s) current"
  fi
}

# The SEL->SQL dialect map. One authored source, ten renderings.
#
# Every output the generator writes has to be listed here, not just the ones a
# release is thought to need. With Node present the `--check` below is content
# authoritative and would catch an unlisted file anyway; without it this falls
# back to comparing timestamps, and a timestamp is only compared for a file
# somebody remembered to name. That fallback is not a corner case -- it is the
# whole point of the gate, since a C++ or Lisp consumer is precisely the one
# with no JS tooling to run the generator with.
check_group "sql dialect map" "node tools/gen-sql-map.mjs" \
  sql/dialects/*.json spec/builtins.json tools/gen-sql-map.mjs \
  -- \
  php/src/Sql/MapData.php python/sel/sql/_map.py js/src/sql/_map.mjs \
  cpp/sel_sql_map_data.cpp lisp/src/sql/map-data.lisp \
  go/sel/sql/map_data_gen.go rust/src/sql/map_data.rs \
  php/bin/MapReplay.php python/bin/map_replay.py js/bin/map-replay.mjs \
  cpp/bin/map_replay.cpp lisp/bin/map-replay.lisp \
  go/bin/sqlreplay/replay_data_gen.go rust/dev/src/bin/map_replay_data.rs

# The builtin manifest: names, arities, extra arity rules and lazy/binds flags,
# authored once and rendered into the table each host checks itself against at
# startup. A stale rendering is a host holding its builtins to yesterday's
# manifest.
check_group "builtin manifest" "node tools/gen-builtins.mjs" \
  spec/builtins.json tools/gen-builtins.mjs \
  -- \
  js/src/_builtin_manifest.mjs python/sel/_builtin_manifest.py \
  php/src/BuiltinManifest.php cpp/sel_builtin_manifest.hpp \
  lisp/src/builtin-manifest.lisp go/internal/manifest/builtins.go \
  rust/src/manifest/builtins.rs \
  docs/reference/builtins.md

# The math-operation manifest: what the native math plans compile, authored
# once and rendered into each host's vocabulary table.
check_group "math-operation manifest" "node tools/gen-math-ops.mjs" \
  spec/math-ops.json spec/builtins.json tools/gen-math-ops.mjs \
  -- \
  js/src/_math_ops.mjs python/sel/_math_ops.py php/src/MathOps.php \
  cpp/sel_math_ops.hpp lisp/src/math-ops.lisp go/internal/mathops/math_ops.go \
  rust/src/math_ops.rs \
  docs/internals/math-ops.md

# The lexicon: reserved words, operator tokens and the precedence table,
# checked against spec/grammar.md and spec/SPEC.md §5 and rendered into every
# host's lexer and parser vocabulary.
check_group "lexicon" "node tools/gen-lexicon.mjs" \
  spec/lexicon.json spec/grammar.md spec/SPEC.md tools/gen-lexicon.mjs \
  -- \
  js/src/_lexicon.mjs python/sel/_lexicon.py php/src/Lexicon.php \
  cpp/sel_lexicon.hpp lisp/src/lexicon.lisp go/internal/lexicon/lexicon.go \
  rust/src/lexicon.rs \
  docs/reference/lexicon.md

# The limits and error catalogue, checked against the spec text and rendered
# into each host's constants.
check_group "limits and error catalogue" "node tools/gen-limits.mjs" \
  spec/limits.json spec/SPEC.md spec/errors.md tools/gen-limits.mjs \
  -- \
  js/src/_limits.mjs python/sel/_limits.py php/src/Limits.php \
  cpp/sel_limits.hpp lisp/src/limits.lisp go/internal/limits/limits.go \
  rust/src/limits.rs \
  docs/reference/limits.md

# The SQL case tables, so a clone can run the suite without Node.
check_group "sql case data" "node tools/gen-sql-cases.mjs" \
  sql/cases/*.sqlt tools/gen-sql-cases.mjs \
  -- \
  php/bin/CaseData.php python/bin/case_data.py js/bin/case-data.mjs \
  cpp/bin/case_data.cpp lisp/bin/case-data.lisp \
  go/bin/sqlt/case_data_gen.go rust/dev/src/bin/sqlt/case_data.rs

# The support desk's PostgreSQL seed, rendered from the SEL program that
# examples/memory-complex runs in memory: stale, and the two examples would be
# comparing different data.
check_group "usage example seed" "node tools/gen-usage-seed.mjs" \
  examples/lib/tickets-generate.sel tools/gen-usage-seed.mjs \
  -- \
  examples/sql-complex/seed.postgresql.sql

# The decimal conformance cases, with every expectation computed by the exact
# oracle. A stale file would hold every host to yesterday's oracle. Python, not
# Node, renders them; the gate needs Python anyway.
check_group "decimal conformance cases" "python3 tools/gen-decimal-cases.py" \
  tools/gen-decimal-cases.py tools/decimal-oracle-exact.py \
  -- \
  conformance/24-decimal-boundaries.selt conformance/32-numeric-plans.selt

# The regex-ambiguity cases: the reference validator's verdicts and Python re's
# match results, pinned.
check_group "regex ambiguity cases" "python3 tools/gen-regex-ambiguity-cases.py" \
  tools/gen-regex-ambiguity-cases.py tools/regex-ambiguity-ref.py \
  -- \
  conformance/28b-regex-ambiguity.selt

# The SQL scope and literal-slot cases: every expectation is the JS translator's
# rendering of an alpha-equivalent control (tools/sql-scope-ref.mjs), so this
# group also needs the JS SQL layer current.
check_group "sql scope cases" "python3 tools/gen-sql-scope-cases.py" \
  tools/gen-sql-scope-cases.py tools/sql-scope-ref.mjs \
  -- \
  sql/cases/48-scope-and-slots.sqlt

if [ "$status" -ne 0 ]; then
  echo "GENERATED ARTIFACTS ARE NOT CURRENT — run the command(s) above" >&2
fi
exit "$status"
