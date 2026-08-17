<?php
// Evaluation context: the root value, plus the shallow stack of aggregate
// binders. There is no lexical scoping and no call stack of frames.

declare(strict_types=1);

namespace Sel;

final class Context
{
    public Value $root;
    /** @var list<array<string, Value>> */
    public array $frames = [];
    public int $depth = 0;

    public function __construct(?Value $root = null)
    {
        $this->root = $root ?? Value::none();
    }

    public function lookup(string $name): ?Value
    {
        for ($i = count($this->frames) - 1; $i >= 0; $i--) {
            if (isset($this->frames[$i][$name])) {
                return $this->frames[$i][$name];
            }
        }
        return $this->root->get($name);
    }

    public function isBound(string $name): bool
    {
        for ($i = count($this->frames) - 1; $i >= 0; $i--) {
            if (isset($this->frames[$i][$name])) {
                return true;
            }
        }
        return false;
    }

    /** @param array<string, Value> $map */
    public function pushFrame(array $map): void
    {
        $this->frames[] = $map;
    }

    public function popFrame(): void
    {
        array_pop($this->frames);
    }
}
