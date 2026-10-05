<?php
// The two size caps of SPEC §6.4, checked BEFORE anything is allocated (the
// same module as js/src/budget.mjs and python/sel/_budget.py).
//
// MAX_TEXT_LEN bounds the code points of a TEXT value and the bytes of a BIN
// value that an operation may build; MAX_COLLECTION bounds the children of a
// collection an operation may build. Exceeding either is E_RANGE at the node
// that builds the value, after its arguments have been evaluated and coerced,
// and an EMPTY result is never too large: `REPEAT("", 10^30)` is "".
//
// A caller on a per-row or per-node path guards the call with the plain
// comparison (for text, against the byte length, which bounds the code point
// length from above) and calls in only past the cap, so the hot path pays one
// comparison and the rule and its message live here.

declare(strict_types=1);

namespace Sel;

final class Budget
{
    /**
     * E_RANGE at `$pos` when a TEXT of `$len` code points, or a BIN of `$len`
     * bytes, would be built.
     *
     * @param array<string,mixed>|null $pos
     */
    public static function checkText(int $len, ?array $pos, string $what = 'the result'): void
    {
        if ($len > Limits::MAX_TEXT_LEN) {
            fail('E_RANGE', "{$what} would be longer than " . Limits::MAX_TEXT_LEN, $pos);
        }
    }

    /**
     * E_RANGE at `$pos` when a collection of `$n` children would be built.
     *
     * @param array<string,mixed>|null $pos
     */
    public static function checkCollection(int $n, ?array $pos, string $what = 'the collection'): void
    {
        if ($n > Limits::MAX_COLLECTION) {
            fail('E_RANGE', "{$what} would have more than " . Limits::MAX_COLLECTION . ' elements', $pos);
        }
    }
}
