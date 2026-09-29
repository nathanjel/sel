# External review — 2026-09-28 (as received)

## JavaScript review

  - High — chained joins can emit SQL for an invalid field read. js/src/sql/translator.mjs:480 maps every join's left binder to the
    original source relation. In R .> LINK(S, X, Y, X["id"] == Y["id"]) .> LINK(T, A, B, A["id"] == B["tid"]), the first joined row
    has no promoted id because both R and S have one. JavaScript evaluation raises E_NO_KEY, but SQL translation emits a second
    join using R.id, potentially returning rows.

  - Medium — oversized native integers bypass the digit limit. js/src/value.mjs:561 converts a bigint directly to text.
    Value.int(10n ** 1000000n) raises the required E_RANGE; Value.fromNative and Program.run accept that same 1,000,001-digit
    integer.

## PHP review

  - High — chained joins can produce a SQL result where PHP evaluation fails. php/src/Sql/Translator.php:981 maps every join's left
    binder to the original source table. With R and S both carrying id, R .> LINK(S, X, Y, X["id"] == Y["id"]) .> LINK(T, A, B,
    A["id"] == B["tid"]) raises E_NO_KEY in PHP: the first joined row has no promoted id. The translator instead emits the second
    join using R.id. This is the same defect confirmed in JavaScript.

  - Medium — some PHP value entry points admit invalid UTF-8 keys. php/src/Value.php:416 installs the first fromNativeRows shape
    without validating its keys; record and shaped also accept such keys. fromNative and set correctly raise E_UTF8 for the same
    key. A later INDEXES call then fails, moving the error away from the input boundary.

## C++ review

  - High — chained joins can emit SQL for a field the interpreter rejects. cpp/sel_sql_translator.cpp:422 maps every left binder to
    the original relation. In a two-join probe where both first-join inputs have id, C++ evaluation raises E_NO_KEY for the second
    join's A["id"]; translation emits a query using R.id. This is the same defect found in JavaScript and PHP.

  - Medium — public record construction bypasses UTF-8 key validation. cpp/sel.cpp:1546 accepts a unique record key containing
    invalid UTF-8, while Value::set raises E_UTF8 for it. The invalid key can therefore enter a value and fail later during
    processing.

  - Medium — Value::num(Dec) bypasses the decimal digit cap. cpp/sel.cpp:1482 accepted a Dec with scale 1,000,001, though the
    specified fractional digit limit is 1,000,000.

## Common Lisp review

  - High — to-native lets callers mutate SEL keys. lisp/src/value.lisp:683 returns the value's key strings in the native alist
    without copying them. Changing "foo" to "boo" in that alist changed the value's key and changed the output of a later run of
    the same compiled RECORD("foo", "x") program. Key lookup could then find neither spelling.

  - High — chained joins can emit SQL where evaluation raises E_NO_KEY. lisp/src/sql/translator.lisp:245 maps every join's left
    binder to the original relation. The two-join probe from the earlier reviews raised E_NO_KEY in Lisp but emitted a second join
    using R.id.

  - Medium — make-num accepts an over-limit decimal object. lisp/src/value.lisp:178 accepted a decimal with scale 1,000,001, beyond
    the 1,000,000 fractional digit limit.

## Python review

  - High — chained joins can emit SQL where evaluation raises E_NO_KEY. python/sel/sql/translator.py:418 maps every left binder to
    the original relation. The two-join probe emits a condition using R.id, while Python evaluation rejects that field read. This
    defect is now confirmed in all five implementations.

  - High — public collection constructors retain caller-owned lists. python/sel/value.py:267 stores the list passed to Value.record
    or Value.shaped, and Value.list (python/sel/value.py:331) does the same. Replacing an item in the caller's list later changes
    the SEL value.

  - Medium — record length mismatches are accepted. python/sel/value.py:270 can build a shape with fewer values than keys, causing
    a later Python IndexError; extra values produce an inconsistent record and are omitted from its dump.

  - Medium — direct record constructors skip key validation. python/sel/value.py:252 accepts a lone-surrogate key that from_native
    rejects with E_UTF8. A later INDEXES call then raises the error.

  - Medium — Value.bin accepts booleans as bytes. python/sel/value.py:241 turns [True] into b01, losing parity with hosts that
    reject a boolean byte.

  - Medium — Value.num(Dec) bypasses the digit cap. python/sel/value.py:303 accepted a decimal object with scale 1,000,001.
