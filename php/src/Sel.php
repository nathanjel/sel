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
        return $this->evaluate($context, true);
    }

    /**
     * `run()` for a program that will not run again. Building the
     * physical tree costs about as much as evaluating a small rule twice, and
     * pays for itself only when a body is evaluated per element (an aggregate) or
     * a pipeline can be fused. A program with neither is evaluated as written --
     * the reference the optimiser is held to -- and the optimiser is not run. One
     * that has either goes through run() unchanged.
     *
     * @param Value|array<mixed>|null $context
     */
    public function runOnce($context = null): Value
    {
        if ($this->physical !== null || self::repeatsWork($this->ast)) {
            return $this->run($context);
        }
        return $this->evaluate($context, false);
    }

    /**
     * run() and runOnce(): the context as a Value, then the optimised tree
     * ($physical) or the tree as written, evaluated with PHP's cycle collector
     * paused (SEL's values form no cycles, and a collection pass would walk the
     * whole resident context to find none).
     *
     * @param Value|array<mixed>|null $context
     */
    private function evaluate($context, bool $physical): Value
    {
        $root = $context instanceof Value ? $context : Value::fromNative($context ?? []);
        $wasGcEnabled = gc_enabled();
        if ($wasGcEnabled) {
            gc_disable();
        }
        try {
            return Evaluator::evalNode($physical ? $this->physicalAst() : $this->ast, new Context($root));
        } finally {
            if ($wasGcEnabled) {
                gc_enable();
            }
        }
    }

    /**
     * Whether any call in the tree binds a name (runs a body per element) or is a
     * pipeline stage. An iterative walk: the tree can be as deep as the source is
     * long.
     *
     * @param array<string,mixed> $ast
     */
    private static function repeatsWork(array $ast): bool
    {
        $stack = [$ast];
        while ($stack !== []) {
            $n = array_pop($stack);
            if (($n['t'] ?? null) === 'call') {
                $name = (string) ($n['name'] ?? '');
                $spec = Registry::lookup($name);
                if ($spec === null || ($spec['binds'] ?? false) || in_array($name, Optimizer::PIPELINE_OPS, true)) {
                    return true;
                }
            }
            foreach (Ast::children($n) as $child) $stack[] = $child;
        }
        return false;
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
     * Every variable the program can read before it has DEFINITELY assigned it
     * (spec/SPEC.md §8), found statically by a walk in evaluation order and
     * returned sorted, as every host returns it. Only possible because
     * SEL has no dynamic symbol operator; this is what tells a frontend which
     * inputs should re-trigger which rule.
     *
     * @return list<string>
     */
    public function dependencies(): array
    {
        $reads = [];
        $defined = [];
        self::collect($this->ast, [], $reads, $defined, 1);
        $out = array_keys($reads);
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
     * there is nothing to release on the way out -- which is also what lets every
     * host spell this identically. Capped at the same MAX_DEPTH the evaluator uses
     * and tripping at the same node, so a program whose dependencies cannot be
     * computed is exactly a program that could not have been evaluated.
     *
     * FLOW-SENSITIVE (§8): `$defined` is the set of names definitely assigned on
     * every path that reaches the node being walked, so it is threaded through the
     * walk in evaluation order. What is not always evaluated runs on a COPY of it
     * whose additions are dropped (or, for the branches of IF and COND, merged by
     * intersection): the right side of AND, OR, `??`, `???`, the later arguments of
     * COALESCE, GET and PATH, and every body an aggregate runs per element (there
     * may be none).
     *
     * @param array<string,mixed> $node
     * @param array<string,bool> $bound names bound by an enclosing aggregate
     * @param array<string,bool> $reads
     * @param array<string,bool> $defined
     */
    private static function collect(
        array $node,
        array $bound,
        array &$reads,
        array &$defined,
        int $depth,
    ): void {
        if ($depth > MAX_DEPTH) {
            fail('E_DEPTH', 'expression nested too deeply', $node['pos']);
        }
        $d = $depth + 1;
        switch ($node['t']) {
            case 'var':
                $name = $node['name'];
                if (!isset($bound[$name]) && !isset($defined[$name])) {
                    $reads[$name] = true;
                }
                return;

            case 'assign':
                $target = $node['target'];
                $indexes = [];
                while ($target['t'] === 'index') {
                    $indexes[] = $target['idx'];
                    $target = $target['obj'];
                }
                // Index expressions evaluate once, in source order, before the right side.
                foreach (array_reverse($indexes) as $idx) {
                    self::collect($idx, $bound, $reads, $defined, $d);
                }
                $name = $target['name'];
                // `A op= x` and `A[k] op= x` read their target; `A = x` and `A[k] = x` do not
                // (the latter creates A).
                if ($node['op'] !== '=' && !isset($bound[$name]) && !isset($defined[$name])) {
                    $reads[$name] = true;
                }
                self::collect($node['value'], $bound, $reads, $defined, $d);
                $defined[$name] = true;
                return;

            case 'call':
                self::collectCall($node, $bound, $reads, $defined, $d);
                return;

            case 'seq':
            case 'list':
                foreach ($node['items'] as $item) {
                    self::collect($item, $bound, $reads, $defined, $d);
                }
                return;

            case 'index':
                self::collect($node['obj'], $bound, $reads, $defined, $d);
                self::collect($node['idx'], $bound, $reads, $defined, $d);
                return;

            case 'bin':
                self::collect($node['l'], $bound, $reads, $defined, $d);
                if (in_array($node['op'], ['AND', 'OR', '??', '???'], true)) {
                    $rhs = $defined;   // may not run: its assignments are not definite
                    self::collect($node['r'], $bound, $reads, $rhs, $d);
                } else {
                    self::collect($node['r'], $bound, $reads, $defined, $d);
                }
                return;

            case 'un':
                self::collect($node['x'], $bound, $reads, $defined, $d);
                return;
        }
    }

    /**
     * A call. Which arguments run inside the binder, and what they see, is decided
     * once, by Registry::bindingForm over the manifest's forms (spec/builtins.md).
     * No form -- a strict function, or a count the evaluator would refuse -- and
     * every argument is read where the call stands, except for the lazy functions
     * that are not aggregates (IF, COND, COALESCE, GET, PATH).
     *
     * @param array<string,mixed> $node
     * @param array<string,bool> $bound
     * @param array<string,bool> $reads
     * @param array<string,bool> $defined
     */
    private static function collectCall(array $node, array $bound, array &$reads, array &$defined, int $d): void
    {
        $name = $node['name'] ?? '';
        $args = $node['args'];
        $form = Registry::bindingForm($name, $args);
        if ($form !== null) {
            $inner = null;
            foreach ($args as $i => $arg) {
                $scope = $form['scopes'][$i];
                if ($scope === 'binder') continue;
                if ($scope === 'inner') {
                    if ($inner === null) {
                        $inner = $bound;
                        foreach ($form['binds'] as $b) $inner[$b] = true;
                    }
                    $body = $defined;   // runs once per element: possibly never
                    self::collect($arg, $inner, $reads, $body, $d);
                } else {
                    self::collect($arg, $bound, $reads, $defined, $d);
                }
            }
            return;
        }
        switch ($name) {
            case 'IF':
                if (count($args) >= 1) self::collect($args[0], $bound, $reads, $defined, $d);
                $ends = [];
                foreach (array_slice($args, 1) as $arg) {
                    $branch = $defined;
                    self::collect($arg, $bound, $reads, $branch, $d);
                    $ends[] = $branch;
                }
                // A missing else branch yields NULL and assigns nothing.
                if (count($args) < 3) $ends[] = $defined;
                $defined = self::intersect($ends, $defined);
                return;

            case 'COND':
                // Conditions run in order until one holds; each value runs only on its own path.
                $ends = [];
                $n = count($args);
                for ($i = 0; $i + 1 < $n; $i += 2) {
                    self::collect($args[$i], $bound, $reads, $defined, $d);
                    $branch = $defined;
                    self::collect($args[$i + 1], $bound, $reads, $branch, $d);
                    $ends[] = $branch;
                }
                if ($n % 2 === 1) {
                    $branch = $defined;
                    self::collect($args[$n - 1], $bound, $reads, $branch, $d);
                    $ends[] = $branch;
                } else {
                    $ends[] = $defined;   // no default: NULL, nothing more assigned
                }
                $defined = self::intersect($ends, $defined);
                return;

            case 'COALESCE':
                foreach ($args as $i => $arg) {
                    if ($i === 0) {
                        self::collect($arg, $bound, $reads, $defined, $d);
                    } else {
                        $rest = $defined;
                        self::collect($arg, $bound, $reads, $rest, $d);
                    }
                }
                return;

            case 'GET':
            case 'PATH':
                foreach ($args as $i => $arg) {
                    if ($i < 2) {
                        self::collect($arg, $bound, $reads, $defined, $d);
                    } else {
                        $dflt = $defined;   // the default runs only when the key is absent
                        self::collect($arg, $bound, $reads, $dflt, $d);
                    }
                }
                return;
        }
        foreach ($args as $arg) {
            self::collect($arg, $bound, $reads, $defined, $d);
        }
    }

    /**
     * @param list<array<string,bool>> $sets
     * @param array<string,bool> $fallback
     * @return array<string,bool>
     */
    private static function intersect(array $sets, array $fallback): array
    {
        if ($sets === []) return $fallback;
        $out = array_shift($sets);
        foreach ($sets as $set) $out = array_intersect_key($out, $set);
        return $out;
    }
}

final class Sel
{
    /** The package version (the CHANGELOG's top heading; tools/check-version.sh). */
    public const VERSION = '0.10.0';

    /** @param mixed $source anything but a string is E_BAD_ARG (spec/SPEC.md §8) */
    public static function compile($source): Program
    {
        if (!is_string($source)) {
            fail('E_BAD_ARG', 'compile takes the source text as a string, not ' . get_debug_type($source));
        }
        return new Program($source, Parser::parse($source));
    }

    /** @param Value|array<mixed>|null $context */
    public static function evaluate($source, $context = null): Value
    {
        return self::compile($source)->runOnce($context);
    }

    /** @return list<string> */
    public static function functionNames(): array
    {
        return Registry::names();
    }

    /**
     * Adds an application's own strict function (spec/SPEC.md §8.1); register
     * it before compiling a program that calls it.
     *
     * @param callable(Args): Value $fn
     */
    public static function registerFunction(string $name, int $min, int $max, callable $fn): void
    {
        Registry::registerFunction($name, $min, $max, $fn);
    }
}
