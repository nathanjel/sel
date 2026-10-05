#!/usr/bin/env bash
# The host registry is complete: every configuration of the default roster (and
# python-wheel) has an arm in every role function of tools/impls.sh and a line
# in impl_available, and every role function refuses an unknown configuration.
#
#   tools/check-registry.sh
#
# The registry is a role x configuration matrix written by hand -- one `case`
# per `impl_<role>` function -- because the commands are not uniform (slot
# wrappers, env prefixes, multi-step lanes, a role that does not apply). A
# configuration added to SEL_DEFAULT_IMPLS without an arm in some role would
# fall through to "unknown implementation" in that role only, and the tools that
# treat a role as optional would report it as a quiet skip; this check makes
# that a gate failure instead. It reads the file; it runs nothing.
set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

status=0
configs="$SEL_DEFAULT_IMPLS python-wheel"
# Roles that exist for a subset by design, with the subset.
declare -A SUBSET=(
  [impl_example]="js php cpp lisp python python-wheel rust go"   # see example_impls
)

roles="$(grep -oE '^impl_[a-z_]+\(\)' tools/impls.sh | tr -d '()' | grep -v '^impl_skip_note$')"
for role in $roles; do
  body="$(awk -v f="$role()" '$1 == f { on = 1 } on { print } on && /^}/ { exit }' tools/impls.sh)"
  # The case labels: `    a|b|c)` at the start of an arm.
  labels=" $(grep -oE '^ +[a-z][a-z|-]*\)' <<<"$body" | tr -d ' )' | tr '|' ' ' | tr '\n' ' ') "
  want="${SUBSET[$role]:-$configs}"
  for c in $want; do
    case "$labels" in *" $c "*) ;; *)
      echo "FAIL $role has no arm for $c"; status=1 ;;
    esac
  done
  if [ "$role" != impl_available ] && ! grep -q 'unknown implementation\|no worked example' <<<"$body"; then
    echo "FAIL $role does not refuse an unknown configuration"; status=1
  fi
done
# example_impls must name exactly impl_example's arms.
ex="$(sed -n '/^example_impls()/,/^}/p' tools/impls.sh | grep -oE '^ +[a-z][a-z|-]*\)' | tr -d ' )' | tr '|' ' ')"
for c in ${SUBSET[impl_example]}; do
  case " $ex " in *" $c "*) ;; *) echo "FAIL example_impls does not list $c"; status=1 ;; esac
done
[ "$status" -eq 0 ] && echo "registry: $(wc -w <<<"$roles") roles, each with an arm for: $configs"
exit "$status"
