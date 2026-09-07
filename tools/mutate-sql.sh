#!/usr/bin/env bash
# Mutation testing for the SQL layer: break it on purpose, and require the checks
# to notice.
#
# Every other tool here answers "is the code right?". This one answers "would we
# know if it weren't?", which is a different question and the one that was never
# asked. The slot invariant added after the code review was a no-op: it was
# committed, trusted, and did not fire when the bug it existed for was put back
# by hand. Nothing said so, because nothing tried.
#
# The mutations are data, in sql/mutations.json, each one a defect this layer has
# actually shipped or a plausible neighbour of one. For each, the tree is copied,
# the mutation applied, and the checks run in order until one fails. A mutation
# every check survives is a HOLE, reported by name.
#
#   tools/mutate-sql.sh [name-substring ...]
#
# The oracle checks need a database; without one, a mutation that survives sqlt
# and sqldoc is reported as skipped rather than as a hole, because the check that
# would have caught it was not run. See sql/oracle/README.md.

set -uo pipefail
cd "$(dirname "$0")/.."
exec python3 tools/mutate-sql.py "$@"
