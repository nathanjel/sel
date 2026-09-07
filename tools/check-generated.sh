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

# The SEL->SQL dialect map. One authored source, seven renderings.
check_group "sql dialect map" "node tools/gen-sql-map.mjs" \
  sql/dialects/*.json tools/gen-sql-map.mjs \
  -- \
  php/src/Sql/MapData.php python/sel/sql/_map.py js/src/sql/_map.mjs \
  cpp/sel_sql_map_data.cpp php/bin/MapReplay.php python/bin/map_replay.py \
  js/bin/map-replay.mjs

# The SQL case tables, so a clone can run the suite without Node.
check_group "sql case data" "node tools/gen-sql-cases.mjs" \
  sql/cases/*.sqlt tools/gen-sql-cases.mjs \
  -- \
  php/bin/CaseData.php python/bin/case_data.py js/bin/case-data.mjs

if [ "$status" -ne 0 ]; then
  echo "GENERATED ARTIFACTS ARE NOT CURRENT — run the command(s) above" >&2
fi
exit "$status"
