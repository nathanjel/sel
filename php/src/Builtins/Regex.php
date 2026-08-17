<?php
// The portable regex subset. See spec/SPEC.md §7.8. Ported from
// js/src/builtins/regex.mjs — the validator must reject exactly the same
// patterns in both hosts, or the whole point is lost.
//
// PHP compiles with `usD`: `u` for code point matching (which leaves \d \w \s
// ASCII, as ECMAScript's `u` also does), `s` because dotall is permanently on,
// and `D` so that `$` does not also match before a trailing newline the way PCRE
// otherwise would.

declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\Registry;
use Sel\Utf8;
use Sel\Value;

use function Sel\fail;

final class Regex
{
    private const MAX_QUANTIFIER = 65535;   // PCRE2's own hard limit

    /**
     * \d, \w and \s are rewritten into explicit ASCII classes rather than passed
     * through. PHP's `u` modifier turns on PCRE2's UCP, which makes \d match
     * Arabic-Indic digits and \w match accented letters, while ECMAScript's `u`
     * leaves both ASCII. Expanding them here makes the guarantee structural
     * instead of dependent on a library flag neither host fully controls.
     */
    private const EXPAND_OUTSIDE = [
        'd' => '[0-9]', 'D' => '[^0-9]',
        'w' => '[0-9A-Za-z_]', 'W' => '[^0-9A-Za-z_]',
        's' => '[ \t\n\r\f\x0b]', 'S' => '[^ \t\n\r\f\x0b]',
    ];
    private const EXPAND_INSIDE = ['d' => '0-9', 'w' => '0-9A-Za-z_', 's' => ' \t\n\r\f\x0b'];

    /**
     * \v is excluded: in PCRE it means "any vertical whitespace", in ECMAScript
     * it means U+000B. Same spelling, different language.
     */
    private const CONTROL_ESCAPES = ['n', 'r', 't', 'f'];
    /** Exactly JS's u-mode identity escapes; PCRE accepts all of these too. */
    private const SYNTAX_CHARS = ['^', '$', '\\', '.', '*', '+', '?', '(', ')', '[', ']', '{', '}', '|', '/'];

    /** @var array<string,string> */
    private static array $cache = [];

    /** @param array<string,mixed>|null $pos */
    private static function bad(string $message, string $pattern, int $at, ?array $pos): void
    {
        fail('E_REGEX_SYNTAX', "{$message} (at offset {$at} of /{$pattern}/)", $pos);
    }

    /** @param array<string,mixed>|null $pos */
    private static function rejectEscape(string $e, string $pattern, int $at, ?array $pos): void
    {
        if ($e === 'b' || $e === 'B') {
            self::bad(
                "\\{$e} is not portable — word boundaries depend on the engine's idea of a word "
                    . 'character, which differs. Use an explicit class such as (^|[^0-9A-Za-z_])',
                $pattern,
                $at,
                $pos,
            );
        }
        if ($e === 'v') {
            self::bad(
                '\\v is not portable — PCRE reads it as any vertical whitespace and ECMAScript as U+000B',
                $pattern,
                $at,
                $pos,
            );
        }
        if ($e >= '0' && $e <= '9') {
            self::bad('backreferences are not portable', $pattern, $at, $pos);
        }
        if ($e === 'p' || $e === 'P') {
            self::bad('\\p{...} is not portable', $pattern, $at, $pos);
        }
        if (in_array($e, ['A', 'z', 'Z', 'G', 'K'], true)) {
            self::bad("\\{$e} is not portable — use ^ and $", $pattern, $at, $pos);
        }
        self::bad("unsupported escape \\{$e}", $pattern, $at, $pos);
    }

    /**
     * Validates and rewrites in one pass, returning source that means the same
     * thing to both engines. Both hosts run this, so both compile the identical
     * pattern.
     *
     * @param array<string,mixed>|null $pos
     */
    public static function validate(string $pattern, ?array $pos = null): string
    {
        $p = Utf8::chars($pattern);
        $n = count($p);
        $out = '';
        $i = 0;

        while ($i < $n) {
            $c = $p[$i];

            if ($c === '\\') {
                if ($i + 1 >= $n) {
                    self::bad('trailing backslash', $pattern, $i, $pos);
                }
                $e = $p[$i + 1];
                if (isset(self::EXPAND_OUTSIDE[$e])) {
                    $out .= self::EXPAND_OUTSIDE[$e];
                    $i += 2;
                    continue;
                }
                if (in_array($e, self::CONTROL_ESCAPES, true)
                    || in_array($e, self::SYNTAX_CHARS, true)) {
                    $out .= $c . $e;
                    $i += 2;
                    continue;
                }
                self::rejectEscape($e, $pattern, $i, $pos);
            }

            if ($c === '[') {
                [$text, $next] = self::validateClass($p, $i, $pattern, $pos);
                $out .= $text;
                $i = $next;
                continue;
            }

            if ($c === '(') {
                if (($p[$i + 1] ?? '') === '?') {
                    if (($p[$i + 2] ?? '') === ':') {
                        $out .= '(?:';
                        $i += 3;
                        continue;
                    }
                    $k = $p[$i + 2] ?? '';
                    $kind = ($k === '=' || $k === '!') ? 'lookahead'
                        : ($k === '<' ? 'lookbehind and named groups'
                            : ($k === '>' ? 'atomic groups' : 'this group type'));
                    self::bad("{$kind} is not portable — only (?: ) is", $pattern, $i, $pos);
                }
                $out .= '(';
                $i++;
                continue;
            }

            if ($c === '{') {
                $end = self::afterQuantifier($p, self::validateBraces($p, $i, $pattern, $pos), $pattern, $pos);
                $out .= implode('', array_slice($p, $i, $end - $i));
                $i = $end;
                continue;
            }
            if ($c === '*' || $c === '+' || $c === '?') {
                $end = self::afterQuantifier($p, $i + 1, $pattern, $pos);
                $out .= implode('', array_slice($p, $i, $end - $i));
                $i = $end;
                continue;
            }
            if ($c === '}') {
                self::bad('unmatched } — escape it as \\}', $pattern, $i, $pos);
            }
            if ($c === ']') {
                self::bad('unmatched ] — escape it as \\]', $pattern, $i, $pos);
            }

            $out .= $c;
            $i++;
        }
        return $out;
    }

    /**
     * A quantifier may be followed by `?` (lazy). `+` would make it possessive,
     * which PCRE supports and ECMAScript does not.
     *
     * @param list<string> $p
     * @param array<string,mixed>|null $pos
     */
    private static function afterQuantifier(array $p, int $i, string $pattern, ?array $pos): int
    {
        $c = $p[$i] ?? '';
        if ($c === '+') {
            self::bad('possessive quantifiers are not portable', $pattern, $i, $pos);
        }
        return $c === '?' ? $i + 1 : $i;
    }

    /**
     * @param list<string> $p
     * @param array<string,mixed>|null $pos
     */
    /**
     * Reads a {n}, {n,} or {n,m} quantifier and checks both bounds.
     *
     * spec/SPEC.md §6.4. Not delegated to the engine: PCRE2 rejects a huge
     * repeat count as a syntax error while ECMAScript and cl-ppcre accept it
     * and simply never match, and cl-ppcre also accepts the empty {2,1}.
     */
    private static function validateBraces(array $p, int $start, string $pattern, ?array $pos): int
    {
        $i = $start + 1;
        $loStart = $i;
        while ($i < count($p) && $p[$i] >= '0' && $p[$i] <= '9') {
            $i++;
        }
        if ($i === $loStart) {
            self::bad('{ must begin a quantifier such as {2,4} — escape it as \\{', $pattern, $start, $pos);
        }
        $lo = (int) implode('', array_slice($p, $loStart, $i - $loStart));
        $hi = null;
        if (($p[$i] ?? '') === ',') {
            $i++;
            $hiStart = $i;
            while ($i < count($p) && $p[$i] >= '0' && $p[$i] <= '9') {
                $i++;
            }
            if ($i > $hiStart) {
                $hi = (int) implode('', array_slice($p, $hiStart, $i - $hiStart));
            }
        }
        if (($p[$i] ?? '') !== '}') {
            self::bad('malformed quantifier', $pattern, $start, $pos);
        }
        if ($lo > self::MAX_QUANTIFIER || ($hi !== null && $hi > self::MAX_QUANTIFIER)) {
            self::bad('quantifier bound exceeds the maximum of ' . self::MAX_QUANTIFIER, $pattern, $start, $pos);
        }
        if ($hi !== null && $hi < $lo) {
            self::bad("quantifier {{$lo},{$hi}} is empty — the upper bound is below the lower one", $pattern, $start, $pos);
        }
        return $i + 1;
    }

    /**
     * @param list<string> $p
     * @param array<string,mixed>|null $pos
     * @return array{0:string,1:int} rewritten text, and the index past the ']'
     */
    private static function validateClass(array $p, int $start, string $pattern, ?array $pos): array
    {
        $n = count($p);
        $i = $start + 1;
        $out = '[';
        if (($p[$i] ?? '') === '^') {
            $out .= '^';
            $i++;
        }
        if (($p[$i] ?? '') === '[' && ($p[$i + 1] ?? '') === ':') {
            self::bad('POSIX classes such as [[:alpha:]] are not portable', $pattern, $i, $pos);
        }
        // `]` always closes the class. PCRE treats a leading `]` as a literal
        // while ECMAScript reads `[]` as an empty class, so neither spelling is
        // portable — write `\]` instead.
        $count = 0;
        while ($i < $n) {
            $c = $p[$i];
            if ($c === ']') {
                if ($count === 0) {
                    self::bad('empty character class — write \\] for a literal bracket', $pattern, $start, $pos);
                }
                return [$out . ']', $i + 1];
            }
            $count++;
            if ($c === '\\') {
                if ($i + 1 >= $n) {
                    self::bad('trailing backslash in character class', $pattern, $i, $pos);
                }
                $e = $p[$i + 1];
                if (isset(self::EXPAND_INSIDE[$e])) {
                    $out .= self::EXPAND_INSIDE[$e];
                    $i += 2;
                    continue;
                }
                if ($e === 'D' || $e === 'W' || $e === 'S') {
                    self::bad(
                        "\\{$e} inside a character class cannot be expressed portably — "
                            . 'negate the whole class instead',
                        $pattern,
                        $i,
                        $pos,
                    );
                }
                if (in_array($e, self::CONTROL_ESCAPES, true)
                    || in_array($e, self::SYNTAX_CHARS, true)
                    || $e === '-') {
                    $out .= $c . $e;
                    $i += 2;
                    continue;
                }
                self::rejectEscape($e, $pattern, $i, $pos);
            }
            $out .= $c;
            $i++;
        }
        self::bad('unterminated character class', $pattern, $start, $pos);
        return [$out, $i];
    }

    // --- compilation --------------------------------------------------------

    /**
     * @param array<string,mixed>|null $pos
     * @param array<string,mixed>|null $patPos
     */
    private static function compile(string $pattern, string $flags, ?array $pos, ?array $patPos): string
    {
        $ignoreCase = false;
        foreach (Utf8::chars($flags) as $ch) {
            $f = strtolower($ch);
            if ($f === 'i') {
                $ignoreCase = true;
                continue;
            }
            if ($f === 'm' || $f === 's') {
                fail(
                    'E_BAD_ARG',
                    'flag ' . json_encode($ch) . ' is not offered — SEL always matches . against any '
                        . 'character and anchors ^ $ to the whole subject',
                    $pos,
                );
            }
            fail('E_BAD_ARG', 'unknown regex flag ' . json_encode($ch), $pos);
        }

        if ($ignoreCase) {
            foreach (Utf8::codePoints($pattern) as $cp) {
                if ($cp > 0x7f) {
                    fail(
                        'E_BAD_ARG',
                        'the i flag needs an ASCII-only pattern — case folding above ASCII differs '
                            . 'between PCRE and ECMAScript',
                        $pos,
                    );
                }
            }
        }

        $key = ($ignoreCase ? 'i ' : ' ') . $pattern;
        if (isset(self::$cache[$key])) {
            return self::$cache[$key];
        }

        $source = self::validate($pattern, $patPos);
        $compiled = '/' . self::escapeDelimiter($source) . '/usD' . ($ignoreCase ? 'i' : '');
        if (@preg_match($compiled, '') === false) {
            fail('E_REGEX_SYNTAX', "PCRE rejected /{$pattern}/", $patPos);
        }
        return self::$cache[$key] = $compiled;
    }

    /** The pattern may contain a bare `/`, which JS allows and a `/` delimiter does not. */
    private static function escapeDelimiter(string $pattern): string
    {
        $out = '';
        $chars = Utf8::chars($pattern);
        for ($i = 0, $n = count($chars); $i < $n; $i++) {
            $c = $chars[$i];
            if ($c === '\\') {
                $out .= $c . ($chars[$i + 1] ?? '');
                $i++;
                continue;
            }
            $out .= $c === '/' ? '\\/' : $c;
        }
        return $out;
    }

    /** @return array{0:string,1:string} */
    private static function argsFor(Args $a, int $patIndex, int $subjIndex, int $flagIndex): array
    {
        $pattern = $a->text($patIndex);
        $subject = $a->text($subjIndex);
        $flags = $a->count() > $flagIndex ? $a->text($flagIndex) : '';
        $flagPos = $a->count() > $flagIndex ? $a->posOf($flagIndex) : $a->pos;
        return [self::compile($pattern, $flags, $flagPos, $a->posOf($patIndex)), $subject];
    }

    public static function register(): void
    {
        Registry::define(['name' => 'RMATCH', 'min' => 2, 'max' => 3,
            'fn' => static function (Args $a): Value {
                [$re, $subject] = self::argsFor($a, 0, 1, 2);
                return Value::bool(preg_match($re, $subject) === 1);
            }]);

        Registry::define(['name' => 'RFIND', 'min' => 2, 'max' => 3,
            'fn' => static function (Args $a): Value {
                [$re, $subject] = self::argsFor($a, 0, 1, 2);
                // preg reports byte offsets; SEL reports code point offsets.
                if (preg_match($re, $subject, $m, PREG_OFFSET_CAPTURE) !== 1) {
                    return Value::int(0);
                }
                return Value::int(Utf8::cpIndex($subject, $m[0][1]) + 1);
            }]);

        Registry::define(['name' => 'RGROUPS', 'min' => 2, 'max' => 3,
            'fn' => static function (Args $a): Value {
                [$re, $subject] = self::argsFor($a, 0, 1, 2);
                if (preg_match($re, $subject, $m, PREG_UNMATCHED_AS_NULL) !== 1) {
                    return Value::none();
                }
                $out = [];
                foreach ($m as $k => $g) {
                    if (is_int($k)) {
                        $out[] = Value::text($g ?? '');
                    }
                }
                return Value::list($out);
            }]);

        // Replacement is spliced by hand rather than handed to preg_replace,
        // whose \1 has no ECMAScript equivalent. SEL understands $0-$9 and $$.
        Registry::define(['name' => 'RREPLACE', 'min' => 3, 'max' => 4,
            'fn' => static function (Args $a): Value {
                $pattern = $a->text(0);
                $repl = $a->text(1);
                $subject = $a->text(2);
                $flags = $a->count() > 3 ? $a->text(3) : '';
                $flagPos = $a->count() > 3 ? $a->posOf(3) : $a->pos;
                $re = self::compile($pattern, $flags, $flagPos, $a->posOf(0));

                preg_match_all(
                    $re,
                    $subject,
                    $all,
                    PREG_SET_ORDER | PREG_OFFSET_CAPTURE | PREG_UNMATCHED_AS_NULL,
                );

                $out = '';
                $last = 0;
                foreach ($all as $m) {
                    $groups = [];
                    foreach ($m as $k => $g) {
                        if (is_int($k)) {
                            $groups[] = $g[0];
                        }
                    }
                    $at = $m[0][1];
                    $out .= substr($subject, $last, $at - $last);
                    $out .= self::expand($repl, $groups, $a->posOf(1));
                    $last = $at + strlen((string) $m[0][0]);
                }
                return Value::text($out . substr($subject, $last));
            }]);
    }

    /**
     * @param list<string|null> $groups
     * @param array<string,mixed> $pos
     */
    private static function expand(string $repl, array $groups, array $pos): string
    {
        $out = '';
        for ($i = 0, $n = strlen($repl); $i < $n; $i++) {
            if ($repl[$i] !== '$') {
                $out .= $repl[$i];
                continue;
            }
            $next = $repl[$i + 1] ?? '';
            if ($next === '$') {
                $out .= '$';
                $i++;
                continue;
            }
            if ($next >= '0' && $next <= '9') {
                $g = (int) $next;
                if ($g >= count($groups)) {
                    $have = count($groups) - 1;
                    fail(
                        'E_BAD_ARG',
                        "replacement refers to \${$g} but the pattern has {$have} groups",
                        $pos,
                    );
                }
                $out .= $groups[$g] ?? '';
                $i++;
                continue;
            }
            $out .= '$';
        }
        return $out;
    }
}
