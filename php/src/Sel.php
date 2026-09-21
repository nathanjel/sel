<?php
// Public host interface. See spec/SPEC.md §8.

declare(strict_types=1);

namespace Sel;

final class Program
{
    public string $source;
    /**
     * The parse tree, and the tree every other consumer reads: dependencies(),
     * the SQL translator, the hybrid planner. It is IMMUTABLE once here -- the
     * optimiser and the planner copy on the way down and never write into it
     * (sql/cases/25-hybrid-plans.sqlt asserts so) -- and a caller who builds a
     * Program from an AST of their own is held to the same rule. Not
     * `readonly`, because __destruct has to take it apart by reference, and
     * because reassigning the WHOLE tree is fine and noticed (§12.1): the
     * physical tree is keyed by the identity of $ast and rebuilt for a new
     * one. Writing into its nodes is what is unsupported.
     *
     * @var array<string,mixed>
     */
    public array $ast;

    /**
     * The physical tree run() evaluates: $ast after the in-memory optimiser,
     * built on the first run and kept, because the rewrite and the copy it
     * makes cost more than evaluating a small rule does. SQL translation never
     * sees it, since a physical rewrite (join pushdown) is not
     * something a database can be asked to run.
     *
     * @var array<string,mixed>|null
     */
    private ?array $physical = null;
    /** The $ast the physical tree was built from. */
    private ?array $physicalOf = null;

    /** @param array<string,mixed> $ast */
    public function __construct(string $source, array $ast)
    {
        $this->source = $source;
        $this->ast = $ast;
    }

    /**
     * The parse tree is taken apart iteratively rather than dropped.
     *
     * Freeing a nested array recurses once per level, and a left-leaning chain is
     * as deep as the source is long: at about two hundred thousand operators this
     * host printed the right answer and was then killed by SIGSEGV on its way
     * out. C++ does the same thing in `~Node`, which every path gets for free;
     * PHP has no destructor for an array, so this is where the call goes for a
     * program that compiled. Parser::parseTerm holds the other one, for a program
     * that did not.
     *
     * Parser::dismantle explains the shape and why objects would not have helped.
     */
    public function __destruct()
    {
        // Drop the cache key's share of $ast first: dismantling a copy-on-write
        // array that is still shared would separate the two and leave the
        // original, undismantled, to be destructed recursively.
        $this->physicalOf = null;
        Parser::dismantle($this->ast);
        // The physical tree shares most of its nodes with $ast and is as deep
        // as it is; once $ast has been taken apart this is the last holder of
        // those chains, so it gets the same treatment.
        if ($this->physical !== null) {
            Parser::dismantle($this->physical);
        }
    }

    /**
     * $context may be a Value, a plain array, or null. Returns a Value; the
     * context is mutated in place by any assignments the program performs.
     *
     * @param Value|array<mixed>|null $context
     */
    public function run($context = null): Value
    {
        $root = $context instanceof Value ? $context : Value::fromNative($context ?? []);
        return Evaluator::evalNode($this->physicalAst(), new Context($root));
    }

    /**
     * The optimised tree run() evaluates, built once.
     *
     * @return array<string,mixed>
     */
    public function physicalAst(): array
    {
        // Keyed by the identity of $ast: PHP arrays are values, but a copy on
        // write that has not been written to is one array, so `!==` is O(1)
        // until the caller reassigns $ast -- which is then noticed, as in the
        // other hosts (§12.1: "reassigning the whole tree is fine, and noticed").
        if ($this->physical === null || $this->physicalOf !== $this->ast) {
            $this->physical = Optimizer::optimize($this->ast, true);
            $this->physicalOf = $this->ast;
        }
        return $this->physical;
    }

    /**
     * Every variable the program reads without having assigned it first, found
     * statically. Only possible because SEL has no dynamic symbol operator; this
     * is what tells a frontend which inputs should re-trigger which rule.
     *
     * @return list<string>
     */
    public function dependencies(): array
    {
        $reads = [];
        $assigned = [];
        self::collect($this->ast, [], $reads, $assigned, 1);
        $out = array_values(array_diff(array_keys($reads), array_keys($assigned)));
        sort($out);
        return $out;
    }

    /**
     * The static walk of the tree, and the third thing in each host that recurses
     * over it. spec/SPEC.md 6.4 caps the other two -- the parser's nesting and the
     * evaluator's -- and says why: uncounted recursion over a tree the source can
     * make arbitrarily deep reaches the host's own stack limit. This walk was
     * uncounted, and `dependencies()` on a flat chain of about 48,000 operators
     * segfaulted this host once its memory_limit was out of the way.
     *
     * The depth rides as a parameter rather than as a counter with a guard, because
     * there is nothing to release on the way out -- which is also what lets the five
     * hosts spell this identically. Capped at the same MAX_DEPTH the evaluator uses
     * and tripping at the same node, so a program whose dependencies cannot be
     * computed is exactly a program that could not have been evaluated.
     *
     * @param array<string,mixed> $node
     * @param array<string,bool> $bound
     * @param array<string,bool> $reads
     * @param array<string,bool> $assigned
     */
    private static function collect(
        array $node,
        array $bound,
        array &$reads,
        array &$assigned,
        int $depth,
    ): void {
        if ($depth > MAX_DEPTH) {
            fail('E_DEPTH', 'expression nested too deeply', $node['pos']);
        }
        switch ($node['t']) {
            case 'var':
                if (!isset($bound[$node['name']])) {
                    $reads[$node['name']] = true;
                }
                return;

            case 'assign':
                $target = $node['target'];
                while ($target['t'] === 'index') {
                    self::collect($target['idx'], $bound, $reads, $assigned, $depth + 1);
                    $target = $target['obj'];
                }
                // `A = x` defines A; `A[k] = x` and `A += x` also read it.
                if ($node['target']['t'] !== 'var' || $node['op'] !== '=') {
                    if (!isset($bound[$target['name']])) {
                        $reads[$target['name']] = true;
                    }
                }
                $assigned[$target['name']] = true;
                self::collect($node['value'], $bound, $reads, $assigned, $depth + 1);
                return;

            case 'call':
                // Which arguments run inside the binder, and what they see, is
                // decided once, by Registry::bindingForm over the manifest's
                // forms (spec/builtins.md). No form -- a strict function, or a
                // count the evaluator would refuse -- and every argument is
                // read where the call stands.
                $form = Registry::bindingForm($node['name'] ?? '', $node['args']);
                if ($form === null) {
                    foreach ($node['args'] as $arg) self::collect($arg, $bound, $reads, $assigned, $depth + 1);
                    return;
                }
                $inner = null;
                foreach ($node['args'] as $i => $arg) {
                    $scope = $form['scopes'][$i];
                    if ($scope === 'binder') continue;
                    if ($scope === 'inner') {
                        if ($inner === null) {
                            $inner = $bound;
                            foreach ($form['binds'] as $b) $inner[$b] = true;
                        }
                        self::collect($arg, $inner, $reads, $assigned, $depth + 1);
                    } else {
                        self::collect($arg, $bound, $reads, $assigned, $depth + 1);
                    }
                }
                return;

            case 'seq':
            case 'list':
                foreach ($node['items'] as $item) {
                    self::collect($item, $bound, $reads, $assigned, $depth + 1);
                }
                return;

            case 'index':
                self::collect($node['obj'], $bound, $reads, $assigned, $depth + 1);
                self::collect($node['idx'], $bound, $reads, $assigned, $depth + 1);
                return;

            case 'bin':
                self::collect($node['l'], $bound, $reads, $assigned, $depth + 1);
                self::collect($node['r'], $bound, $reads, $assigned, $depth + 1);
                return;

            case 'un':
                self::collect($node['x'], $bound, $reads, $assigned, $depth + 1);
                return;
        }
    }
}

final class Sel
{
    public static function compile(string $source): Program
    {
        return new Program($source, Parser::parse($source));
    }

    /** @param Value|array<mixed>|null $context */
    public static function evaluate(string $source, $context = null): Value
    {
        return self::compile($source)->run($context);
    }

    /** @return list<string> */
    public static function functionNames(): array
    {
        return Registry::names();
    }
}
