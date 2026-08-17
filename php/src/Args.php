<?php
// Wraps the flattened argument vector. Values are evaluated at most once, so a
// built-in body can read the same argument repeatedly without thinking about it,
// and typed accessors report failures against the argument's own position.
//
// Arity is checked by the parser before any of this runs, so no built-in body
// counts its own arguments.

declare(strict_types=1);

namespace Sel;

final class Args
{
    /** @var list<array<string,mixed>> */
    private array $nodes;
    public string $name;
    /** @var array<string,mixed> */
    public array $pos;
    private Context $ctx;
    /** @var array<int, Value> */
    private array $vals = [];

    /** @param array<string,mixed> $node */
    public function __construct(array $node, Context $ctx)
    {
        $this->nodes = $node['args'];
        $this->name = $node['name'];
        $this->pos = $node['pos'];
        $this->ctx = $ctx;
    }

    public function count(): int
    {
        return count($this->nodes);
    }

    /** @return array<string,mixed> */
    public function node(int $i): array
    {
        return $this->nodes[$i];
    }

    /** @return array<string,mixed> */
    public function posOf(int $i): array
    {
        return $this->nodes[$i]['pos'];
    }

    public function val(int $i): Value
    {
        if (!isset($this->vals[$i])) {
            $this->vals[$i] = Evaluator::evalNode($this->nodes[$i], $this->ctx);
        }
        return $this->vals[$i];
    }

    /**
     * For lazy functions re-evaluating a body node under changed bindings.
     *
     * @param array<string,mixed> $node
     */
    public function evalNode(array $node): Value
    {
        return Evaluator::evalNode($node, $this->ctx);
    }

    public function text(int $i): string
    {
        return $this->val($i)->asText($this->posOf($i));
    }

    public function bytes(int $i): string
    {
        return $this->val($i)->asBytes($this->posOf($i));
    }

    public function bool(int $i): bool
    {
        return $this->val($i)->asBool($this->posOf($i));
    }

    /** @return array{neg:bool,digits:string,scale:int} */
    public function dec(int $i): array
    {
        return $this->val($i)->asDecimal($this->posOf($i));
    }

    public function int(int $i): int
    {
        $d = $this->dec($i);
        if (!Dec::isInteger($d)) {
            $n = $i + 1;
            fail('E_NOT_INT', "{$this->name} argument {$n} must be a whole number", $this->posOf($i));
        }
        return Dec::toInt($d);
    }

    public function nonNegInt(int $i): int
    {
        $n = $this->int($i);
        if ($n < 0) {
            $k = $i + 1;
            fail('E_RANGE', "{$this->name} argument {$k} must not be negative", $this->posOf($i));
        }
        return $n;
    }

    /**
     * Requires the argument to be a bare identifier in the source — the AST shape
     * check that gives aggregates their three-argument binder form.
     */
    public function symbol(int $i): string
    {
        $n = $this->nodes[$i];
        if ($n['t'] !== 'var' || !empty($n['grouped'])) {
            $k = $i + 1;
            fail('E_EXPECT_SYMBOL', "{$this->name} argument {$k} must be a plain name", $n['pos']);
        }
        return $n['name'];
    }
}
