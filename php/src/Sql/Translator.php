<?php
// Stage 4: render the normalised tree.
//
// Kind inference is folded into this walk rather than run as a separate pass.
// The walk is post-order, so every operand's kind is already known when its
// parent needs it — which is exactly what a separate pass would have computed,
// at the cost of a second traversal and a side table keyed by node identity
// that PHP's array-valued AST cannot cheaply provide. Whole-expression refusal
// is unaffected: nothing becomes characters until Fragment::asValue() is
// called, so a kind failure still escapes with no partial output.
//
// Aggregates are stage 2 and land in M3. Until then this refuses them, which is
// the same refusal path any untranslatable construct takes.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Value;

final class Translator
{
    /** Lowered by stage 2; none of them is a `funcs` entry. See sql/MAP.md §4. */
    private const AGGREGATES = ['ALL', 'ANY', 'MAP', 'FILTER', 'SUM', 'JOIN'];

    private string $dialect;
    private Emit $emit;
    private Bindings $bindings;
    private bool $strict;

    /** @var list<Value> */
    private array $params = [];
    /** @var list<string> */
    private array $paramKinds = [];
    /** @var array<string,bool> */
    private array $caveats = [];

    /** @param array<string,mixed> $options */
    public function __construct(string $dialect, Bindings $bindings, array $options = [])
    {
        $this->dialect = $dialect;
        $this->emit = new Emit($dialect);
        $this->bindings = $bindings;
        $this->strict = (bool) ($options['strict'] ?? false);
    }

    /** @param array<string,mixed> $ast */
    public function translate(array $ast): Fragment
    {
        Map::requireTarget($this->dialect);
        $this->bindings->checkAliases();

        $this->params = [];
        $this->paramKinds = [];
        $this->caveats = [];
        $f = $this->node(Normalise::run($ast));

        return new Fragment($f->parts, $f->kind, $this->dialect,
            $this->params, $this->paramKinds, array_keys($this->caveats));
    }

    // --- the walk -----------------------------------------------------------

    /** @param array<string,mixed> $n */
    private function node(array $n): Fragment
    {
        return match ($n['t']) {
            'num' => $this->literal(Value::num($n['v']), 'NUM'),
            'text' => $this->literal(Value::text($n['v']), 'TEXT'),
            'bool' => $this->literal(Value::bool($n['v']), 'BOOL'),
            'var' => $this->variable($n),
            'index' => $this->index($n),
            'un' => $this->unary($n),
            'bin' => $this->binary($n),
            'list', 'clist' => refuse('E_SQL_SHAPE',
                'a list is not a SQL value; a list can only be the thing an '
                . 'aggregate iterates', $n['pos']),
            'call' => $this->call($n),
            default => refuse('E_SQL_SHAPE',
                "cannot translate a {$n['t']} node", $n['pos']),
        };
    }

    /**
     * Every literal becomes a parameter slot; §9 of docs/SQL-TRANSLATION.md.
     *
     * $kind is both the static kind the expression has and the form the literal
     * is written in, and the two are the same thing only because this is where
     * the AST node kind is still known. A `num` node gives NUM, a `text` node
     * gives TEXT, and no later stage has to guess which of the two a value that
     * happens to read as a number came from.
     */
    private function literal(Value $v, string $kind): Fragment
    {
        $this->params[] = $v;
        $this->paramKinds[] = $kind === 'UNKNOWN' || $kind === 'LIST' ? 'TEXT' : $kind;
        return new Fragment([count($this->params)], $kind, $this->dialect);
    }

    /** @param array<string,mixed> $n */
    private function variable(array $n): Fragment
    {
        $b = $this->bindings->get($n['name'], $n['pos']);
        switch ($b['kind']) {
            case 'column':
                return $this->columnRef($b);

            case 'value':
                $v = $b['value'];
                if ($v->size() > 0) {
                    refuse('E_SQL_SHAPE',
                        "{$n['name']} is bound to a list, and a list is not a SQL "
                        . 'value; it can only be the thing an aggregate iterates',
                        $n['pos']);
                }
                return $this->literal($v, self::declaredKind($b, $v));

            case 'columns':
            case 'relation':
                refuse('E_SQL_SHAPE',
                    "{$n['name']} is bound as a {$b['kind']}, which names a set of "
                    . 'values rather than one; use it as the first argument of an '
                    . 'aggregate, not as a value on its own', $n['pos']);
        }
        refuse('E_SQL_BINDING', "unusable binding for {$n['name']}", $n['pos']);
    }

    /** @param array<string,mixed> $c */
    private function columnRef(array $c): Fragment
    {
        $sql = isset($c['raw'])
            ? (string) $c['raw']
            : $this->emit->column($c['table'] ?? null, (string) $c['column']);
        return new Fragment([$sql], (string) ($c['type'] ?? 'UNKNOWN'), $this->dialect);
    }

    /**
     * Indexing is meaningful against a relation or columns binding — a field or
     * a position — and against nothing else. `A[k]` on a scalar column would
     * have to reach inside a value SQL has no way to look inside.
     *
     * @param array<string,mixed> $n
     */
    private function index(array $n): Fragment
    {
        $obj = $n['obj'];
        if ($obj['t'] !== 'var') {
            refuse('E_SQL_SHAPE',
                'only a bound name can be indexed here; SQL has no way to index '
                . 'into the result of an expression', $n['pos']);
        }
        $b = $this->bindings->get($obj['name'], $obj['pos']);
        $key = $this->constantIndex($n['idx']);

        if ($b['kind'] === 'relation') {
            $field = strtoupper($key);
            if (!isset($b['fields'][$field])) {
                $known = array_keys($b['fields']);
                sort($known);
                refuse('E_SQL_BINDING',
                    "{$obj['name']}[\"{$key}\"] is not a field of that relation"
                    . ($known === [] ? '; it declares none' : '; it has ' . implode(', ', $known)),
                    $n['pos']);
            }
            return $this->columnRef($b['fields'][$field]);
        }
        if ($b['kind'] === 'columns') {
            $i = (int) $key;
            if (!preg_match('/^[0-9]+$/', $key) || $i < 1 || $i > count($b['items'])) {
                refuse('E_SQL_BINDING',
                    "{$obj['name']}[{$key}] is outside that binding's "
                    . count($b['items']) . ' column(s)', $n['pos']);
            }
            return $this->columnRef($b['items'][$i - 1]);
        }
        if ($b['kind'] === 'value') {
            $child = $b['value']->get($key);
            if ($child === null) {
                refuse('E_SQL_BINDING',
                    "{$obj['name']}[\"{$key}\"] is not a key of that value", $n['pos']);
            }
            if ($child->size() > 0) {
                refuse('E_SQL_SHAPE',
                    "{$obj['name']}[\"{$key}\"] is a list, not a SQL value", $n['pos']);
            }
            return $this->literal($child, self::declaredKind($b, $child));
        }
        refuse('E_SQL_SHAPE',
            "{$obj['name']} is bound as a column, which has no parts to index",
            $n['pos']);
    }

    /** @param array<string,mixed> $idx */
    private function constantIndex(array $idx): string
    {
        return match ($idx['t']) {
            'num', 'text' => (string) $idx['v'],
            default => refuse('E_SQL_SHAPE',
                'an index must be a constant here: the column it names has to be '
                . 'known before the query runs', $idx['pos']),
        };
    }

    /** @param array<string,mixed> $n */
    private function unary(array $n): Fragment
    {
        $x = $this->node($n['x']);
        if ($n['op'] === 'NOT') {
            $x = $this->requireBool($x, $n['x']['pos'], 'NOT');
        }
        return $this->apply('ops', $n['op'], [$x], $n['pos']);
    }

    /** @param array<string,mixed> $n */
    private function binary(array $n): Fragment
    {
        $op = $n['op'];

        if ($op === 'IN') {
            return $this->inOperator($n);
        }

        $l = $this->node($n['l']);
        $r = $this->node($n['r']);

        if (in_array($op, ['AND', 'OR', 'XOR'], true)) {
            $l = $this->requireBool($l, $n['l']['pos'], $op);
            $r = $this->requireBool($r, $n['r']['pos'], $op);
        }
        return $this->apply('ops', $op, [$l, $r], $n['pos'], $this->variantFor($op, [$l, $r]));
    }

    /**
     * `x IN list` is the one operator whose right operand is a list on purpose.
     * SEL's IN is EQL-based and therefore structural; SQL's is a value
     * comparison under a collation. For scalars under the binary collation the
     * two agree, and that is the only shape accepted.
     *
     * @param array<string,mixed> $n
     */
    private function inOperator(array $n): Fragment
    {
        $needle = $this->node($n['l']);
        $rhs = $n['r'];

        $elements = null;
        if ($rhs['t'] === 'list') {
            $elements = $rhs['items'];
        } elseif ($rhs['t'] === 'clist') {
            $elements = array_map(static fn (array $e): array => $e[1], $rhs['entries']);
        }

        if ($elements === null) {
            $r = $this->node($rhs);          // a scalar; spec §5.4's second case
            return $this->apply('ops', 'IN', [$needle, $r], $n['pos'], 'scalar');
        }

        $args = [$needle];
        foreach ($elements as $e) {
            $f = $this->node($e);
            if ($f->kind === 'LIST') {
                refuse('E_SQL_SHAPE',
                    'IN over a list of lists is structural in SEL and has no SQL '
                    . 'counterpart', $e['pos']);
            }
            $args[] = $f;
        }
        return $this->apply('ops', 'IN', $args, $n['pos'], 'list');
    }

    /** @param array<string,mixed> $n */
    private function call(array $n): Fragment
    {
        $name = $n['name'];

        if (in_array($name, self::AGGREGATES, true)) {
            refuse('E_SQL_UNSUPPORTED',
                "{$name} iterates, and turning iteration into SQL is stage 2, which "
                . 'this build does not have yet', $n['pos']);
        }
        if (in_array($name, ['COUNT', 'INDEXES', 'HAS'], true)) {
            refuse('E_SQL_UNSUPPORTED',
                "{$name} asks about a value's structure, which needs the same "
                . 'lowering the aggregates do; not in this build', $n['pos']);
        }
        if ($name === 'ABORT') {
            refuse('E_SQL_UNSUPPORTED',
                'ABORT raises an error, which is a control-flow effect and not a '
                . 'value a SQL expression can be', $n['pos']);
        }
        if ($name === 'IF' || $name === 'COND') {
            return $this->conditional($n);
        }

        $args = [];
        foreach ($n['args'] as $arg) {
            $f = $this->node($arg);
            if ($f->kind === 'LIST') {
                refuse('E_SQL_SHAPE',
                    "argument to {$name} is a list, and a SQL expression is a scalar",
                    $arg['pos']);
            }
            $args[] = $f;
        }
        return $this->apply('funcs', $name, $args, $n['pos']);
    }

    /**
     * IF and COND are the same construct: condition/result pairs and a default.
     * IF's two-argument form defaults to TEXT "" exactly as spec §7.2 says, so
     * one builder covers both and the CASE skeleton has one shape.
     *
     * @param array<string,mixed> $n
     */
    private function conditional(array $n): Fragment
    {
        $args = $n['args'];
        if ($n['name'] === 'IF' && count($args) === 2) {
            $args[] = ['t' => 'text', 'v' => '', 'pos' => $n['pos']];
        }

        $branchTpl = $this->skeleton('caseBranch', $n['pos']);
        $caseTpl = $this->skeleton('case', $n['pos']);

        $branches = [];
        $results = [];
        $last = count($args) - 1;
        for ($i = 0; $i < $last; $i += 2) {
            $cond = $this->requireBool($this->node($args[$i]), $args[$i]['pos'], $n['name']);
            $then = $this->node($args[$i + 1]);
            $results[] = $then;
            $branches[] = new Fragment(
                $this->fillNamed($branchTpl, ['cond' => [$cond], 'then' => [$then]], $n['pos']),
                'UNKNOWN', $this->dialect);
        }
        $else = $this->node($args[$last]);
        $results[] = $else;

        // The branches are joined by the skeleton's own spacing, not by ", ".
        $joined = [];
        foreach ($branches as $i => $b) {
            if ($i > 0) {
                $joined[] = ' ';
            }
            $joined[] = $b;
        }
        $parts = $this->fillNamed($caseTpl, [
            'branches' => $joined,
            'else' => [$else],
        ], $n['pos']);

        return new Fragment($parts, self::unify($results), $this->dialect);
    }

    // --- map application ----------------------------------------------------

    /**
     * Look one entry up, check it, and fill it. Every refusal in the map arrives
     * here, and a refusal spelled as a string in the map becomes the message the
     * caller reads — which is what makes one error class enough.
     *
     * @param list<Fragment> $args
     * @param array{line:int,col:int,offset:int} $pos
     */
    private function apply(string $section, string $key, array $args, array $pos,
                           ?string $variant = null): Fragment
    {
        $entry = Map::entry($this->dialect, $section, $key);
        $what = $section === 'ops' ? "the {$key} operator" : $key;

        if ($entry === Map::MISSING) {
            refuse('E_SQL_UNSUPPORTED',
                "{$what} has no mapping in dialect {$this->dialect}", $pos);
        }
        if ($entry === null) {
            refuse('E_SQL_UNSUPPORTED',
                "{$what} has no mapping in dialect {$this->dialect}", $pos);
        }
        if (is_string($entry)) {
            refuse('E_SQL_UNSUPPORTED',
                "{$what} has no mapping in dialect {$this->dialect} — {$entry}", $pos);
        }
        if (isset($entry['builder'])) {
            return ($entry['builder'])($this->emit, $args, ['pos' => $pos]);
        }

        if (isset($entry['since'])
            && !Map::versionAtLeast(Map::version($this->dialect), $entry['since'])) {
            refuse('E_SQL_DIALECT',
                "{$what} needs {$this->dialect} " . $entry['since']
                . ', and this map assumes ' . Map::version($this->dialect), $pos);
        }
        if (isset($entry['caveat'])) {
            if ($this->strict) {
                refuse('E_SQL_UNSUPPORTED',
                    "{$what} maps to something that is not exactly equivalent "
                    . "({$entry['caveat']}), and strict mode refuses those", $pos);
            }
            $this->caveats[$entry['caveat']] = true;
        }

        $tpl = $this->templateOf($entry, $args, $variant, $what, $pos);
        return new Fragment(
            $this->emit->fill($tpl, $args, $pos),
            $this->retKind($entry, $args),
            $this->dialect);
    }

    /**
     * @param array<string,mixed> $entry
     * @param list<Fragment> $args
     * @param array{line:int,col:int,offset:int} $pos
     */
    private function templateOf(array $entry, array $args, ?string $variant,
                                string $what, array $pos): string
    {
        if (isset($entry['variants'])) {
            if ($variant === null || !isset($entry['variants'][$variant])) {
                refuse('E_SQL_UNSUPPORTED',
                    "{$what} has no mapping in dialect {$this->dialect} for "
                    . ($variant === null ? 'this shape' : "{$variant} operands"), $pos);
            }
            return (string) $entry['variants'][$variant];
        }
        $tpl = $entry['tpl'];
        if (is_string($tpl)) {
            return $tpl;
        }
        $n = (string) count($args);
        if (!isset($tpl[$n])) {
            $have = array_keys($tpl);
            sort($have);
            refuse('E_SQL_UNSUPPORTED',
                "{$what} has no mapping in dialect {$this->dialect} for {$n} "
                . 'argument(s); it maps ' . implode(', ', $have), $pos);
        }
        return (string) $tpl[$n];
    }

    /**
     * Which variant a family selects. Not in the data — sql/MAP.md §4.3 fixes
     * three selectors and every host implements them identically.
     *
     * @param list<Fragment> $args
     */
    private function variantFor(string $op, array $args): ?string
    {
        static $numeric = ['==', '!=', '<', '<=', '>', '>='];
        static $textual = ['$==', '$!=', '$<', '$<=', '$>', '$>=', 'EQL'];

        if (in_array($op, $numeric, true)) {
            return $args[0]->kind === 'NUM' && $args[1]->kind === 'NUM' ? 'num' : 'coerce';
        }
        if (in_array($op, $textual, true)) {
            return 'text';
        }
        if ($op === '&') {
            return $args[0]->kind === 'BIN' || $args[1]->kind === 'BIN' ? 'bin' : 'text';
        }
        return null;
    }

    /**
     * @param array<string,mixed> $entry
     * @param list<Fragment> $args
     */
    private function retKind(array $entry, array $args): string
    {
        $ret = (string) $entry['ret'];
        if ($ret === '@concat') {
            foreach ($args as $a) {
                if ($a->kind === 'BIN') {
                    return 'BIN';
                }
            }
            return 'TEXT';
        }
        if (str_starts_with($ret, '@unify:')) {
            $pick = [];
            foreach (explode(',', substr($ret, 7)) as $i) {
                if (isset($args[(int) $i])) {
                    $pick[] = $args[(int) $i];
                }
            }
            return self::unify($pick);
        }
        return $ret;
    }

    /** @param list<Fragment> $fs */
    private static function unify(array $fs): string
    {
        $kind = null;
        foreach ($fs as $f) {
            if ($kind === null) {
                $kind = $f->kind;
            } elseif ($kind !== $f->kind) {
                return 'UNKNOWN';
            }
        }
        return $kind ?? 'UNKNOWN';
    }

    /**
     * The kind of a value supplied by the host, which is the one place the
     * num/text ambiguity cannot be resolved from the AST — there is no AST, the
     * host handed us a Value.
     *
     * So it is not guessed. BOOL and BIN are unambiguous; everything else is
     * TEXT, and therefore quoted, unless the binding declares `type: NUM`.
     * Guessing from looksNumeric() would emit a product code of "00123" as the
     * number 123, and a rule comparing it with $== would then be answered by
     * the database rather than by SEL's semantics.
     *
     * @param array<string,mixed> $b
     */
    private static function declaredKind(array $b, Value $v): string
    {
        if ($v->isBool()) {
            return 'BOOL';
        }
        if ($v->isBin()) {
            return 'BIN';
        }
        if ($v->isNone()) {
            return 'LIST';
        }
        $declared = $b['type'] ?? null;
        return $declared === 'NUM' ? 'NUM' : 'TEXT';
    }

    /**
     * SEL has no truthiness, and the point of translating is not to acquire
     * some. UNKNOWN passes, because a column of undeclared type may well be
     * boolean and the database is the one that knows.
     *
     * @param array{line:int,col:int,offset:int} $pos
     */
    private function requireBool(Fragment $f, array $pos, string $where): Fragment
    {
        if ($f->kind === 'BOOL' || $f->kind === 'UNKNOWN') {
            return $f;
        }
        refuse('E_SQL_SHAPE',
            "{$where} needs a BOOL here and this is {$f->kind}; SEL has no "
            . 'truthiness, so neither does its translation', $pos);
    }

    /** @param array{line:int,col:int,offset:int} $pos */
    private function skeleton(string $name, array $pos): string
    {
        $s = Map::entry($this->dialect, 'skel', $name);
        if ($s === Map::MISSING || $s === null) {
            refuse('E_SQL_UNSUPPORTED',
                "dialect {$this->dialect} has no {$name} skeleton", $pos);
        }
        if (is_string($s)) {
            refuse('E_SQL_UNSUPPORTED',
                "dialect {$this->dialect} cannot express {$name} — {$s}", $pos);
        }
        return (string) $s['tpl'];
    }

    /**
     * Fill a skeleton, whose placeholders are named rather than numbered.
     *
     * @param array<string, list<Fragment|string>> $slots
     * @param array{line:int,col:int,offset:int} $pos
     * @return list<string|int>
     */
    private function fillNamed(string $tpl, array $slots, array $pos): array
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

        $i = 0;
        $len = strlen($tpl);
        while ($i < $len) {
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
            $name = substr($tpl, $i + 1, $end - $i - 1);
            $i = $end + 1;
            if (!isset($slots[$name])) {
                refuse('E_SQL_UNSUPPORTED',
                    "a skeleton in dialect {$this->dialect} uses {{$name}}, which "
                    . 'is not one of its slots', $pos);
            }
            foreach ($slots[$name] as $item) {
                if (is_string($item)) {
                    $push($item);
                    continue;
                }
                foreach ($item->parts as $p) {
                    if (is_string($p)) {
                        $push($p);
                    } else {
                        $parts[] = $p;
                    }
                }
            }
        }
        return $parts;
    }
}
