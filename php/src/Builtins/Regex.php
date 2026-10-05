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

    /** @var array<string,array<string,mixed>> compiled patterns, keyed by flag and pattern; bounded */
    private static array $cache = [];
    private const CACHE_MAX = 256;
    /** @var list<string>|null group definitions collected while emitting for the engine */
    private static ?array $defs = null;
    /** Set while re-emitting a pattern PCRE found too large: see emitRep. */
    private static bool $lowerAll = false;

    /** @param array<string,mixed>|null $pos */
    private static function bad(string $message, string $pattern, int $at, ?array $pos): void
    {
        fail('E_REGEX_SYNTAX', "{$message} (at offset {$at} of /" . self::excerpt($pattern) . "/)", $pos);
    }

    /** The message quotes the pattern; a 65 000-character one is not quoted whole. */
    /** At most 80 code points of the pattern, cut between code points so the message stays UTF-8. */
    private static function excerpt(string $pattern): string
    {
        if (strlen($pattern) <= 80 || Utf8::length($pattern) <= 80) return $pattern;
        return substr($pattern, 0, Utf8::advance($pattern, 77)) . '...';
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

    // --- the parser ---------------------------------------------------------
    //
    // The validator is a real parser that builds a tree (spec/SPEC.md §7.8): the
    // rules it enforces — loops over nullable bodies, captures that may not take
    // part in an iteration, group depth and count — and the static analysis that
    // refuses exponentially ambiguous patterns all need the shape of the pattern,
    // which a single pass over its characters cannot give. Nodes are arrays:
    //
    //   lit  one code point            src, ranges
    //   any  `.`                       src, ranges
    //   cls  a class or \d \w \s ...   src, ranges (positive members), neg
    //   eps  an anchor, `^` or `$`     src
    //   cat  a sequence                items
    //   alt  alternatives              br
    //   grp  a group                   cap (bool), x
    //   rep  a quantified atom         x, lo, hi (null = unbounded), lazy

    /**
     * @param list<string> $p
     * @param array<string,mixed>|null $pos
     * @return array<string,mixed> the tree
     */
    private static function parse(array $p, string $pattern, ?array $pos): array
    {
        $n = count($p);
        if ($n > \Sel\Limits::MAX_REGEX_PATTERN) {
            self::bad('the pattern is longer than ' . \Sel\Limits::MAX_REGEX_PATTERN . ' code points', $pattern, 0, $pos);
        }
        $st = ['p' => $p, 'n' => $n, 'i' => 0, 'groups' => 0, 'pattern' => $pattern, 'pos' => $pos];
        $tree = self::parseAlt($st, 0);
        if ($st['i'] < $n) {
            self::bad('unmatched )', $pattern, $st['i'], $pos);
        }
        return $tree;
    }

    /** @param array<string,mixed> $st @return array<string,mixed> */
    private static function parseAlt(array &$st, int $depth): array
    {
        $branches = [self::parseCat($st, $depth)];
        while ($st['i'] < $st['n'] && $st['p'][$st['i']] === '|') {
            $st['i']++;
            $branches[] = self::parseCat($st, $depth);
        }
        return count($branches) === 1 ? $branches[0] : ['k' => 'alt', 'br' => $branches];
    }

    /** @param array<string,mixed> $st @return array<string,mixed> */
    private static function parseCat(array &$st, int $depth): array
    {
        $items = [];
        while ($st['i'] < $st['n']) {
            $c = $st['p'][$st['i']];
            if ($c === '|' || $c === ')') break;
            $items[] = self::parseQuantified($st, $depth);
        }
        if (count($items) === 1) return $items[0];
        return ['k' => 'cat', 'items' => $items];
    }

    /** @param array<string,mixed> $st @return array<string,mixed> */
    private static function parseQuantified(array &$st, int $depth): array
    {
        $atom = self::parseAtom($st, $depth);
        $p = $st['p'];
        $n = $st['n'];
        $i = $st['i'];
        $c = $i < $n ? $p[$i] : '';
        if ($c !== '*' && $c !== '+' && $c !== '?' && $c !== '{') return $atom;

        $pattern = $st['pattern'];
        $pos = $st['pos'];
        $start = $i;
        if ($c === '*') { $lo = 0; $hi = null; $i++; }
        elseif ($c === '+') { $lo = 1; $hi = null; $i++; }
        elseif ($c === '?') { $lo = 0; $hi = 1; $i++; }
        else {
            [$i, $lo, $hi] = self::validateBraces($p, $i, $pattern, $pos);
        }
        $lazy = false;
        if (($p[$i] ?? '') === '?') { $lazy = true; $i++; }
        elseif (($p[$i] ?? '') === '+') {
            self::bad('possessive quantifiers are not portable', $pattern, $i, $pos);
        }
        $d = $p[$i] ?? '';
        if ($d === '*' || $d === '+' || $d === '?' || $d === '{') {
            self::bad('a quantifier cannot follow another', $pattern, $i, $pos);
        }
        if ($atom['k'] === 'eps') {
            self::bad('an anchor cannot be quantified — PCRE, ECMAScript and Python refuse it', $pattern, $start, $pos);
        }
        // A loop is a quantifier whose maximum is above 1 (spec §7.8).
        if ($hi === null || $hi > 1) {
            if (self::nullable($atom)) {
                self::bad(
                    'a loop whose body can match the empty string is not portable — engines disagree on '
                        . 'what an empty iteration captures and matches',
                    $pattern,
                    $start,
                    $pos,
                );
            }
            if (self::optionalCapture($atom, false)) {
                self::bad(
                    'a loop holding a capture that need not take part in every iteration is not '
                        . 'portable — PCRE keeps the last value, ECMAScript resets it',
                    $pattern,
                    $start,
                    $pos,
                );
            }
        }
        $st['i'] = $i;
        return ['k' => 'rep', 'x' => $atom, 'lo' => $lo, 'hi' => $hi, 'lazy' => $lazy];
    }

    /** Can the node match the empty string? @param array<string,mixed> $node */
    private static function nullable(array $node): bool
    {
        switch ($node['k']) {
            case 'lit': case 'any': case 'cls':
                return false;
            case 'eps':
                return true;
            case 'cat':
                foreach ($node['items'] as $it) if (!self::nullable($it)) return false;
                return true;
            case 'alt':
                foreach ($node['br'] as $b) if (self::nullable($b)) return true;
                return false;
            case 'grp':
                return self::nullable($node['x']);
            default: // rep
                return $node['lo'] === 0 || self::nullable($node['x']);
        }
    }

    /**
     * Does the node hold a capture group that need not take part whenever the
     * node matches: one under an alternation with other branches, or under a
     * quantifier whose minimum is 0?
     *
     * @param array<string,mixed> $node
     */
    private static function optionalCapture(array $node, bool $optional): bool
    {
        switch ($node['k']) {
            case 'cat':
                foreach ($node['items'] as $it) if (self::optionalCapture($it, $optional)) return true;
                return false;
            case 'alt':
                foreach ($node['br'] as $b) if (self::optionalCapture($b, true)) return true;
                return false;
            case 'grp':
                if ($node['cap'] && $optional) return true;
                return self::optionalCapture($node['x'], $optional);
            case 'rep':
                return self::optionalCapture($node['x'], $optional || $node['lo'] === 0);
            default:
                return false;
        }
    }

    /** @param array<string,mixed> $st @return array<string,mixed> */
    private static function parseAtom(array &$st, int $depth): array
    {
        $p = $st['p'];
        $n = $st['n'];
        $i = $st['i'];
        $pattern = $st['pattern'];
        $pos = $st['pos'];
        $c = $p[$i];

        if ($c === '\\') {
            if ($i + 1 >= $n) {
                self::bad('trailing backslash', $pattern, $i, $pos);
            }
            $e = $p[$i + 1];
            $st['i'] = $i + 2;
            if (isset(self::EXPAND_OUTSIDE[$e])) {
                $neg = $e === 'D' || $e === 'W' || $e === 'S';
                return ['k' => 'cls', 'src' => self::EXPAND_OUTSIDE[$e], 'esc' => true,
                    'ranges' => self::CLASS_RANGES[strtolower($e)], 'neg' => $neg];
            }
            if (in_array($e, self::CONTROL_ESCAPES, true)) {
                return ['k' => 'lit', 'src' => $c . $e, 'ranges' => [[self::CONTROL_CP[$e], self::CONTROL_CP[$e]]]];
            }
            if (in_array($e, self::SYNTAX_CHARS, true)) {
                $cp = Utf8::ord($e);
                return ['k' => 'lit', 'src' => $c . $e, 'ranges' => [[$cp, $cp]]];
            }
            self::rejectEscape($e, $pattern, $i, $pos);
        }

        if ($c === '[') {
            [$node, $next] = self::parseClass($p, $i, $pattern, $pos);
            $st['i'] = $next;
            return $node;
        }

        if ($c === '(') {
            $k = $p[$i + 1] ?? '';
            $cap = true;
            $after = $i + 1;
            if ($k === '*') {
                self::bad('PCRE verbs such as (*FAIL) are not portable', $pattern, $i, $pos);
            }
            if ($k === '?') {
                if (($p[$i + 2] ?? '') === ':') {
                    $cap = false;
                    $after = $i + 3;
                } else {
                    $k2 = $p[$i + 2] ?? '';
                    $kind = ($k2 === '=' || $k2 === '!') ? 'lookahead'
                        : ($k2 === '<' ? 'lookbehind and named groups'
                            : ($k2 === '>' ? 'atomic groups' : 'this group type'));
                    self::bad("{$kind} is not portable — only (?: ) is", $pattern, $i, $pos);
                }
            }
            if ($depth + 1 > \Sel\Limits::MAX_DEPTH) {
                self::bad('groups nest deeper than ' . \Sel\Limits::MAX_DEPTH, $pattern, $i, $pos);
            }
            if (++$st['groups'] > \Sel\Limits::MAX_REGEX_GROUPS) {
                self::bad('more than ' . \Sel\Limits::MAX_REGEX_GROUPS . ' groups', $pattern, $i, $pos);
            }
            $st['i'] = $after;
            $inner = self::parseAlt($st, $depth + 1);
            if ($st['i'] >= $n || $p[$st['i']] !== ')') {
                self::bad('unterminated group', $pattern, $i, $pos);
            }
            $st['i']++;
            return ['k' => 'grp', 'cap' => $cap, 'x' => $inner];
        }

        if ($c === '*' || $c === '+' || $c === '?' || $c === '{') {
            self::bad('nothing to repeat — escape a literal ' . $c . ' as \\' . $c, $pattern, $i, $pos);
        }
        if ($c === '}') {
            self::bad('unmatched } — escape it as \\}', $pattern, $i, $pos);
        }
        if ($c === ']') {
            self::bad('unmatched ] — escape it as \\]', $pattern, $i, $pos);
        }
        $st['i'] = $i + 1;
        if ($c === '.') {
            return ['k' => 'any', 'src' => '.', 'ranges' => [[0, 0x10FFFF]]];
        }
        if ($c === '^' || $c === '$') {
            return ['k' => 'eps', 'src' => $c];
        }
        $cp = Utf8::ord($c);
        return ['k' => 'lit', 'src' => $c, 'ranges' => [[$cp, $cp]]];
    }

    /** Code points of the escapes the subset offers, and of \d \w \s as ranges. */
    private const CONTROL_CP = ['n' => 10, 'r' => 13, 't' => 9, 'f' => 12];
    private const CLASS_RANGES = [
        'd' => [[48, 57]],
        'w' => [[48, 57], [65, 90], [95, 95], [97, 122]],
        's' => [[9, 13], [32, 32]],
    ];

    /**
     * Reads a {n}, {n,} or {n,m} quantifier and checks both bounds.
     *
     * spec/SPEC.md §6.4. Not delegated to the engine: PCRE2 rejects a huge
     * repeat count as a syntax error while ECMAScript and cl-ppcre accept it
     * and simply never match, and cl-ppcre also accepts the empty {2,1}.
     *
     * @param list<string> $p
     * @param array<string,mixed>|null $pos
     * @return array{0:int,1:int,2:?int} index past the '}', lower and upper bound
     */
    private static function validateBraces(array $p, int $start, string $pattern, ?array $pos): array
    {
        $i = $start + 1;
        $loStart = $i;
        while ($i < count($p) && $p[$i] >= '0' && $p[$i] <= '9') {
            $i++;
        }
        if ($i === $loStart) {
            self::bad('{ must begin a quantifier such as {2,4} — escape it as \\{', $pattern, $start, $pos);
        }
        $lo = self::boundValue(implode('', array_slice($p, $loStart, $i - $loStart)));
        $hi = null;
        if (($p[$i] ?? '') === ',') {
            $i++;
            $hiStart = $i;
            while ($i < count($p) && $p[$i] >= '0' && $p[$i] <= '9') {
                $i++;
            }
            if ($i > $hiStart) {
                $hi = self::boundValue(implode('', array_slice($p, $hiStart, $i - $hiStart)));
            }
        } else {
            $hi = $lo;
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
        return [$i + 1, $lo, $hi];
    }

    /** A run of digits as an int, saturating: `{99999999999999999999}` is over the cap, not a float. */
    private static function boundValue(string $digits): int
    {
        $digits = ltrim($digits, '0');
        if ($digits === '' ) return 0;
        return strlen($digits) > 9 ? PHP_INT_MAX : (int) $digits;
    }

    /**
     * Reads a class into a node: its rewritten source text, the code points it
     * holds as ranges, and whether it is negated.
     *
     * @param list<string> $p
     * @param array<string,mixed>|null $pos
     * @return array{0:array<string,mixed>,1:int} the node, and the index past the ']'
     */
    private static function parseClass(array $p, int $start, string $pattern, ?array $pos): array
    {
        $n = count($p);
        $i = $start + 1;
        $neg = false;
        if (($p[$i] ?? '') === '^') {
            $neg = true;
            $i++;
        }
        // `]` always closes the class. PCRE treats a leading `]` as a literal
        // while ECMAScript reads `[]` as an empty class, so neither spelling is
        // portable — write `\]` instead.
        //
        // atoms: ['src' => spelled, 'cp' => int|null, 'ranges' => ...|null, 'esc' => bool, 'dash' => bool]
        $atoms = [];
        $closed = false;
        while ($i < $n) {
            $c = $p[$i];
            if ($c === ']') {
                if ($atoms === []) {
                    self::bad('empty character class — write \\] for a literal bracket', $pattern, $start, $pos);
                }
                $i++;
                $closed = true;
                break;
            }
            if ($c === '[') {
                // A `[` followed by `:`, `.` or `=` inside a class is refused, closed or
                // not (SPEC 7.8): the POSIX bracket forms [:alpha:], [.x.] and [=x=] are
                // read differently by the engines, and so is an unfinished one.
                $k = $p[$i + 1] ?? '';
                if ($k === ':' || $k === '.' || $k === '=') {
                    self::bad('POSIX classes such as [[:alpha:]] are not portable', $pattern, $i, $pos);
                }
                $atoms[] = ['src' => '[', 'cp' => 91, 'esc' => false, 'dash' => false];
                $i++;
                continue;
            }
            if ($c === '\\') {
                if ($i + 1 >= $n) {
                    self::bad('trailing backslash in character class', $pattern, $i, $pos);
                }
                $e = $p[$i + 1];
                if (isset(self::EXPAND_INSIDE[$e])) {
                    $atoms[] = ['src' => self::EXPAND_INSIDE[$e], 'cp' => null,
                        'ranges' => self::CLASS_RANGES[$e], 'esc' => true, 'dash' => false, 'at' => $i];
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
                if (in_array($e, self::CONTROL_ESCAPES, true)) {
                    $atoms[] = ['src' => $c . $e, 'cp' => self::CONTROL_CP[$e], 'esc' => false, 'dash' => false];
                    $i += 2;
                    continue;
                }
                if (in_array($e, self::SYNTAX_CHARS, true) || $e === '-') {
                    $atoms[] = ['src' => $c . $e, 'cp' => Utf8::ord($e), 'esc' => false, 'dash' => false];
                    $i += 2;
                    continue;
                }
                self::rejectEscape($e, $pattern, $i, $pos);
            }
            $atoms[] = ['src' => $c, 'cp' => Utf8::ord($c), 'esc' => false, 'dash' => $c === '-', 'at' => $i];
            $i++;
        }
        if (!$closed) {
            self::bad('unterminated character class', $pattern, $start, $pos);
        }

        // Ranges. A bare `-` between two atoms is a range operator; first or last
        // it is a literal. A class escape (\d \w \s) cannot be an endpoint: PCRE
        // takes the hyphen literally and ECMAScript refuses.
        $out = '';
        $ranges = [];
        $m = count($atoms);
        for ($k = 0; $k < $m; $k++) {
            $a = $atoms[$k];
            $isRange = $k + 2 < $m + 0 && ($atoms[$k + 1]['dash'] ?? false);
            // `a-b` needs a third atom after the dash; `a-` at the end is literal.
            $isRange = ($k + 2 < $m) && $atoms[$k + 1]['dash'];
            if ($a['esc']) {
                if ($isRange) {
                    self::bad('a class escape such as \\d cannot be a range endpoint', $pattern, $a['at'], $pos);
                }
                $out .= $a['src'];
                foreach ($a['ranges'] as $r) $ranges[] = $r;
                continue;
            }
            if ($isRange) {
                $hiAtom = $atoms[$k + 2];
                if ($hiAtom['esc']) {
                    self::bad('a class escape such as \\d cannot be a range endpoint', $pattern, $hiAtom['at'], $pos);
                }
                if ($hiAtom['cp'] < $a['cp']) {
                    self::bad('range out of order in character class', $pattern, $a['at'] ?? $start, $pos);
                }
                $out .= $a['src'] . '-' . $hiAtom['src'];
                $ranges[] = [$a['cp'], $hiAtom['cp']];
                $k += 2;
                continue;
            }
            $out .= $a['src'];
            $ranges[] = [$a['cp'], $a['cp']];
        }
        // A trailing escape after a literal hyphen: `[a-\d]` was refused above; a
        // leading `\d-x`: the escape is atom k, the dash k+1, x k+2 — refused above.
        $node = ['k' => 'cls', 'src' => '[' . ($neg ? '^' : '') . $out . ']', 'ranges' => $ranges, 'neg' => $neg];
        return [$node, $i];
    }

    // --- emitting PCRE source ----------------------------------------------

    /**
     * The tree back as source. `$compact` is for the engine, not for the SQL
     * translator (which must keep emitting exactly the portable text): it folds a
     * run of one repeated literal into `x{n}` — PCRE's compiled program is limited
     * to 64 KB and a 65 000-character pattern of `a`s does not fit — and unwraps a
     * group around a single character before a count, since a counted GROUP is
     * copied n times by PCRE and a counted single character is one instruction.
     *
     * @param array<string,mixed> $node
     */
    private static function emit(array $node, bool $compact): string
    {
        switch ($node['k']) {
            case 'lit': case 'any': case 'cls': case 'eps':
                return $node['src'];
            case 'cat':
                $out = '';
                $items = $node['items'];
                $m = count($items);
                for ($k = 0; $k < $m; $k++) {
                    $it = $items[$k];
                    if ($compact && $it['k'] === 'lit') {
                        $j = $k + 1;
                        while ($j < $m && $items[$j]['k'] === 'lit' && $items[$j]['src'] === $it['src']) $j++;
                        if ($j - $k >= 2) {
                            $out .= $it['src'] . '{' . ($j - $k) . '}';
                            $k = $j - 1;
                            continue;
                        }
                    }
                    $out .= self::emit($it, $compact);
                }
                return $out;
            case 'alt':
                return implode('|', array_map(static fn (array $b): string => self::emit($b, $compact), $node['br']));
            case 'grp':
                return ($node['cap'] ? '(' : '(?:') . self::emit($node['x'], $compact) . ')';
            default:
                return self::emitRep($node, $compact);
        }
    }

    /** @param array<string,mixed> $node */
    private static function emitRep(array $node, bool $compact): string
    {
        $x = $node['x'];
        $lo = $node['lo'];
        $hi = $node['hi'];
        $lazy = $node['lazy'] ? '?' : '';
        $q = static function (int $a, ?int $b): string {
            if ($b === null) return $a === 0 ? '*' : ($a === 1 ? '+' : '{' . $a . ',}');
            if ($a === 0 && $b === 1) return '?';
            return $a === $b ? '{' . $a . '}' : '{' . $a . ',' . $b . '}';
        };
        $single = static fn (array $y): bool => in_array($y['k'], ['lit', 'any', 'cls'], true);
        if ($compact && $x['k'] === 'grp') {
            $body = $x['x'];
            $bodySingle = $single($body);
            if ($bodySingle && !$x['cap']) {
                // (?:c){n,m} is c{n,m}: one instruction instead of n copies of a group.
                return $body['src'] . $q($lo, $hi) . $lazy;
            }
            if (!$x['cap'] && $hi === $lo && self::$defs !== null && !self::hasCapture($body)) {
                $bodySrc = self::emit($body, true);
                if ($lo * max(1, strlen($bodySrc)) > 20000 || (self::$lowerAll && $lo >= 2)) {
                    return self::counted($bodySrc, $lo);
                }
            }
            if ($bodySingle && $x['cap'] && ($hi === null ? $lo : $hi) >= 2000) {
                // (c){n,m}: the capture is the LAST iteration's character, so all but
                // the last iteration can be plain repeats: c{n-1,m-1}(c). The same
                // numbers of iterations, in the same order of preference.
                $c = $body['src'];
                if ($lo >= 1) {
                    return $c . $q($lo - 1, $hi === null ? null : $hi - 1) . $lazy . '(' . $c . ')';
                }
                return '(?:' . $c . $q(0, $hi === null ? null : $hi - 1) . $lazy . '(' . $c . '))?' . $lazy;
            }
            if (self::$lowerAll && $lo >= 2 && self::$defs !== null) {
                // (G){lo,hi} is lo-1 iterations, then G{1,hi-lo+1}: the same
                // iterations, mandatory ones first, the optional ones nested after
                // them, in the same order of preference. Every capture in the body
                // takes part in every iteration (spec §7.8), so the last iteration
                // sets them all and the first lo-1 may be copies without captures,
                // which counted() defines once instead of PCRE copying them.
                // Used only when the plain form did not fit (compiled()).
                $head = self::counted(self::emit(self::uncaptured($body), true), $lo - 1);
                if ($hi === $lo) return $head . self::emit($x, true);
                return $head . self::emit($x, true) . $q(1, $hi === null ? null : $hi - $lo + 1) . $lazy;
            }
        }
        return self::emit($x, $compact) . $q($lo, $hi) . $lazy;
    }

    /**
     * The tree with every capturing group made non-capturing.
     *
     * @param array<string,mixed> $node
     * @return array<string,mixed>
     */
    private static function uncaptured(array $node): array
    {
        switch ($node['k']) {
            case 'cat':
                $node['items'] = array_map(self::uncaptured(...), $node['items']);
                return $node;
            case 'alt':
                $node['br'] = array_map(self::uncaptured(...), $node['br']);
                return $node;
            case 'grp':
                $node['cap'] = false;
                $node['x'] = self::uncaptured($node['x']);
                return $node;
            case 'rep':
                $node['x'] = self::uncaptured($node['x']);
                return $node;
            default:
                return $node;
        }
    }

    /** How many capturing groups the tree holds. @param array<string,mixed> $node */
    private static function countGroups(array $node): int
    {
        switch ($node['k']) {
            case 'cat':
                $t = 0;
                foreach ($node['items'] as $it) $t += self::countGroups($it);
                return $t;
            case 'alt':
                $t = 0;
                foreach ($node['br'] as $b) $t += self::countGroups($b);
                return $t;
            case 'grp':
                return ($node['cap'] ? 1 : 0) + self::countGroups($node['x']);
            case 'rep':
                return self::countGroups($node['x']);
            default:
                return 0;
        }
    }

    /** Does the tree hold a capturing group? @param array<string,mixed> $node */
    private static function hasCapture(array $node): bool
    {
        switch ($node['k']) {
            case 'cat':
                foreach ($node['items'] as $it) if (self::hasCapture($it)) return true;
                return false;
            case 'alt':
                foreach ($node['br'] as $b) if (self::hasCapture($b)) return true;
                return false;
            case 'grp':
                return $node['cap'] || self::hasCapture($node['x']);
            case 'rep':
                return self::hasCapture($node['x']);
            default:
                return false;
        }
    }

    /**
     * `(?:body){n}` for the engine when n copies of the body would not fit in PCRE's
     * 64 KB program: the body is defined once, in a `(?(DEFINE)…)` block appended to
     * the pattern, and doubled by subroutine calls — level j is two calls of level
     * j-1 — so the program holds one body and about log2(n) calls, and n is spelled
     * out in binary at the use. A call is an ordinary backtracking match of the
     * group (PCRE2 10.30 and later), and the body holds no capture, so the language
     * matched is exactly that of n copies.
     */
    private static function counted(string $bodySrc, int $n): string
    {
        $id = count(self::$defs);
        $name = "sel{$id}_";
        $bits = 0;                                   // floor(log2(n)), n >= 1
        while ((2 << $bits) <= $n) $bits++;
        $defs = "(?P<{$name}0>{$bodySrc})";
        for ($j = 1; $j <= $bits; $j++) {
            $prev = $j - 1;
            $defs .= "(?P<{$name}{$j}>(?&{$name}{$prev})(?&{$name}{$prev}))";
        }
        self::$defs[] = $defs;
        $out = '';
        for ($j = $bits; $j >= 0; $j--) {
            if ($n & (1 << $j)) $out .= "(?&{$name}{$j})";
        }
        return $out;
    }

    /** The shortest subject length, in code points, the node can match (saturating). @param array<string,mixed> $node */
    private static function minLen(array $node): int
    {
        $cap = 1 << 40;
        switch ($node['k']) {
            case 'lit': case 'any': case 'cls':
                return 1;
            case 'eps':
                return 0;
            case 'cat':
                $t = 0;
                foreach ($node['items'] as $it) $t += self::minLen($it);
                return min($t, $cap);
            case 'alt':
                $m = $cap;
                foreach ($node['br'] as $b) $m = min($m, self::minLen($b));
                return $m;
            case 'grp':
                return self::minLen($node['x']);
            default:
                $inner = self::minLen($node['x']);
                return $inner === 0 ? 0 : (int) min($node['lo'] * $inner, $cap);
        }
    }

    /**
     * Validates and rewrites, returning source that means the same thing to every
     * host's engine: the portable text. The SQL translator emits this, and the
     * evaluator compiles a compacted form of the same tree.
     *
     * @param array<string,mixed>|null $pos
     */
    public static function validate(string $pattern, ?array $pos = null, bool $ignoreCase = false): string
    {
        $tree = self::parse(Utf8::chars($pattern), $pattern, $pos);
        RegexAmbiguity::check($tree, $ignoreCase, $pattern, $pos);
        return self::emit($tree, false);
    }

    /**
     * The portable form of a pattern: validated against the subset spec §7.8
     * allows, with \d, \w and \s expanded into explicit ASCII classes.
     *
     * Public so the SQL translator can emit the same thing the evaluator
     * compiles. A second copy of the expansion in the SQL layer would be a
     * second thing to keep in step, and the failure mode is silent: MariaDB's
     * engine matches \d against Arabic-Indic digits where SEL does not, so a
     * translator that passed the pattern through would answer differently from
     * the evaluator on the same input.
     *
     * @param array<string,mixed>|null $pos
     */
    public static function portableSource(string $pattern, ?array $pos = null): string
    {
        return self::validate($pattern, $pos);
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

    // --- compilation --------------------------------------------------------

    /**
     * The flags argument: `i` and nothing else (spec §7.8). Compared exactly —
     * folding the flag's case, as this once did, accepted `I`.
     *
     * @param array<string,mixed>|null $pos
     */
    private static function ignoreCase(string $flags, ?array $pos): bool
    {
        $ignoreCase = false;
        foreach (Utf8::chars($flags) as $ch) {
            if ($ch === 'i') {
                $ignoreCase = true;
                continue;
            }
            if ($ch === 'm' || $ch === 's') {
                fail(
                    'E_BAD_ARG',
                    'flag ' . json_encode($ch) . ' is not offered — SEL always matches . against any '
                        . 'character and anchors ^ $ to the whole subject',
                    $pos,
                );
            }
            fail('E_BAD_ARG', 'unknown regex flag ' . json_encode($ch), $pos);
        }
        return $ignoreCase;
    }

    /**
     * @param array<string,mixed>|null $pos
     * @param array<string,mixed>|null $patPos
     * @return array<string,mixed>
     */
    private static function compile(string $pattern, string $flags, ?array $pos, ?array $patPos): array
    {
        $ignoreCase = self::ignoreCase($flags, $pos);
        // A cached `i` pattern has already passed the ASCII check (only ASCII
        // patterns are ever compiled with the flag on), so the scan is for the
        // first use only (PHP-P30).
        if ($ignoreCase && !isset(self::$cache['i ' . $pattern])) {
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
        return self::compiled($pattern, $ignoreCase, $patPos);
    }

    /**
     * Validate, and compile for PCRE. Cached by flag AND pattern, at most
     * CACHE_MAX entries, the oldest evicted first: an unbounded cache was a
     * memory leak for a rule set that builds its patterns from data.
     *
     * @param array<string,mixed>|null $patPos
     * @return array{re:string,reNoJit:string,minLen:int,tooLarge:bool,groups:int}
     */
    private static function compiled(string $pattern, bool $ignoreCase, ?array $patPos): array
    {
        $key = ($ignoreCase ? 'i ' : ' ') . $pattern;
        if (isset(self::$cache[$key])) {
            return self::$cache[$key];
        }

        $tree = self::parse(Utf8::chars($pattern), $pattern, $patPos);
        RegexAmbiguity::check($tree, $ignoreCase, $pattern, $patPos);
        $flags = '/usD' . ($ignoreCase ? 'i' : '');
        [$source, $ok, $message] = self::engineSource($tree, $flags, false);
        if ($ok === false && $message !== null && str_contains($message, 'too large')) {
            // Too large as written: lower every counted group (see emitRep).
            [$source, $ok, $message] = self::engineSource($tree, $flags, true);
        }
        $re = '/' . $source . $flags;
        $tooLarge = false;
        if ($ok === false) {
            // The validator has accepted the pattern, so PCRE refusing it is a
            // limit of PCRE's (its compiled program is capped at 64 KB and a
            // counted GROUP is copied n times), not a syntax error of the
            // pattern's. Such a pattern still has an answer whenever the subject
            // is too short to match it at all; otherwise it is refused at the
            // pattern's position, when it is used.
            if ($message !== null && str_contains($message, 'too large')) {
                $tooLarge = true;
            } else {
                fail('E_REGEX_SYNTAX', 'PCRE rejected /' . self::excerpt($pattern) . '/', $patPos);
            }
        }
        if (count(self::$cache) >= self::CACHE_MAX) {
            unset(self::$cache[array_key_first(self::$cache)]);
        }
        return self::$cache[$key] = [
            're' => $re,
            // The same pattern under a different cache key, so it is compiled
            // afresh WITHOUT the JIT (see run()).
            'reNoJit' => '/(?:' . $source . ')' . $flags,
            'minLen' => self::minLen($tree),
            'tooLarge' => $tooLarge,
            // The pattern's own capture groups: the engine's source can end in
            // (?(DEFINE)…) groups (see counted()) whose numbers follow them.
            'groups' => self::countGroups($tree),
        ];
    }

    /**
     * The tree as PCRE source, and whether PCRE compiles it (false, with its
     * warning, when it does not).
     *
     * @param array<string,mixed> $tree
     * @return array{0:string,1:int|false,2:?string}
     */
    private static function engineSource(array $tree, string $flags, bool $lowerAll): array
    {
        self::$defs = [];
        self::$lowerAll = $lowerAll;
        try {
            $source = self::emit($tree, true);
            if (self::$defs !== []) {
                $source .= '(?(DEFINE)' . implode('', self::$defs) . ')';
            }
        } finally {
            self::$defs = null;
            self::$lowerAll = false;
        }
        $source = self::escapeDelimiter($source);
        $message = null;
        set_error_handler(static function (int $no, string $str) use (&$message): bool {
            $message = $str;
            return true;
        });
        try {
            $ok = preg_match('/' . $source . $flags, '');
        } finally {
            restore_error_handler();
        }
        return [$source, $ok, $message];
    }

    /**
     * Literal patterns are checked when the program is compiled (spec §7.8), not
     * when the call runs, so a bad pattern in a branch that never executes is
     * still refused. `$flags` is the flags argument when it is a literal too,
     * else null; a bad flag is not this check's business (it is E_BAD_ARG when
     * the call runs), so it only decides whether `i` is on. A non-ASCII
     * pattern under `i` is still checked, without the fold (spec §7.8: the
     * `i` refusal is a run-time one and comes last).
     *
     * @param array<string,mixed>|null $patPos
     */
    public static function checkLiteral(string $pattern, ?string $flags, ?array $patPos): void
    {
        $ignoreCase = $flags !== null && in_array('i', Utf8::chars($flags), true);
        if ($ignoreCase) {
            foreach (Utf8::codePoints($pattern) as $cp) {
                if ($cp > 0x7f) {
                    $ignoreCase = false;
                    break;
                }
            }
        }
        self::compiled($pattern, $ignoreCase, $patPos);
    }

    /**
     * Runs one PCRE call. The JIT has a fixed stack and a pattern such as
     * `^(?:a|b)*$` over 60 000 characters exhausts it (`preg_*` then returns
     * false, which callers took for "no match"); so a resource failure is
     * retried on the interpreter, compiled without the JIT and with the match
     * limits raised, whose frames live on the heap. Still failing, it is an
     * error — never a wrong answer.
     *
     * @param array{re:string,reNoJit:string,minLen:int,tooLarge:bool,groups:int} $c
     * @param callable(string):mixed $op
     * @param array<string,mixed>|null $pos
     */
    private static function run(array $c, callable $op, ?array $pos): mixed
    {
        $r = $op($c['re']);
        if ($r !== false || preg_last_error() === PREG_NO_ERROR) return $r;
        $saved = [ini_get('pcre.jit'), ini_get('pcre.backtrack_limit'), ini_get('pcre.recursion_limit')];
        ini_set('pcre.jit', '0');
        ini_set('pcre.backtrack_limit', '2147483647');
        ini_set('pcre.recursion_limit', '2147483647');
        try {
            $r = $op($c['reNoJit']);
            $err = preg_last_error_msg();
        } finally {
            ini_set('pcre.jit', (string) $saved[0]);
            ini_set('pcre.backtrack_limit', (string) $saved[1]);
            ini_set('pcre.recursion_limit', (string) $saved[2]);
        }
        if ($r === false) {
            fail('E_RANGE', "the regular expression exceeded the engine's resource limits ({$err})", $pos);
        }
        return $r;
    }

    /**
     * True when the call has an answer without running PCRE: the pattern is too
     * large for PCRE, and the subject is shorter than anything it can match.
     * Raises when it is too large and the subject is long enough to matter.
     *
     * @param array{re:string,reNoJit:string,minLen:int,tooLarge:bool,groups:int} $c
     * @param array<string,mixed>|null $patPos
     */
    private static function cannotMatch(array $c, string $subject, ?array $patPos): bool
    {
        if (!$c['tooLarge']) return false;
        if (Utf8::length($subject) < $c['minLen']) return true;
        fail('E_REGEX_SYNTAX', 'the pattern is too large for this host\'s regular expression engine', $patPos);
    }

    /** @return array{0:array{re:string,reNoJit:string,minLen:int,tooLarge:bool},1:string} */
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
                [$c, $subject] = self::argsFor($a, 0, 1, 2);
                if (self::cannotMatch($c, $subject, $a->posOf(0))) return Value::bool(false);
                return Value::bool(self::run($c, static fn (string $re) => preg_match($re, $subject), $a->pos) === 1);
            }]);

        Registry::define(['name' => 'RFIND', 'min' => 2, 'max' => 3,
            'fn' => static function (Args $a): Value {
                [$c, $subject] = self::argsFor($a, 0, 1, 2);
                if (self::cannotMatch($c, $subject, $a->posOf(0))) return Value::int(0);
                $m = [];
                // preg reports byte offsets; SEL reports code point offsets.
                $r = self::run($c, static function (string $re) use ($subject, &$m) {
                    return preg_match($re, $subject, $m, PREG_OFFSET_CAPTURE);
                }, $a->pos);
                if ($r !== 1) {
                    return Value::int(0);
                }
                return Value::int(Utf8::cpIndex($subject, $m[0][1]) + 1);
            }]);

        Registry::define(['name' => 'RGROUPS', 'min' => 2, 'max' => 3,
            'fn' => static function (Args $a): Value {
                [$c, $subject] = self::argsFor($a, 0, 1, 2);
                if (self::cannotMatch($c, $subject, $a->posOf(0))) return Value::none();
                $m = [];
                $r = self::run($c, static function (string $re) use ($subject, &$m) {
                    return preg_match($re, $subject, $m, PREG_UNMATCHED_AS_NULL);
                }, $a->pos);
                if ($r !== 1) {
                    return Value::none();
                }
                $out = [];
                foreach ($m as $k => $g) {
                    if (is_int($k) && $k <= $c['groups']) {
                        $out[] = Value::text($g ?? '');
                    }
                }
                return Value::list($out);
            }]);

        // Replacement is spliced by hand rather than handed to preg_replace,
        // whose \1 has no ECMAScript equivalent. SEL understands $0-$9 and $$.
        //
        // The scan is spec §7.8's, not PCRE's preg_match_all: left to right, and
        // after an EMPTY match at s it resumes at s+1 (copying that code point
        // through); after a non-empty match it resumes at the match's end, where
        // an empty match is allowed. preg_match_all instead retries a non-empty
        // match at the same position after an empty one, which gave `b*?` on
        // `abb` the answer `-a-----` where every other host gives `-a-b-b-`.
        Registry::define(['name' => 'RREPLACE', 'min' => 3, 'max' => 4,
            'fn' => static function (Args $a): Value {
                $pattern = $a->text(0);
                $repl = $a->text(1);
                $subject = $a->text(2);
                $flags = $a->count() > 3 ? $a->text(3) : '';
                $flagPos = $a->count() > 3 ? $a->posOf(3) : $a->pos;
                $c = self::compile($pattern, $flags, $flagPos, $a->posOf(0));
                if (self::cannotMatch($c, $subject, $a->posOf(0))) return Value::text($subject);

                $out = '';
                $built = 0;     // code points of the result so far
                $ascii = \Sel\Utf8::isAscii($subject) && \Sel\Utf8::isAscii($repl);
                $last = 0;      // bytes of the subject already copied or replaced
                $at = 0;        // where the next search starts
                $len = strlen($subject);
                $replPos = $a->posOf(1);
                $parts = self::parseReplacement($repl);
                $plain = count($parts) === 1 && is_string($parts[0]) ? $parts[0] : null;   // no group reference
                while ($at <= $len) {
                    $m = [];
                    $r = preg_match($c['re'], $subject, $m, PREG_OFFSET_CAPTURE | PREG_UNMATCHED_AS_NULL, $at);
                    if ($r === false && preg_last_error() !== PREG_NO_ERROR) {
                        // A PCRE resource limit: retried by run() without the JIT and with raised limits.
                        $r = self::run($c, static function (string $re) use ($subject, &$m, $at) {
                            return preg_match($re, $subject, $m, PREG_OFFSET_CAPTURE | PREG_UNMATCHED_AS_NULL, $at);
                        }, $a->pos);
                    }
                    if ($r !== 1) break;
                    $start = $m[0][1];
                    $matched = (string) $m[0][0];
                    if ($plain !== null) {
                        $expansion = $plain;
                    } else {
                        $groups = [];
                        foreach ($m as $k => $g) {
                            if (is_int($k) && $k <= $c['groups']) {
                                $groups[] = $g[0];
                            }
                        }
                        $expansion = self::expandParts($parts, $groups, $replPos);
                    }
                    $piece = substr($subject, $last, $start - $last) . $expansion;
                    // The result is checked as it grows, so a replacement that
                    // would run past the cap (spec §6.4) is refused at the call
                    // with at most one cap's worth built, not after the fact.
                    $out .= $piece;
                    $built += $ascii ? strlen($piece) : \Sel\Utf8::length($piece);
                    if ($built > \Sel\Limits::MAX_TEXT_LEN) {
                        fail('E_RANGE', 'RREPLACE result would be longer than ' . \Sel\Limits::MAX_TEXT_LEN, $a->pos);
                    }
                    $last = $start + strlen($matched);
                    if ($matched === '') {
                        // Resume one code point on; the code point is copied
                        // through by the next substr.
                        if ($start >= $len) break;
                        $b = ord($subject[$start]);
                        $at = $start + ($b < 0x80 ? 1 : ($b < 0xE0 ? 2 : ($b < 0xF0 ? 3 : 4)));
                    } else {
                        $at = $last;
                    }
                }
                $tail = substr($subject, $last);
                $built += $ascii ? strlen($tail) : \Sel\Utf8::length($tail);
                if ($built > \Sel\Limits::MAX_TEXT_LEN) {
                    fail('E_RANGE', 'RREPLACE result would be longer than ' . \Sel\Limits::MAX_TEXT_LEN, $a->pos);
                }
                return Value::text($out . $tail);
            }]);
    }

    /**
     * The replacement text cut once into literal strings and group numbers
     * ($0-$9), so a replacement with a thousand matches is not re-scanned a
     * thousand times (PHP-P18). `$$` is a literal dollar and any other `$` stays one.
     * A replacement with no `$` at all is a single literal.
     *
     * @return list<string|int>
     */
    private static function parseReplacement(string $repl): array
    {
        if (!str_contains($repl, '$')) {
            return [$repl];
        }
        $parts = [];
        $lit = '';
        for ($i = 0, $n = strlen($repl); $i < $n; $i++) {
            if ($repl[$i] !== '$') {
                $lit .= $repl[$i];
                continue;
            }
            $next = $repl[$i + 1] ?? '';
            if ($next === '$') {
                $lit .= '$';
                $i++;
                continue;
            }
            if ($next >= '0' && $next <= '9') {
                if ($lit !== '') {
                    $parts[] = $lit;
                    $lit = '';
                }
                $parts[] = (int) $next;
                $i++;
                continue;
            }
            $lit .= '$';
        }
        if ($lit !== '') {
            $parts[] = $lit;
        }
        return $parts;
    }

    /**
     * @param list<string|int> $parts
     * @param list<string|null> $groups
     * @param array<string,mixed> $pos
     */
    private static function expandParts(array $parts, array $groups, array $pos): string
    {
        $out = '';
        foreach ($parts as $part) {
            if (is_string($part)) {
                $out .= $part;
                continue;
            }
            if ($part >= count($groups)) {
                $have = count($groups) - 1;
                fail(
                    'E_BAD_ARG',
                    "replacement refers to \${$part} but the pattern has {$have} groups",
                    $pos,
                );
            }
            $out .= $groups[$part] ?? '';
        }
        return $out;
    }
}
