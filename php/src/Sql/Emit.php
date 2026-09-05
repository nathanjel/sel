<?php
// Everything that turns a value or a template into characters. The one place
// quoting happens, so there is one place to get it right.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Value;

final class Emit
{
    private string $dialect;

    public function __construct(string $dialect)
    {
        $this->dialect = $dialect;
    }

    public function dialect(): string
    {
        return $this->dialect;
    }

    /** @return mixed */
    public function lex(string $key)
    {
        return Map::lexical($this->dialect, $key);
    }

    // --- literals -----------------------------------------------------------

    /**
     * A SEL value as a SQL literal, in the form the caller says it has. Static
     * so Fragment can join without holding an emitter, which keeps a Fragment a
     * plain data object.
     *
     * The form is passed in and never inferred, because it cannot be inferred:
     * SEL numbers *are* TEXT values (spec §4), so Value::num('5.00') and
     * Value::text('5.00') are the same object and looksNumeric() cannot tell
     * "the author wrote 5.00" from "the author wrote \"5.00\"". Only the AST
     * knows, and it is the AST that tells us.
     *
     * Getting this wrong is not cosmetic. Emitted bare, "5.00" $== "5" becomes
     * 5.00 = 5, which the database answers TRUE and SEL answers FALSE.
     *
     * @param array{line:int,col:int,offset:int}|null $pos
     */
    public static function literal(string $dialect, Value $v, string $form = 'TEXT',
                                   ?array $pos = null): string
    {
        if ($form === 'BOOL' || $v->isBool()) {
            return (string) Map::lexical($dialect, $v->asBool($pos) ? 'true' : 'false');
        }
        if ($form === 'BIN' || $v->isBin()) {
            $tpl = Map::lexical($dialect, 'binaryLiteral');
            if (!is_string($tpl)) {
                refuse('E_SQL_UNSUPPORTED',
                    "dialect {$dialect} has no binary literal syntax", $pos);
            }
            return str_replace('{hex}', bin2hex($v->asBytes($pos)), $tpl);
        }
        if ($form === 'NUM') {
            return self::numericLiteral($v, $pos);
        }
        return self::textLiteral($dialect, $v->asText($pos));
    }

    /**
     * The only unquoted output in the layer.
     *
     * A NUM literal is the one thing emitted without quotes, which makes it the
     * one thing that has to be a number. The AST path arrives already parsed,
     * but a `value` binding declaring `type: NUM` reaches here straight from
     * host data, and `"1 OR 1=1 -- "` would go out verbatim. Fragment's part
     * list keeps a literal from being confused with SQL; it cannot keep a
     * literal from BEING SQL.
     *
     * What is emitted is what the parse recovered — `Dec::format`'s output —
     * rather than the text the caller supplied. The two agree for everything the
     * parser produces, and the difference is the point: proving a string is a
     * number and then emitting a *different* string is a gap, however small, and
     * the gap is where "1 OR 1=1" lived. After this the characters that can
     * leave here are digits, one `.` and a leading `-`, by construction.
     *
     * That is a narrower door, not a locked one. `Dec::parse` is still being
     * called on host data, and a host that has its own reason to believe a
     * string is a number can still be wrong about it — what it cannot be is
     * wrong in a way that reaches the server. Scale survives: 2.50 stays 2.50,
     * because scale is part of a SEL number (spec §4.1) and part of what SQL
     * DECIMAL arithmetic reads.
     */
    private static function numericLiteral(Value $v, ?array $pos): string
    {
        $text = $v->asText($pos);
        $d = \Sel\Dec::parse($text);
        if ($d === null) {
            refuse('E_SQL_BINDING',
                'a value bound as NUM must be a number, and '
                . Value::quoteDump($text) . ' is not', $pos);
        }
        $n = \Sel\Dec::format($d);
        // A negative number is parenthesised so that unary minus in front of it
        // cannot produce `--`. MariaDB reads that as double negation and gets
        // the right answer by luck; PostgreSQL and SQLite read it as the start
        // of a line comment and the rest of the expression disappears. Only
        // reachable through a `value` binding, since the parser never produces a
        // signed `num` node — which is exactly the kind of narrow path that
        // stays broken until a dialect that cares is added.
        return str_starts_with($n, '-') ? '(' . $n . ')' : $n;
    }

    public static function textLiteral(string $dialect, string $text): string
    {
        $quote = (string) Map::lexical($dialect, 'textQuote');
        $escape = Map::lexical($dialect, 'textEscape');
        $out = $text;
        if (is_array($escape)) {
            // Longest first, so a rule for "\\" is applied before one for "\".
            $keys = array_keys($escape);
            usort($keys, static fn ($a, $b): int => strlen((string) $b) <=> strlen((string) $a));
            $out = strtr($out, array_combine($keys, array_map(
                static fn ($k) => (string) $escape[$k], $keys)));
        }
        return $quote . $out . $quote;
    }

    /** The params-mode placeholder for slot $n, 1-based. */
    public static function placeholder(string $dialect, int $n): string
    {
        $tpl = (string) Map::lexical($dialect, 'placeholder');
        return str_contains($tpl, '{n}') ? str_replace('{n}', (string) $n, $tpl) : $tpl;
    }

    /**
     * An operand of a byte comparison: cast to a character type, then given the
     * dialect's binary collation.
     *
     * Both halves are needed and neither is enough alone. Without the collation
     * MariaDB's default is case-insensitive, so "A" $== "a" is true there and
     * false in SEL. Without the cast the collation does not stop two numeric
     * operands being compared as numbers, so 3.0 EQL 3 is true there and false
     * in SEL — EQL is structural and does not normalise numbers.
     *
     * Applied here rather than in the templates because three places need it —
     * two-operand comparisons, IN over a list, and the inRelation skeleton —
     * and only one of those is a two-operand template.
     */
    public function textOperand(Fragment $f): Fragment
    {
        $cast = $this->lex('textCast');
        $collate = (string) $this->lex('textCollate');
        $parts = $f->parts;

        if (is_string($cast) && $cast !== '{0}') {
            $parts = $this->fill($cast, [$f]);
        }
        if ($collate !== '') {
            $parts[] = $collate;
        }
        return new Fragment($parts, 'TEXT', $this->dialect);
    }

    // --- identifiers --------------------------------------------------------

    /**
     * A table or column name, quoted. The quote character is doubled — or
     * whatever `identEscape` says — inside the name, which is what stops a
     * binding naming a column `a"b` from ending the identifier early.
     */
    public function ident(string $name): string
    {
        $q = (string) $this->lex('identQuote');
        $e = (string) $this->lex('identEscape');
        return $q . str_replace($q, $e, $name) . $q;
    }

    /** `table`.`column`, or just the column when no table was given. */
    public function column(?string $table, string $column): string
    {
        return $table === null || $table === ''
            ? $this->ident($column)
            : $this->ident($table) . '.' . $this->ident($column);
    }

    // --- templates ----------------------------------------------------------

    /**
     * Fill a template with already-rendered arguments, producing a part list.
     *
     * Splicing part lists rather than strings is the whole point: an argument
     * carrying parameter slots keeps them. Concatenating the arguments into
     * strings first would work exactly until a literal contained something that
     * looked like a placeholder.
     *
     * Slot numbers are **absolute from the moment the literal is created** —
     * one translation has one parameter vector, held by the Translator, and an
     * intermediate Fragment carries indices into it rather than a vector of its
     * own. So splicing copies slots verbatim and never renumbers. The
     * alternative, every Fragment owning its own params and being renumbered on
     * each splice, is the same information arranged so that one missed
     * renumbering silently binds the wrong value to the wrong placeholder.
     *
     * `{key}` and `{key:n}` lexical forms are expanded here too. The generator
     * has already done that for the shipped map; this is for entries an
     * application registers at run time, which never pass through it.
     *
     * @param list<Fragment> $args
     * @return list<string|int>
     */
    public function fill(string $tpl, array $args, ?array $pos = null): array
    {
        $parts = [];
        $push = static function (string $s) use (&$parts): void {
            if ($s === '') {
                return;
            }
            $n = count($parts);
            if ($n > 0 && is_string($parts[$n - 1])) {
                $parts[$n - 1] .= $s;
            } else {
                $parts[] = $s;
            }
        };
        $splice = function (Fragment $f) use (&$parts, $push): void {
            foreach ($f->parts as $p) {
                if (is_string($p)) {
                    $push($p);
                } else {
                    $parts[] = $p;          // absolute already; see the note above
                }
            }
        };
        $join = function (array $subset) use ($splice, $push): void {
            $first = true;
            foreach ($subset as $f) {
                if (!$first) {
                    $push(', ');
                }
                $first = false;
                $splice($f);
            }
        };

        $i = 0;
        $len = strlen($tpl);
        while ($i < $len) {
            if ($tpl[$i] === '{' && ($tpl[$i + 1] ?? '') === '{') {
                $push('{');
                $i += 2;
                continue;
            }
            if ($tpl[$i] === '}' && ($tpl[$i + 1] ?? '') === '}') {
                $push('}');
                $i += 2;
                continue;
            }
            if ($tpl[$i] !== '{') {
                $push($tpl[$i]);
                $i++;
                continue;
            }
            $end = strpos($tpl, '}', $i);
            if ($end === false) {
                $push(substr($tpl, $i));
                break;
            }
            $slot = substr($tpl, $i + 1, $end - $i - 1);
            $i = $end + 1;

            if ($slot === '*') {
                $join($args);
                continue;
            }
            if (preg_match('/^([0-9]+):$/', $slot, $m) === 1) {
                $join(array_slice($args, (int) $m[1]));
                continue;
            }
            if (preg_match('/^[0-9]+$/', $slot) === 1) {
                $n = (int) $slot;
                if (!isset($args[$n])) {
                    refuse('E_SQL_UNSUPPORTED',
                        "the mapping for this expression asks for argument {$n}, "
                        . 'which it was not given', $pos);
                }
                $splice($args[$n]);
                continue;
            }
            // A lexical reference, from a runtime-registered template.
            [$key, $arg] = str_contains($slot, ':') ? explode(':', $slot, 2) : [$slot, null];
            $val = $this->lex($key);
            if (!is_string($val)) {
                refuse('E_SQL_UNSUPPORTED',
                    "a template used {{$slot}}, which is neither an argument nor a "
                    . "lexical entry of dialect {$this->dialect}", $pos);
            }
            if ($arg === null || $arg === '') {
                $push($val);
                continue;
            }
            $sub = $this->fill(str_replace('{0}', '{' . $arg . '}', $val), $args, $pos);
            foreach ($sub as $p) {
                if (is_string($p)) {
                    $push($p);
                } else {
                    $parts[] = $p;
                }
            }
        }
        return $parts;
    }
}
