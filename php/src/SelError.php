<?php
// SEL errors. See spec/errors.md — codes are contract, messages are not.

declare(strict_types=1);

namespace Sel;

/**
 * spec/SPEC.md §6.4's three caps, which are one number. The parser's nesting, the
 * evaluator's, and a value's -- each is a recursion over a structure the input can
 * grow without bound, and each finds this host's own stack instead of an error if
 * it is not counted. It lives here, with fail(), because this file is the one
 * every other requires and none requires back, and because the number and the
 * E_DEPTH it raises are the same fact.
 */
const MAX_DEPTH = 200;

final class SelError extends \Exception
{
    // Untyped so it can override Exception::$code, whose type must be omitted.
    /** @var string */
    public $code;
    public int $line;
    public int $col;
    public int $offset;

    /** @param array{line:int,col:int,offset:int}|null $pos */
    public function __construct(string $code, string $message, ?array $pos = null)
    {
        parent::__construct($message);
        $this->code = $code;
        $this->line = $pos['line'] ?? 0;
        $this->col = $pos['col'] ?? 0;
        $this->offset = $pos['offset'] ?? 0;
    }

    public function __toString(): string
    {
        return "{$this->code} at {$this->line}:{$this->col}: {$this->getMessage()}";
    }
}

/**
 * Raise at the innermost point of failure. Nothing wraps this on the way out.
 *
 * @param array{line:int,col:int,offset:int}|null $pos
 * @return never
 */
function fail(string $code, string $message, ?array $pos = null): void
{
    throw new SelError($code, $message, $pos);
}
