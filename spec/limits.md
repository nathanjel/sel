# The limits and error catalogue — `spec/limits.json`

The normative numbers (§6.4's depth cap, argument caps and value caps, §7.8's
regex caps, §5.3's division scale) and the language's error identifiers, in one machine-readable file.

**It is not a second authority.** `spec/SPEC.md` and `spec/errors.md` define
these; this file only restates them so that hosts can be held to them. The
generator refuses to render it unless every number here is stated in the spec
and every code here has its row in `spec/errors.md` under the phase the file
claims (`compile`, `run`, or `both` for `E_DEPTH`, which each half raises).
Change the spec first; the check tells you when this file has fallen behind.

| Key | Meaning |
|---|---|
| `limits.<NAME>.value` | the number; `spec` names the section, `meaning` the sentence |
| `errors.<CODE>.phase` | `compile`, `run` or `both` |

Renderings, all committed: `js/src/_limits.mjs`, `python/sel/_limits.py`,
`php/src/Limits.php`, `cpp/sel_limits.hpp`, `lisp/src/limits.lisp`,
`go/internal/limits/limits.go`, `rust/src/limits.rs` and
`docs/reference/limits.md`. Every host reads every number from its rendering
rather than writing it as a literal — the depth and value caps, the `ROUND`
scale, `POWER` exponent and regex quantifier caps of §6.4's argument table, the
§7.8 ambiguity budget and analysis caps, `DIV_SCALE` — and a host constant that
follows from one of them (the bit length that guards the integer-digit cap, the
SQL layer's constant-folding depth margin) is derived from it in integer
arithmetic, so the hosts cannot drift. `tools/check-error-codes.sh` then reads
every host's sources and requires that the codes a host raises are exactly the
catalogue's (plus the SQL layer's own, from `sql/errors.md`), so a new error can
neither be raised unlisted nor listed unraised.

What stays out, on purpose: host budgets that are not language facts — cache
sizes, freelist pools, shape-interning caps, the SQL layer's per-dialect
limits. (`MAX_SQL_NODES` is here because it is a fact of the translator's
contract, not of any host: every host's translator must refuse at the same
size.) `conformance/10-limits.selt` and the API probes keep their literal
`200`s: those are independent oracles of the same fact, and an oracle that
read its expected answer from the manifest it is checking would prove nothing.
