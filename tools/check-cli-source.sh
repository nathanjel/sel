#!/usr/bin/env bash
# The command line's treatment of source BYTES, in every host.
#
# The conformance suite is text-in: a .selt file cannot carry an invalid byte or
# a bare CR, and a host API is handed a string that some other layer already
# decoded. The command line is where a person's file becomes source, and it is
# where four of the five hosts mangled it -- Python read a file in universal-
# newline mode (CRLF inside a literal became LF, and a lone CR shifted every line
# number), JS and Python replaced or choked on invalid UTF-8 instead of raising
# E_UTF8, Lisp died on a stream-decoding error, and every host that did raise
# E_UTF8 said `line 0 column 0`. spec/SPEC.md §2 fixes all of it: source is read
# as bytes, unchanged, and an invalid one is E_UTF8 at the first invalid unit,
# counted in code points.
#
#   tools/check-cli-source.sh
#
# Every fixture is a byte string written with printf; the expectation is the
# first line the CLI prints up to the message ("E_UTF8 at line 2 column 4"), or
# the printed value. Messages are free to differ and are not compared.

set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

IMPLS="$(available_impls)"

# name | printf format of the file | expected first line
read -r -d '' FIXTURES <<'FIXTURES_END' || true
crlf-inside-a-literal|A = "a\r\nb";\nLEN(A)|4
cr-is-not-a-line-end|A = 1 # c\r+ 2\r\nA|E_SYNTAX at line 2 column 1
cr-inside-a-literal|LEN("a\rb")|3
bare-invalid-byte|\xff|E_UTF8 at line 1 column 1
invalid-byte-in-a-literal|"a\xffb"|E_UTF8 at line 1 column 3
invalid-byte-on-line-two|1 +\n "a\xffb"|E_UTF8 at line 2 column 4
invalid-after-multibyte|"\xc5\x82\xff"|E_UTF8 at line 1 column 3
truncated-sequence-at-eof|"\xe2\x82|E_UTF8 at line 1 column 2
overlong-encoding|"\xc0\x80"|E_UTF8 at line 1 column 2
encoded-surrogate|"\xed\xa0\x80"|E_UTF8 at line 1 column 2
valid-multibyte-is-counted-in-code-points|"\xc5\x82" & @|E_SYNTAX at line 1 column 7
FIXTURES_END

failures=0
while IFS='|' read -r name body want; do
  [ -n "${name:-}" ] || continue
  f="$WORK/$name.sel"
  # shellcheck disable=SC2059
  printf "$body" > "$f"
  for impl in $IMPLS; do
    out="$(impl_cli "$impl" "$f" 2>&1 </dev/null | head -1)"
    got="${out%%:*}"
    # A value has no message part; take the whole line.
    case "$want" in E_*) ;; *) got="$out" ;; esac
    if [ "$got" != "$want" ]; then
      printf 'FAIL %-40s %-14s want %q, got %q\n' "$name" "$impl" "$want" "${out:0:80}"
      failures=$((failures + 1))
    fi
  done
done <<< "$FIXTURES"

if [ "$failures" -gt 0 ]; then
  echo "$failures CLI source check(s) failed"
  exit 1
fi
echo "CLI source bytes: every fixture agrees across: $IMPLS"
