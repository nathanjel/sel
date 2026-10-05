#!/usr/bin/env bash
# The `sel` command line in every host: its treatment of source BYTES, and the
# CLI contract docs/usage/repl.md writes down (flags, exit statuses, streams, the
# `sel: ` prefix, the REPL's prompt and blank lines, an empty --deps list).
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
# Source bytes: every fixture is a byte string written with printf; the expectation is the
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
# One background job per host; each host's fixtures run in order.
source_bytes() {
  local impl="$1" name body want f out got
  while IFS='|' read -r name body want; do
    [ -n "${name:-}" ] || continue
    f="$WORK/$name.sel"
    out="$(impl_cli "$impl" "$f" 2>&1 </dev/null | head -1)"
    got="${out%%:*}"
    # A value has no message part; take the whole line.
    case "$want" in E_*) ;; *) got="$out" ;; esac
    if [ "$got" != "$want" ]; then
      printf 'FAIL %-40s %-14s want %q, got %q\n' "$name" "$impl" "$want" "${out:0:80}"
    fi
  done <<< "$FIXTURES"
}
while IFS='|' read -r name body want; do
  [ -n "${name:-}" ] || continue
  # shellcheck disable=SC2059
  printf "$body" > "$WORK/$name.sel"
done <<< "$FIXTURES"
for impl in $IMPLS; do
  sel_slot source_bytes "$impl" > "$WORK/bytes.$impl" 2>&1 &
done
wait
for impl in $IMPLS; do
  cat "$WORK/bytes.$impl"
  failures=$((failures + $(grep -c '^FAIL' "$WORK/bytes.$impl")))
done

# --- the CLI contract (docs/usage/repl.md, "The `sel` command line") ----------
# Each row runs one command line with a byte-exact stdin and pins the exit
# status, the exact standard output, and how standard error starts ('' = it must
# be empty). Nothing may look like a host crash: a stack trace, an unhandled-
# condition banner, a PHP warning, a signal.
CRASH_MARK='(Traceback|Unhandled|node:|file:///|Warning|Notice|Fatal error|Exception|panic|goroutine|SB-|#<|Segmentation|core dumped)'
VERSION="$(sed -n 's/^  "version": "\(.*\)",$/\1/p' package.json)"
mkdir -p "$WORK/a-directory"
printf '1 + 1' > "$WORK/ok.sel"
MISSING_PATH="$WORK/no-such-dir/no-such-file.sel"

# contract <label> <stdin printf-format> <exit> <stdout printf-format> <stderr prefix> -- <args...>
# A stdout of '*' means "any non-empty text" (usage text is the host's own).
contract() {
  local label="$1" stdin="$2" want_rc="$3" want_out="$4" want_err="$5"; shift 5
  [ "${1:-}" != -- ] || shift
  local impl="$TABLE_IMPL" rc so se want
  so="$WORK/rows/$impl.$label.so"; se="$WORK/rows/$impl.$label.se"
  # shellcheck disable=SC2059
  printf "$stdin" > "$WORK/rows/$impl.$label.in"
  # shellcheck disable=SC2059
  want="$(printf -- "$want_out"; echo x)"; want="${want%x}"
  {
    impl_cli "$impl" "$@" < "$WORK/rows/$impl.$label.in" > "$so" 2> "$se"; rc=$?
    local got; got="$(cat "$so"; echo x)"; got="${got%x}"
    local why=""
    [ "$rc" = "$want_rc" ] || why="exit $rc, want $want_rc"
    if [ "$want_out" = '*' ]; then
      [ -n "$why" ] || [ -n "$got" ] || why="stdout empty, want usage text"
    else
      [ -n "$why" ] || [ "$got" = "$want" ] || why="stdout $(printf %q "${got:0:80}"), want $(printf %q "$want")"
    fi
    if [ -z "$why" ]; then
      if [ -z "$want_err" ]; then
        [ ! -s "$se" ] || why="stderr not empty: $(printf %q "$(head -c 100 "$se")")"
      else
        case "$(head -c 400 "$se")" in "$want_err"*) ;; *) why="stderr $(printf %q "$(head -c 100 "$se")"), want it to start $(printf %q "$want_err")" ;; esac
      fi
    fi
    [ -n "$why" ] || ! grep -Eq "$CRASH_MARK" "$se" || why="host crash text on stderr: $(printf %q "$(head -c 100 "$se")")"
    if [ -n "$why" ]; then
      printf 'FAIL cli %-34s %-14s %s\n' "$label" "$impl" "$why"
    fi
  }
}

# Every row is its own background job under a slot: each starts an interpreter,
# and thirty rows times nine configurations one after another is minutes, most
# of it Lisp start-up.
mkdir -p "$WORK/rows"
row() { sel_slot contract "$@" > "$WORK/rows/$TABLE_IMPL.$1.out" 2>&1 & }
table() {
  #        label                           stdin         exit stdout            stderr starts
  row "e-without-expression"          ''            2    ''                'sel: -e needs an expression' -- -e
  row "deps-e-without-expression"     ''            2    ''                'sel: -e needs an expression' -- --deps -e
  row "help"                          ''            0    '*'               ''  -- --help
  row "help-short"                    ''            0    '*'               ''  -- -h
  row "version"                       ''            0    "sel $VERSION\\n" ''  -- --version
  row "unknown-option"                ''            2    ''                'sel: unknown option --no-such-flag' -- --no-such-flag
  row "unknown-option-after-e"        ''            2    ''                'sel: unknown option --no-such-flag' -- -e 1 --no-such-flag
  row "extra-argument-after-e"        ''            2    ''                'sel: unexpected argument extra' -- -e 1 extra
  row "extra-argument-after-file"     ''            2    ''                'sel: unexpected argument extra' -- "$WORK/ok.sel" extra
  row "missing-file"                  ''            1    ''                "sel: cannot read $MISSING_PATH" -- "$MISSING_PATH"
  row "directory"                     ''            1    ''                "sel: cannot read $WORK/a-directory" -- "$WORK/a-directory"
  row "deps-missing-file"             ''            1    ''                "sel: cannot read $MISSING_PATH" -- --deps "$MISSING_PATH"
  row "eval-error"                    ''            1    ''                'E_SYNTAX at line 1 column 4: ' -- -e '1 +'
  row "file"                          ''            0    '2\n'             ''  -- "$WORK/ok.sel"
  row "deps-empty-prints-nothing"     ''            0    ''                ''  -- --deps -e '1'
  row "deps-sorted-one-per-line"      ''            0    'A\nB\n'          ''  -- --deps -e 'B + A + B'
  row "deps-file"                     ''            0    ''                ''  -- --deps "$WORK/ok.sel"
  row "render-null"                   ''            0    '-\n'             ''  -- -e 'NULL'
  row "render-bool"                   ''            0    'FALSE\n'         ''  -- -e '1 == 2'
  row "render-bin"                    ''            0    'bin:6162\n'      ''  -- -e 'TO_UTF8("ab")'
  row "render-text-and-number"        ''            0    'a b\n'           ''  -- -e '"a b"'
  row "render-structure"              ''            0    '-{"1"=t"1", "2"=t"a"}\n' '' -- -e '(1, "a")'
  row "repl-pipe-has-no-prompt"       'A = 1\nA + 1\n' 0 '1\n2\n'          ''
  row "repl-error-to-stderr"          '1 +\n2\n'    0    '2\n'             'E_SYNTAX at line 1 column 4: '
  row "repl-sel-blank-lines-skipped"  ' \t\r\n\n1\n' 0   '1\n'             ''
  row "repl-nbsp-line-is-a-program"   '\xc2\xa0\n1+1\n' 0 '2\n'            'E_SYNTAX at line 1 column 1: '
  row "repl-vt-line-is-a-program"     '\v\n1+1\n'   0    '2\n'             'E_SYNTAX at line 1 column 1: '
  row "repl-ideographic-space-line"   '\xe3\x80\x80\n1+1\n' 0 '2\n'        'E_SYNTAX at line 1 column 1: '
  row "repl-last-line-without-newline" 'A = 2\nA * 3' 0  '2\n6\n'          ''
}
for impl in $IMPLS; do TABLE_IMPL="$impl" table; done
wait
for impl in $IMPLS; do
  cat "$WORK/rows/$impl".*.out
  failures=$((failures + $(cat "$WORK/rows/$impl".*.out | grep -c '^FAIL')))
done

# --functions is a listing all seven print byte for byte alike.
ref_functions=""
for impl in $IMPLS; do
  out="$(impl_cli "$impl" --functions < /dev/null 2>&1)"
  if [ -z "$ref_functions" ]; then ref_functions="$out"; ref_impl="$impl"
  elif [ "$out" != "$ref_functions" ]; then
    printf 'FAIL cli %-34s %-14s differs from %s\n' "functions" "$impl" "$ref_impl"
    failures=$((failures + 1))
  fi
done

if [ "$failures" -gt 0 ]; then
  echo "$failures CLI check(s) failed"
  exit 1
fi
echo "CLI source bytes and the CLI contract: every fixture agrees across: $IMPLS"
