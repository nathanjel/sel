# Adding a function — the lazy lane

Worked example: **`FIRST(list, body)`** — the first element for which `body` is
`TRUE`, or TEXT `""` if none matches. Like `FILTER`, but it stops at the first
hit and returns the element rather than a list. The three-argument form
`FIRST(list, X, body)` names the binder, exactly as the core aggregates do.

```sel
FIRST((1, 8, 3, 9), _ > 5)                       # the element 8
FIRST(ITEMS, IT, IT["QTY"] > 0)["SKU"]           # first line that has a quantity
FIRST((1, 2), _ > 5)                             # "" — nothing matched
```

As with [fn-simple](../fn-simple/), these are fragments rather than programs:
a function is a change to the language, implemented in all five hosts together.
See that README for why, and for the order of work.

## What makes this lane harder

A lazy function declares `lazy: true` and reads argument **nodes** instead of
values. Nothing is evaluated for it; it decides what to evaluate, when, and how
many times. Four things it has to get right, and all four are the reason the
strict lane is the default:

1. **Declare `binds: true.`** That is what tells `dependencies()` the second
   argument of the three-argument form is a *binder*, not a variable being read.
   Without it, `FIRST(ITEMS, IT, IT["QTY"] > 0)` reports `IT` as an input field
   the host is expected to supply.
2. **Push a frame per element, and pop it on the way out — including when the
   body raises.** A body that fails must not leave the binder in scope for
   whatever runs next. Every implementation here uses its language's
   try/finally for exactly that.
3. **Bind `_K` as well as the element**, so the body can see the key.
4. **Handle the no-children cases** the way spec/SPEC.md §7.3 specifies: a
   scalar behaves as a one-element list containing itself, and a childless NONE
   is genuinely empty. The second is what `FILTER` returns when nothing matched.

The smallest lazy function is `IF`, which is why `IF` needs no syntax — see
[docs/EXTENDING.md](../../docs/EXTENDING.md#lane-b--a-lazy-ast-function).
