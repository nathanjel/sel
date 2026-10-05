<?php
// SEL errors. See spec/errors.md — codes are contract, messages are not.

declare(strict_types=1);

namespace Sel;

// The limits this file's MAX_DEPTH is defined from. Required here, not only
// from the bootstrap, because tools load this file on its own.
require_once __DIR__ . '/Limits.php';

/**
 * spec/SPEC.md §6.4's three caps, which are one number. The parser's nesting, the
 * evaluator's, and a value's -- each is a recursion over a structure the input can
 * grow without bound, and each finds this host's own stack instead of an error if
 * it is not counted. It lives here, with fail(), because this file is the one
 * every other requires and none requires back, and because the number and the
 * E_DEPTH it raises are the same fact.
 */
const MAX_DEPTH = Limits::MAX_DEPTH;   // spec/limits.json, checked against the spec text

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

    /**
     * An error that goes through a queue, a session or a cache must come back with its
     * code and position (PY-C16's PHP equivalent): Exception's own serialisation does
     * not round-trip this class, whose `code` is a string and whose `line` is a
     * source line, not the file line Exception uses it for.
     *
     * @return array{code:string,message:string,line:int,col:int,offset:int}
     */
    public function __serialize(): array
    {
        return [
            'code' => $this->code, 'message' => $this->getMessage(),
            'line' => $this->line, 'col' => $this->col, 'offset' => $this->offset,
        ];
    }

    /** @param array<string,mixed> $data */
    public function __unserialize(array $data): void
    {
        $this->code = (string) ($data['code'] ?? '');
        $this->message = (string) ($data['message'] ?? '');
        $this->line = (int) ($data['line'] ?? 0);
        $this->col = (int) ($data['col'] ?? 0);
        $this->offset = (int) ($data['offset'] ?? 0);
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
 */
function fail(string $code, string $message, ?array $pos = null): never
{
    throw new SelError($code, $message, $pos);
}
