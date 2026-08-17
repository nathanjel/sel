#!/usr/bin/env bash
# Differential fuzzer: generate random programs, run them through every
# implementation, and report every program where any two disagree on the value,
# the error code, or the error position.
#
# This is what catches the divergences hand-written cases miss — decimal rounding
# edges, UTF-8 boundaries, and regex-subset leaks. When it finds one, add the
# minimal case to conformance/ before fixing any host.
#
#   tools/fuzz.sh [count] [seed]

set -euo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

COUNT="${1:-3000}"
SEED="${2:-20260813}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

IMPLS="$(available_impls)"

# One implementation always agrees with itself; that is not a differential test.
if [ "$(echo "$IMPLS" | wc -w)" -lt 2 ]; then
  echo "need at least two implementations to compare, have: ${IMPLS:-none}" >&2
  exit 1
fi

echo "generating $COUNT programs (seed $SEED)..."
node tools/gen-programs.mjs "$COUNT" "$SEED" > "$WORK/corpus.selc"

# An empty corpus makes every comparison vacuously unanimous.
records="$(grep -c '^### ' "$WORK/corpus.selc" || true)"
if [ "$records" -ne "$COUNT" ]; then
  echo "generator produced $records of $COUNT programs" >&2
  exit 1
fi

for impl in $IMPLS; do
  echo "running $impl..."
  impl_batch "$impl" "$WORK/corpus.selc" > "$WORK/$impl.txt"
done

node - "$WORK" "$IMPLS" <<'EOF'
const { readFileSync } = require('node:fs');
const [work, implList] = process.argv.slice(2);
const impls = implList.split(/\s+/).filter(Boolean);

// Read the corpus back rather than regenerating it, so the report quotes exactly
// what was run.
const programs = [];
for (const line of readFileSync(`${work}/corpus.selc`, 'utf8').split('\n')) {
  if (line.startsWith('### ')) programs.push([]);
  else if (programs.length) programs[programs.length - 1].push(line);
}
const sources = programs.map((l) => l.join('\n').replace(/\n$/, ''));

const out = Object.fromEntries(
  impls.map((i) => [i, readFileSync(`${work}/${i}.txt`, 'utf8').split('\n')]),
);

let diffs = 0, hostErrors = 0, selErrors = 0;
for (let i = 0; i < sources.length; i++) {
  for (const impl of impls) {
    if (out[impl][i] && out[impl][i].startsWith('!HOST')) {
      hostErrors++;
      console.log(`HOST-ERROR ${impl}: ${sources[i]}\n  ${out[impl][i]}`);
    }
  }
  const answers = impls.map((impl) => out[impl][i]);
  const agreed = answers.every((a) => a === answers[0]);
  if (agreed) { if (answers[0] && answers[0][0] === '!') selErrors++; continue; }
  diffs++;
  if (diffs <= 25) {
    console.log(`DIFF  ${sources[i]}`);
    for (const impl of impls) console.log(`  ${impl.padEnd(4)}: ${out[impl][i]}`);
  }
}
console.log(`\n${sources.length} programs across ${impls.length} implementations, `
  + `${selErrors} agreed on a SEL error, ${diffs} disagreements, ${hostErrors} host crashes`);
process.exit(diffs || hostErrors ? 1 : 0);
EOF
