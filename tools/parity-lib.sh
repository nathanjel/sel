# Sourced by the parity checks (tools/check-api.sh, tools/check-sqlapi.sh), after
# tools/impls.sh: run one role in every host side by side and say which hosts'
# drivers died, so a crash in one host is reported by name and the others'
# answers are still compared.

# parity_run <role> <workdir> <impl...>: impl_<role> for each host in parallel,
# its stdout in <workdir>/<impl>.txt. A driver that exits non-zero is named on
# stderr with its status and its stdout so far is kept. Returns 1 when any did.
parity_run() {
  local role="$1" work="$2" impl rc crashed=0
  shift 2
  for impl in "$@"; do
    ( sel_slot "impl_$role" "$impl" > "$work/$impl.txt"; echo $? > "$work/$impl.rc" ) &
  done
  wait
  for impl in "$@"; do
    rc="$(cat "$work/$impl.rc" 2>/dev/null || echo 255)"
    if [ "$rc" != 0 ]; then
      echo "$role probe driver for $impl exited with status $rc" >&2
      crashed=1
    fi
  done
  return "$crashed"
}
