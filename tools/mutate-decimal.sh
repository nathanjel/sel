#!/usr/bin/env bash
# Mutation testing for the decimal cores: break them on purpose, and require the
# checks to notice.
#
# At 0.9.2 the C++ multiply rounded both operands to 18 fractional digits
# whenever both had more -- wrong answers -- and every lane was green: the only
# decimal oracle then (tools/decimal-oracle.py) generated operands of at most 12
# integer and 6 fractional digits, so no product ever reached the broken path.
# "0 mismatches" proves something only about the inputs that were generated.
# This lane asks the other question: would we know if the core were wrong?
#
# The mutants are data, in tools/decimal-mutations.json (format in its `note`),
# a handful per host in every decimal core and the numeric fast paths that
# bypass it. Each is applied to a per-host copy of the tree, the host rebuilt
# where it needs it, and the checks run cheapest first until one fails:
# check-decimal on the narrow and the wide oracle, then the numeric conformance
# files, then the host's numeric unit tests. A mutant every check passes
# SURVIVED: a coverage hole, named.
#
#   tools/mutate-decimal.sh [name-substring ...]    grade (SEL_IMPLS narrows the hosts)
#   tools/mutate-decimal.sh --weak [...]            the 0.9.2-era checks only: the
#                                                   narrow oracle, conformance without
#                                                   24 and 32, no unit lane -- the
#                                                   red run that shows what they missed
#   tools/mutate-decimal.sh --list [...]            list the selected mutants
#
# Exit 1 when a mutant survives, fails to apply or build, or a check already
# fails on the unmutated tree. SEL_MUTATE_DECIMAL_KEEP=1 keeps the work
# directory (one log per copy) for a post-mortem.

set -uo pipefail
cd "$(dirname "$0")/.."
exec python3 tools/mutate-decimal.py "$@"
