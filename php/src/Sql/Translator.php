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
    /**
     * Aggregate binders, innermost last. Consulted before the bindings map, the
     * same precedence Sel\Context::lookup gives a binder over a variable, and
     * pushed per element so that nested aggregates shadow independently.
     *
     * @var list<array<string, Binder>>
     */
    private array $frames = [];
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
        $this->frames = [];
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
        $bound = $this->binder($n['name']);
        if ($bound !== null) {
            return $this->fromBinder($bound, $n);
        }
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
        $bound = $this->binder($obj['name']);
        if ($bound !== null) {
            return $this->indexBinder($bound, $obj['name'], $this->constantIndex($n['idx']), $n);
        }
        $b = $this->bindings->get($obj['name'], $obj['pos']);
        $key = $this->constantIndex($n['idx']);

        if ($b['kind'] === 'relation') {
            if (preg_match('/^[0-9]+$/', $key) === 1) {
                refuse('E_SQL_SHAPE',
                    "{$obj['name']}[{$key}] asks for a row by position, and a "
                    . 'relation has no first row without an ORDER BY that nothing '
                    . 'here can supply', $n['pos']);
            }
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
        $variant = $this->variantFor($op, [$l, $r]);
        if (self::isByteComparison($op)) {
            // See Emit::textOperand for why the operands are transformed here
            // rather than by the template. Selected by operator, NOT by the
            // variant being named "text": `&` has a variant of that name too and
            // is concatenation, not a comparison — casting and collating its
            // operands would be wrong and, briefly, was.
            $l = $this->emit->textOperand($l);
            $r = $this->emit->textOperand($r);
        }
        return $this->apply('ops', $op, [$l, $r], $n['pos'], $variant);
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
        // The needle is rendered per branch, and per comparison in the list
        // branch, rather than once up front. Rendering it eagerly bound a value
        // the list branch then never used, leaving one more entry in `params`
        // than there were placeholders — the mirror of the bug below.
        $rhs = $n['r'];

        // `x IN rel` is the one place a relation appears on the right of an
        // operator rather than as an aggregate's source, so it is lowered here
        // and not by aggregate(). {body} is the relation's declared scalar.
        if ($rhs['t'] === 'var' && $this->binder($rhs['name']) === null
            && $this->bindings->has($rhs['name'])) {
            $b = $this->bindings->get($rhs['name'], $rhs['pos']);
            if ($b['kind'] === 'relation') {
                $scalar = isset($b['scalar']) ? strtoupper((string) $b['scalar']) : null;
                if ($scalar === null || !isset($b['fields'][$scalar])) {
                    refuse('E_SQL_SHAPE',
                        "IN over {$rhs['name']} needs the binding to name a \"scalar\" "
                        . 'field: that is the column the subquery projects', $rhs['pos']);
                }
                return new Fragment(
                    $this->fillNamed($this->skeleton('inRelation', $n['pos']),
                        $this->relationSlots($b) + [
                            'needle' => [$this->emit->textOperand($this->node($n['l']))],
                            'body' => [$this->emit->textOperand(
                                $this->columnRef($b['fields'][$scalar]))],
                        ], $n['pos']),
                    'BOOL', $this->dialect);
            }
        }

        $elements = null;
        if ($rhs['t'] === 'list') {
            $elements = $rhs['items'];
        } elseif ($rhs['t'] === 'clist') {
            $elements = array_map(static fn (array $e): array => $e[1], $rhs['entries']);
        } elseif ($rhs['t'] === 'var' && $this->binder($rhs['name']) === null
                  && $this->bindings->has($rhs['name'])) {
            // A `value` binding holding a list is the natural way a host writes
            // an allow-list, and §5.4 says it unrolls exactly like a literal
            // list. The elements are already synthesised into nodes for the
            // aggregates; this reuses that rather than adding a second path.
            $b = $this->bindings->get($rhs['name'], $rhs['pos']);
            if ($b['kind'] === 'value' && $b['value']->size() > 0) {
                $elements = [];
                foreach ($this->valueElements($b, $rhs['pos']) as $binder) {
                    $elements[] = $binder->payload;
                }
            }
        }

        if ($elements === null) {
            $r = $this->node($rhs);          // a scalar; spec §5.4's second case
            return $this->apply('ops', 'IN',
                [$this->emit->textOperand($this->node($n['l'])),
                 $this->emit->textOperand($r)],
                $n['pos'], 'scalar');
        }

        // A literal list becomes a chain of byte comparisons rather than SQL's
        // IN. Casting each element inside a variadic template is not
        // expressible, and casting only the needle is not enough: on MariaDB
        // 11.8, CAST(3.0 AS CHAR) COLLATE utf8mb4_bin IN (3) is 1, because the
        // numeric right-hand side pulls the comparison back to numbers, where
        // SEL says FALSE. The cast has already cost the index SQL's IN would
        // have used, so the chain gives up nothing the fix had not already
        // spent.
        if ($elements === []) {
            return $this->literal(Value::bool(false), 'BOOL');
        }
        $tests = [];
        foreach ($elements as $e) {
            // Needle first, because it is emitted first. A parameter slot is
            // numbered when it is created and a positional placeholder carries
            // no number, so a driver binds values in creation order to
            // placeholders in text order — and the two are the same order only
            // if operands are rendered left to right.
            //
            // The needle is also rendered once per comparison rather than once
            // and spliced N times: splicing one Fragment twice puts the same
            // slot number in the output twice while `params` holds one entry.
            $needle = $this->emit->textOperand($this->node($n['l']));
            $f = $this->node($e);
            if ($f->kind === 'LIST') {
                refuse('E_SQL_SHAPE',
                    'IN over a list of lists is structural in SEL and has no SQL '
                    . 'counterpart', $e['pos']);
            }
            $tests[] = $this->apply('ops', 'EQL',
                [$needle, $this->emit->textOperand($f)], $e['pos'], 'text');
        }
        return $this->foldPairwise('OR', $tests, $n['pos']);
    }

    /**
     * Fold fragments pairwise-left through an operator's own template — the same
     * path a hand-written chain takes, so an unrolled aggregate and a written-out
     * chain produce the same bytes.
     *
     * @param list<Fragment> $parts
     * @param array{line:int,col:int,offset:int} $pos
     */
    public function foldPairwise(string $op, array $parts, array $pos): Fragment
    {
        $acc = array_shift($parts);
        foreach ($parts as $next) {
            $acc = $this->apply('ops', $op, [$acc, $next], $pos,
                $this->variantFor($op, [$acc, $next]));
        }
        return $acc;
    }

    /** @param array<string,mixed> $n */
    private function call(array $n): Fragment
    {
        $name = $n['name'];

        if (in_array($name, self::AGGREGATES, true)) {
            return $this->aggregate($n);
        }
        if ($name === 'COUNT') {
            return $this->count($n);
        }
        if ($name === 'HAS') {
            return $this->has($n);
        }
        if ($name === 'INDEXES') {
            refuse('E_SQL_SHAPE',
                'INDEXES yields a list of keys, and a SQL expression is a scalar',
                $n['pos']);
        }
        if ($name === 'ABORT') {
            refuse('E_SQL_UNSUPPORTED',
                'ABORT raises an error, which is a control-flow effect and not a '
                . 'value a SQL expression can be', $n['pos']);
        }
        if ($name === 'IF' || $name === 'COND') {
            return $this->conditional($n);
        }

        $n = $this->rewriteRegex($n);

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
     * Put a regex pattern through the language's own rewriter before it is
     * emitted. Spec §7.8 expands \d, \w and \s into explicit ASCII classes
     * rather than passing them through, because otherwise a library flag decides
     * what they mean — and MariaDB's engine decides differently. Verified on
     * 11.8: '٣' REGEXP '^\d$' is 1 there and FALSE in SEL.
     *
     * The rewriter is Sel\Builtins\Regex's own. A copy here would be a second
     * thing to keep in step, and it would fail silently when they drifted.
     *
     * Both the pattern and the flags must be literals: a pattern read from a
     * column cannot be rewritten, and the flag selects the template.
     *
     * @param array<string,mixed> $n
     * @return array<string,mixed>
     */
    private function rewriteRegex(array $n): array
    {
        static $regex = ['RMATCH' => 0, 'RFIND' => 0, 'RREPLACE' => 0, 'RGROUPS' => 0];
        if (!isset($regex[$n['name']])) {
            return $n;
        }
        $at = $regex[$n['name']];
        $pat = $n['args'][$at] ?? null;
        if ($pat === null || $pat['t'] !== 'text') {
            refuse('E_SQL_UNSUPPORTED',
                "{$n['name']} needs a literal pattern here: SEL rewrites \\d, \\w and "
                . '\\s into explicit ASCII classes before matching, and a pattern that '
                . 'is not known until the query runs cannot be rewritten',
                ($pat['pos'] ?? $n['pos']));
        }
        $source = \Sel\Builtins\Regex::portableSource((string) $pat['v'], $pat['pos']);

        // Dotall is permanently on in SEL (spec §7.8) and off by default in the
        // server, so every pattern carries (?s). The modifier goes in the
        // pattern rather than in the template because the flag argument is not
        // something the template should see: selecting an arity-keyed template
        // by argument count gave every three-argument call the case-insensitive
        // form, and left the flag bound as a parameter nothing emitted.
        $inline = '(?s)';

        $flagAt = $n['name'] === 'RREPLACE' ? 3 : 2;
        if (!isset($n['args'][$flagAt])) {
            $n['args'][$at]['v'] = $inline . $source;
            return $n;
        }
        $flags = $n['args'][$flagAt];
        if ($flags['t'] !== 'text') {
            refuse('E_SQL_UNSUPPORTED',
                "{$n['name']} needs literal flags here: their content selects the "
                . 'mapping, so they have to be known before the query runs',
                $flags['pos']);
        }

        // The flag string's CONTENT chooses the template. Choosing by argument
        // count instead meant every three-argument call got the case-insensitive
        // form, so RMATCH(p, s, "") matched case-insensitively where SEL does
        // not, and RMATCH(p, s, "zzz") compiled happily where SEL raises
        // E_BAD_ARG. An empty flag string is dropped so the two-argument
        // template applies.
        $text = (string) $flags['v'];
        if ($text !== '' && strtolower($text) !== 'i') {
            refuse('E_SQL_UNSUPPORTED',
                "{$n['name']} accepts only the i flag here, and SEL accepts only i "
                . 'at all; ' . \Sel\Value::quoteDump($text) . ' is not it',
                $flags['pos']);
        }
        if ($text !== '') {
            // The evaluator refuses i on a pattern with non-ASCII literals,
            // because case folding above ASCII is the one thing PCRE and
            // ECMAScript cannot be made to agree on. A translation that accepted
            // it would disagree with the host that refused it.
            foreach (\Sel\Utf8::codePoints($source) as $cp) {
                if ($cp > 0x7f) {
                    refuse('E_SQL_UNSUPPORTED',
                        'the i flag needs an ASCII-only pattern, which SEL requires '
                        . 'for the same reason and refuses here too', $flags['pos']);
                }
            }
            $inline = '(?si)';
        }
        $n['args'][$at]['v'] = $inline . $source;
        array_splice($n['args'], $flagAt, 1);      // folded into the pattern
        return $n;
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

    // --- aggregates: docs/SQL-TRANSLATION.md §7 -----------------------------
    //
    // Lowering runs inside this walk rather than as an AST pass before it. Two
    // of the three shapes have to render — a relation becomes a subquery, which
    // is characters — and the third needs the dialect's operator templates,
    // which a tree rewrite has no access to.

    private const AGG_RETURNS = ['ALL' => 'BOOL', 'ANY' => 'BOOL', 'SUM' => 'NUM',
                                 'JOIN' => 'TEXT', 'MAP' => 'LIST', 'FILTER' => 'LIST'];
    private const AGG_SKELETON = ['ALL' => 'all', 'ANY' => 'any', 'SUM' => 'sum',
                                  'JOIN' => 'join'];
    private const AGG_FOLD = ['ALL' => 'AND', 'ANY' => 'OR', 'SUM' => '+'];

    private function binder(string $name): ?Binder
    {
        for ($i = count($this->frames) - 1; $i >= 0; $i--) {
            if (isset($this->frames[$i][$name])) {
                return $this->frames[$i][$name];
            }
        }
        return null;
    }

    /** @param array<string,mixed> $n */
    private function fromBinder(Binder $b, array $n): Fragment
    {
        switch ($b->shape) {
            case Binder::NODE:
                return $this->node($b->payload);
            case Binder::COLUMN:
                return $this->columnRef($b->payload);
            case Binder::ROW:
                $rel = $b->payload;
                $scalar = isset($rel['scalar']) ? strtoupper((string) $rel['scalar']) : null;
                if ($scalar === null || !isset($rel['fields'][$scalar])) {
                    refuse('E_SQL_SHAPE',
                        "{$n['name']} names a row, and the relation does not say which "
                        . 'of its fields a bare reference means; give the binding a '
                        . '"scalar", or index the field you want', $n['pos']);
                }
                return $this->columnRef($rel['fields'][$scalar]);
        }
        refuse('E_SQL_SHAPE', (string) $b->reason, $n['pos']);
    }

    /** @param array<string,mixed> $n */
    private function indexBinder(Binder $b, string $name, string $key, array $n): Fragment
    {
        if ($b->shape === Binder::ROW) {
            if (preg_match('/^[0-9]+$/', $key) === 1) {
                refuse('E_SQL_SHAPE',
                    "{$name}[{$key}] asks for a row by position, and a relation has "
                    . 'no first row without an ORDER BY that nothing here can supply',
                    $n['pos']);
            }
            $field = strtoupper($key);
            if (!isset($b->payload['fields'][$field])) {
                $known = array_keys($b->payload['fields']);
                sort($known);
                refuse('E_SQL_BINDING',
                    "{$name}[\"{$key}\"] is not a field of that relation"
                    . ($known === [] ? '; it declares none' : '; it has ' . implode(', ', $known)),
                    $n['pos']);
            }
            return $this->columnRef($b->payload['fields'][$field]);
        }
        if ($b->shape === Binder::NODE) {
            $elem = self::childOf($b->payload, $key);
            if ($elem === null) {
                refuse('E_SQL_BINDING',
                    "{$name}[\"{$key}\"] is not a key of that element", $n['pos']);
            }
            return $this->node($elem);
        }
        refuse('E_SQL_SHAPE',
            "{$name} names a single column, which has no parts to index", $n['pos']);
    }

    /**
     * The 2- and 3-argument forms: `_` by default, a bare name when given.
     *
     * @param array<string,mixed> $n
     * @return array{0:string, 1:array<string,mixed>}
     */
    private static function aggShape(array $n): array
    {
        if (count($n['args']) === 3) {
            if ($n['args'][1]['t'] !== 'var') {
                refuse('E_SQL_SHAPE',
                    "the binder of {$n['name']} must be a bare name", $n['args'][1]['pos']);
            }
            return [$n['args'][1]['name'], $n['args'][2]];
        }
        return ['_', $n['args'][1]];
    }

    /**
     * Classify an aggregate's first argument into one of the three shapes,
     * absorbing any FILTER on the way through. Recursive, so
     * FILTER(FILTER(L, p1), p2) conjoins both predicates over L.
     *
     * @param array<string,mixed> $src
     * @param array<string,mixed> $call
     * @return array<string,mixed>
     */
    private function source(array $src, array $call): array
    {
        if ($src['t'] === 'call' && $src['name'] === 'FILTER') {
            [$fBinder, $fBody] = self::aggShape($src);
            $inner = $this->source($src['args'][0], $call);
            $inner['filters'][] = ['binder' => $fBinder, 'body' => $fBody];
            return $inner;
        }
        if ($src['t'] === 'call' && $src['name'] === 'MAP') {
            refuse('E_SQL_UNSUPPORTED',
                'MAP as the thing an aggregate iterates is not translated: unlike '
                . 'FILTER, which only decides whether an element takes part, MAP '
                . 'changes what the element is, so the two binders mean different '
                . 'things and binding both to one element is not enough. See '
                . 'docs/SQL-TRANSLATION.md §7.5', $src['pos']);
        }

        // Built by a helper rather than by `+`: PHP's array union keeps the
        // LEFT operand's value for a duplicated key, so `$base + [... 'scalarRule'
        // => true]` left scalarRule permanently false and made COUNT of a scalar
        // answer 1 where the evaluator answers 0.
        if ($src['t'] === 'list') {
            $out = [];
            foreach ($src['items'] as $i => $item) {
                $out[(string) ($i + 1)] = Binder::node($item);
            }
            return self::staticSource($out);
        }
        if ($src['t'] === 'clist') {
            $out = [];
            foreach ($src['entries'] as [$k, $v]) {
                $out[$k] = Binder::node($v);
            }
            return self::staticSource($out);
        }

        if ($src['t'] === 'var') {
            $bound = $this->binder($src['name']);
            if ($bound !== null) {
                if ($bound->shape === Binder::NODE) {
                    return $this->source($bound->payload, $call);
                }
                if ($bound->shape === Binder::NONE) {
                    refuse('E_SQL_SHAPE', (string) $bound->reason, $src['pos']);
                }
                // A column or a row is one value, so it is a one-element list
                // containing itself — spec §7.3, the same rule the evaluator
                // applies. This is what makes ALL(V, ALL(V, …)) work.
                return self::staticSource(['1' => $bound], true);
            }
            $b = $this->bindings->get($src['name'], $src['pos']);
            if ($b['kind'] === 'relation') {
                return ['shape' => 'relation', 'relation' => $b, 'filters' => [],
                        'scalarRule' => false];
            }
            if ($b['kind'] === 'columns') {
                $out = [];
                foreach ($b['items'] as $i => $item) {
                    $out[(string) ($i + 1)] = Binder::column($item);
                }
                return ['shape' => 'columns', 'elements' => $out, 'filters' => [],
                        'scalarRule' => false];
            }
            if ($b['kind'] === 'value') {
                $v = $b['value'];
                // A scalar value binding is one value, so the scalar rule applies
                // to it exactly as it does to a column.
                return self::staticSource($this->valueElements($b, $src['pos']),
                    $v->size() === 0 && !$v->isNone());
            }
        }

        // Anything else that is one value: the scalar rule again.
        return self::staticSource(['1' => Binder::node($src)], true);
    }

    /**
     * @param array<string, Binder> $elements
     * @return array<string,mixed>
     */
    private static function staticSource(array $elements, bool $scalarRule = false): array
    {
        return ['shape' => 'static', 'elements' => $elements,
                'filters' => [], 'scalarRule' => $scalarRule];
    }

    /**
     * A `value` binding holds Values, not AST nodes, so its children are
     * synthesised into nodes before binding. A child with children becomes a
     * `clist`; a scalar becomes the node kind its declared type asks for, which
     * is the same rule declaredKind applies to the value as a whole — so what
     * decides quoting is stated once.
     *
     * @param array<string,mixed> $b
     * @param array{line:int,col:int,offset:int} $pos
     * @return array<string, Binder>
     */
    private function valueElements(array $b, array $pos): array
    {
        $v = $b['value'];
        if ($v->size() === 0) {
            // A NONE with no children is genuinely empty — what FILTER returns
            // when nothing matched. A scalar is a one-element list of itself.
            return $v->isNone() ? [] : ['1' => Binder::node($this->valueNode($v, $b, $pos))];
        }
        $out = [];
        foreach ($v->entries() as [$k, $child]) {
            $out[$k] = Binder::node($this->valueNode($child, $b, $pos));
        }
        return $out;
    }

    /**
     * @param array<string,mixed> $b
     * @param array{line:int,col:int,offset:int} $pos
     * @return array<string,mixed>
     */
    private function valueNode(Value $v, array $b, array $pos): array
    {
        if ($v->size() > 0) {
            $entries = [];
            foreach ($v->entries() as [$k, $child]) {
                $entries[] = [$k, $this->valueNode($child, $b, $pos)];
            }
            return ['t' => 'clist', 'entries' => $entries, 'pos' => $pos];
        }
        if ($v->isBool()) {
            return ['t' => 'bool', 'v' => $v->asBool($pos), 'pos' => $pos];
        }
        if ($v->isBin()) {
            refuse('E_SQL_SHAPE',
                'a BIN element of a value binding has no literal node to become; '
                . 'bind it as a column, or convert it before translating', $pos);
        }
        return ['t' => ($b['type'] ?? null) === 'NUM' ? 'num' : 'text',
                'v' => $v->asText($pos), 'pos' => $pos];
    }

    /** @param array<string,mixed> $n */
    private function aggregate(array $n): Fragment
    {
        $name = $n['name'];
        if ($name === 'MAP' || $name === 'FILTER') {
            refuse('E_SQL_SHAPE',
                "{$name} yields a list, and a SQL expression is a scalar; it can "
                . 'only be the thing another aggregate iterates', $n['pos']);
        }
        if ($name === 'JOIN') {
            return $this->joinAggregate($n);
        }

        [$binderName, $body] = self::aggShape($n);
        $src = $this->source($n['args'][0], $n);

        if ($src['shape'] === 'relation') {
            $rendered = $this->withRow($src, $binderName,
                fn (): Fragment => $this->aggBody($name, $body, $src, $n));
            return $this->relationAggregate($name, $src['relation'], $rendered, $n);
        }

        $parts = [];
        foreach ($src['elements'] as $key => $elem) {
            $parts[] = $this->withElement($src, $binderName, $elem, (string) $key, $n,
                fn (): Fragment => $this->aggBody($name, $body, $src, $n));
        }
        if ($parts === []) {
            return match ($name) {                    // spec §7.3's empty cases
                'ALL' => $this->literal(Value::bool(true), 'BOOL'),
                'ANY' => $this->literal(Value::bool(false), 'BOOL'),
                default => $this->literal(Value::num('0'), 'NUM'),
            };
        }
        return count($parts) === 1
            ? $parts[0]
            : $this->foldPairwise(self::AGG_FOLD[$name], $parts, $n['pos']);
    }

    /**
     * Render the body, and combine it with any absorbed FILTER predicates.
     *
     * The four rewrites of §7.5, and each is NULL-safe under the skeletons of
     * §7.3: for ALL a NULL predicate with a FALSE body gives a NULL result,
     * which `IS NOT TRUE` includes — the element is treated as having been in
     * the filter and having failed, which is the conservative reading.
     *
     * @param array<string,mixed> $body
     * @param array<string,mixed> $src
     * @param array<string,mixed> $n
     */
    private function aggBody(string $name, array $body, array $src, array $n): Fragment
    {
        $q = $this->node($body);
        $q = $name === 'SUM'
            ? $this->requireNum($q, $body['pos'], $name)
            : $this->requireBool($q, $body['pos'], $name);

        foreach ($src['filters'] as $filter) {
            $p = $this->requireBool($this->node($filter['body']), $filter['body']['pos'], 'FILTER');
            if ($name === 'SUM') {
                $q = $this->caseWhen($p, $q, $this->literal(Value::num('0'), 'NUM'), $n['pos']);
                continue;
            }
            $q = $name === 'ALL'
                ? $this->apply('ops', 'OR', [$this->apply('ops', 'NOT', [$p], $n['pos']), $q], $n['pos'])
                : $this->apply('ops', 'AND', [$p, $q], $n['pos']);
        }
        return $q;
    }

    /**
     * Push a frame for one element of a static or columns unroll and render.
     * Every absorbed FILTER's binder is bound to the same element, which is what
     * makes absorption three lines rather than a substitution pass — see §7.5.
     *
     * @param array<string,mixed> $src
     * @param array<string,mixed> $n
     */
    private function withElement(array $src, string $binderName, Binder $elem,
                                 string $key, array $n, callable $render): Fragment
    {
        $frame = [$binderName => $elem,
                  '_K' => Binder::node(['t' => 'text', 'v' => $key, 'pos' => $n['pos']])];
        foreach ($src['filters'] as $filter) {
            $frame[$filter['binder']] = $elem;
        }
        $this->frames[] = $frame;
        try {
            return $render();
        } finally {
            array_pop($this->frames);
        }
    }

    /**
     * The same for a relation, where there is one frame rather than one per
     * element. `_K` is in scope only to refuse: a row has no portable key, and
     * inventing one — ROW_NUMBER(), the primary key — would be a guess about the
     * schema this layer is careful never to make.
     *
     * @param array<string,mixed> $src
     */
    private function withRow(array $src, string $binderName, callable $render): Fragment
    {
        $row = Binder::row($src['relation']);
        $frame = [$binderName => $row,
                  '_K' => Binder::none('a row of a relation has no key: SQL rows are '
                      . 'unordered and unkeyed unless the schema says otherwise, and '
                      . 'guessing which column is the key is not something this layer does')];
        foreach ($src['filters'] as $filter) {
            $frame[$filter['binder']] = $row;
        }
        $this->frames[] = $frame;
        try {
            return $render();
        } finally {
            array_pop($this->frames);
        }
    }

    /**
     * @param array<string,mixed> $rel
     * @param array<string,mixed> $n
     */
    private function relationAggregate(string $name, array $rel, Fragment $body, array $n): Fragment
    {
        return new Fragment(
            $this->fillNamed($this->skeleton(self::AGG_SKELETON[$name], $n['pos']),
                $this->relationSlots($rel) + ['body' => [$body]], $n['pos']),
            self::AGG_RETURNS[$name], $this->dialect);
    }

    /**
     * `{from}` is the table and alias, or a query the binding carries; `{corr}`
     * is the join back to the outer row, or the dialect's TRUE when the binding
     * has none — an uncorrelated relation is a subquery over the whole table,
     * which is legal and occasionally what you want.
     *
     * @param array<string,mixed> $rel
     * @return array<string, list<string>>
     */
    private function relationSlots(array $rel): array
    {
        $from = is_array($rel['from']) && isset($rel['from']['raw'])
            ? (string) $rel['from']['raw']
            : $this->emit->ident((string) $rel['from']);
        if (!empty($rel['alias'])) {
            $from .= ' ' . $this->emit->ident((string) $rel['alias']);
        }
        return ['from' => [$from],
                'corr' => [(string) ($rel['correlate']['raw'] ?? $this->emit->lex('true'))]];
    }

    /** @param array<string,mixed> $n */
    private function count(array $n): Fragment
    {
        $src = $this->source($n['args'][0], $n);

        // COUNT is the number of children, so the scalar rule does not apply to
        // it: spec §7.4 says a value with no children counts 0, where §7.3's
        // one-element rule is about what an aggregate iterates.
        if ($src['shape'] !== 'relation' && $src['scalarRule'] && $src['filters'] === []) {
            return $this->literal(Value::num('0'), 'NUM');
        }
        if ($src['filters'] !== []) {
            // COUNT(FILTER(L, p)) is SUM(L, CASE WHEN p THEN 1 ELSE 0 END).
            $body = ['t' => 'num', 'v' => '1', 'pos' => $n['pos']];
            if ($src['shape'] === 'relation') {
                $rendered = $this->withRow($src, '_',
                    fn (): Fragment => $this->aggBody('SUM', $body, $src, $n));
                return $this->relationAggregate('SUM', $src['relation'], $rendered, $n);
            }
            $parts = [];
            foreach ($src['elements'] as $key => $elem) {
                $parts[] = $this->withElement($src, '_', $elem, (string) $key, $n,
                    fn (): Fragment => $this->aggBody('SUM', $body, $src, $n));
            }
            return $parts === []
                ? $this->literal(Value::num('0'), 'NUM')
                : (count($parts) === 1 ? $parts[0]
                    : $this->foldPairwise('+', $parts, $n['pos']));
        }
        if ($src['shape'] === 'relation') {
            return new Fragment(
                $this->fillNamed($this->skeleton('count', $n['pos']),
                    $this->relationSlots($src['relation']), $n['pos']),
                'NUM', $this->dialect);
        }
        return $this->literal(Value::num((string) count($src['elements'])), 'NUM');
    }

    /** @param array<string,mixed> $n */
    private function has(array $n): Fragment
    {
        if (!in_array($n['args'][1]['t'], ['text', 'num'], true)) {
            refuse('E_SQL_SHAPE',
                'HAS needs a constant key here: which column it asks about has to '
                . 'be known before the query runs', $n['args'][1]['pos']);
        }
        $key = (string) $n['args'][1]['v'];
        $src = $this->source($n['args'][0], $n);
        if ($src['filters'] !== []) {
            refuse('E_SQL_SHAPE',
                'HAS over a FILTER would have to know at translation time which '
                . 'elements the filter kept', $n['pos']);
        }
        $found = $src['shape'] === 'relation'
            ? isset($src['relation']['fields'][strtoupper($key)])
            : (!$src['scalarRule'] && isset($src['elements'][$key]));
        return $this->literal(Value::bool($found), 'BOOL');
    }

    /**
     * JOIN is strict, not an aggregate: its second argument is a separator.
     * Folded pairwise through the dialect's own concatenation, because `&` is
     * what SEL's JOIN is, and a variadic concat would need a lexical key spelled
     * two ways for the sake of one function.
     *
     * @param array<string,mixed> $n
     */
    private function joinAggregate(array $n): Fragment
    {
        $src = $this->source($n['args'][0], $n);
        if ($src['shape'] === 'relation') {
            $rel = $src['relation'];
            $scalar = isset($rel['scalar']) ? strtoupper((string) $rel['scalar']) : null;
            if ($scalar === null || !isset($rel['fields'][$scalar])) {
                refuse('E_SQL_SHAPE',
                    'JOIN over a relation needs the binding to name a "scalar" field',
                    $n['pos']);
            }
            $body = $this->columnRef($rel['fields'][$scalar]);
            $skel = $this->skeleton('join', $n['pos']);      // refuses with the map's reason
            return new Fragment(
                $this->fillNamed($skel, $this->relationSlots($rel)
                    + ['body' => [$body], 'sep' => [$this->node($n['args'][1])]], $n['pos']),
                'TEXT', $this->dialect);
        }

        $parts = [];
        foreach ($src['elements'] as $key => $elem) {
            if ($parts !== []) {
                // Rendered per gap, not once and reused: see the note in
                // inOperator on why splicing one Fragment twice breaks `params`.
                $parts[] = $this->node($n['args'][1]);
            }
            $parts[] = $this->withElement($src, '_', $elem, (string) $key, $n,
                fn (): Fragment => $this->fromBinder($elem, $n));
        }
        if ($parts === []) {
            return $this->literal(Value::text(''), 'TEXT');
        }
        return count($parts) === 1 ? $parts[0] : $this->foldPairwise('&', $parts, $n['pos']);
    }

    /** @param array<string,mixed> $node @return array<string,mixed>|null */
    private static function childOf(array $node, string $key): ?array
    {
        if ($node['t'] === 'list') {
            return preg_match('/^[0-9]+$/', $key) === 1
                ? ($node['items'][(int) $key - 1] ?? null)
                : null;
        }
        if ($node['t'] === 'clist') {
            foreach ($node['entries'] as [$k, $v]) {
                if ($k === $key) {
                    return $v;
                }
            }
        }
        return null;
    }

    /** @param array{line:int,col:int,offset:int} $pos */
    private function caseWhen(Fragment $cond, Fragment $then, Fragment $else, array $pos): Fragment
    {
        $branch = new Fragment(
            $this->fillNamed($this->skeleton('caseBranch', $pos),
                ['cond' => [$cond], 'then' => [$then]], $pos),
            'UNKNOWN', $this->dialect);
        return new Fragment(
            $this->fillNamed($this->skeleton('case', $pos),
                ['branches' => [$branch], 'else' => [$else]], $pos),
            $then->kind === $else->kind ? $then->kind : 'UNKNOWN', $this->dialect);
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

    /** The operators specified as byte comparisons: spec §5.3 and §5.4. */
    private static function isByteComparison(string $op): bool
    {
        static $ops = ['$==' => 0, '$!=' => 0, '$<' => 0, '$<=' => 0,
                       '$>' => 0, '$>=' => 0, 'EQL' => 0, 'IN' => 0];
        return isset($ops[$op]);
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

    /**
     * SUM's counterpart to requireBool. UNKNOWN passes for the same reason it
     * does there: an undeclared column may well be numeric, and the database is
     * the one that knows.
     *
     * @param array{line:int,col:int,offset:int} $pos
     */
    private function requireNum(Fragment $f, array $pos, string $where): Fragment
    {
        if ($f->kind === 'NUM' || $f->kind === 'UNKNOWN') {
            return $f;
        }
        refuse('E_SQL_SHAPE',
            "{$where} adds its body up, so it needs a number here and this is "
            . "{$f->kind}", $pos);
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
