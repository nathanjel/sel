# Adding a function — the strict lane

Worked example: `ORD_SUFFIX(n)`, returning `1st`, `2nd`, `3rd`, `4th`.

**A function is a change to the language, not to your application.** No host
exposes function registration as public API, and C++ has none at all —
`sel.hpp` says so in as many words: *"The table is fixed at startup — SEL has no
DEFUN."* That is deliberate. Five implementations exist in order to disagree
with each other, and a function that lives in one of them is a function nobody
has tested.

So the files here are not programs, and `tools/check-examples.sh` does not run
them. They are the fragments you paste into each host's builtin table, kept in
one place so they can be read side by side and so the documentation can quote
them without drifting.

## The order of work

```
spec/SPEC.md §7          say what it does          → spec.md here
conformance/*.selt       write the cases; they fail → cases.selt here
js/src/builtins/*.mjs    implement                  → js.mjs
php/src/Builtins/*.php   implement                  → php.php
cpp/sel.cpp              implement                  → cpp.cpp
lisp/src/builtins/*.lisp implement                  → lisp.lisp
python/sel/builtins/*.py implement                  → python.py
tools/check.sh           all green, or it isn't done
```

Never implement in one host and port it later. They arrive together or the
suite has nothing to compare.

## What is *not* in these files

No argument-count check, no type check, no `eval` call, no try/catch.
`nonNegInt(0)` evaluates argument 0 once, requires a whole number ≥ 0, and
raises `E_NOT_INT` or `E_RANGE` against **that argument's** source position.
The Args API is what makes a builtin four lines instead of twenty; see
[docs/EXTENDING.md](../../docs/EXTENDING.md#the-args-api).

## Registering the file

None of the three has an autoloader: a new file must be added to `:components`
in `lisp/sel-lang.asd`, to `php/src/bootstrap.php`, and to the imports in
`python/sel/builtins/__init__.py`.
