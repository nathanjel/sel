<?php
// Translator errors. See sql/errors.md — codes are contract, messages are not,
// but unlike SelError the message here is expected to be read: a translator
// error is not a bug report, it is an answer.

declare(strict_types=1);

namespace Sel\Sql;

final class SqlError extends \Exception
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
 * Raise at the point of failure. Nothing wraps this on the way out, the same
 * rule spec/errors.md sets for the evaluator.
 *
 * @param array{line:int,col:int,offset:int}|null $pos
 * @return never
 */
function refuse(string $code, string $message, ?array $pos = null): void
{
    throw new SqlError($code, $message, $pos);
}
