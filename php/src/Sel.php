<?php
// Public host interface. See spec/SPEC.md §8.

declare(strict_types=1);

namespace Sel;

final class Program
{
    public string $source;
    /** @var array<string,mixed> */
    public array $ast;

    /** @param array<string,mixed> $ast */
    public function __construct(string $source, array $ast)
    {
        $this->source = $source;
        $this->ast = $ast;
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
        return Evaluator::evalNode($this->ast, new Context($root));
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
        self::collect($this->ast, [], $reads, $assigned);
        $out = array_values(array_diff(array_keys($reads), array_keys($assigned)));
        sort($out);
        return $out;
    }

    /**
     * @param array<string,mixed> $node
     * @param array<string,bool> $bound
     * @param array<string,bool> $reads
     * @param array<string,bool> $assigned
     */
    private static function collect(array $node, array $bound, array &$reads, array &$assigned): void
    {
        switch ($node['t']) {
            case 'var':
                if (!isset($bound[$node['name']])) {
                    $reads[$node['name']] = true;
                }
                return;

            case 'assign':
                $target = $node['target'];
                while ($target['t'] === 'index') {
                    self::collect($target['idx'], $bound, $reads, $assigned);
                    $target = $target['obj'];
                }
                // `A = x` defines A; `A[k] = x` and `A += x` also read it.
                if ($node['target']['t'] !== 'var' || $node['op'] !== '=') {
                    if (!isset($bound[$target['name']])) {
                        $reads[$target['name']] = true;
                    }
                }
                $assigned[$target['name']] = true;
                self::collect($node['value'], $bound, $reads, $assigned);
                return;

            case 'call':
                // An aggregate's three-argument form binds its second argument as
                // a name for the duration of the third.
                $binds = !empty($node['spec']['binds']);
                $n = count($node['args']);
                if ($binds && $n === 3 && $node['args'][1]['t'] === 'var') {
                    self::collect($node['args'][0], $bound, $reads, $assigned);
                    $inner = $bound;
                    $inner[$node['args'][1]['name']] = true;
                    $inner['_K'] = true;
                    self::collect($node['args'][2], $inner, $reads, $assigned);
                    return;
                }
                if ($binds && $n === 2) {
                    self::collect($node['args'][0], $bound, $reads, $assigned);
                    $inner = $bound;
                    $inner['_'] = true;
                    $inner['_K'] = true;
                    self::collect($node['args'][1], $inner, $reads, $assigned);
                    return;
                }
                foreach ($node['args'] as $arg) {
                    self::collect($arg, $bound, $reads, $assigned);
                }
                return;

            case 'seq':
            case 'list':
                foreach ($node['items'] as $item) {
                    self::collect($item, $bound, $reads, $assigned);
                }
                return;

            case 'index':
                self::collect($node['obj'], $bound, $reads, $assigned);
                self::collect($node['idx'], $bound, $reads, $assigned);
                return;

            case 'bin':
                self::collect($node['l'], $bound, $reads, $assigned);
                self::collect($node['r'], $bound, $reads, $assigned);
                return;

            case 'un':
                self::collect($node['x'], $bound, $reads, $assigned);
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
