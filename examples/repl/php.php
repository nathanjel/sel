<?php
// A read-eval-print loop — the whole of it, in PHP.
//
//   php examples/repl/php.php
//   php examples/repl/php.php < examples/repl/session.txt
//
// One context lives across lines, so a variable assigned on one line is there
// on the next. Two commands besides SEL itself: `:deps <expr>` lists what an
// expression reads, and `:reset` empties the context. Errors print their code
// and position — the message is human text and may differ between hosts; the
// code and the position may not.
//
// The four files beside this one print byte-identical output for the session in
// session.txt.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Value;

// EXAMPLE-BEGIN repl
function show(Value $value): string
{
    if ($value->isBool()) {
        return $value->asBool() ? 'TRUE' : 'FALSE';
    }
    if ($value->isNull()) {
        return 'NULL';
    }
    if ($value->size() > 0 || $value->isBin()) {
        return $value->dump();
    }
    return $value->asText();
}

$context = Value::none();
while (($line = fgets(STDIN)) !== false) {
    $line = rtrim($line, "\n");
    if (trim($line) === '') {
        continue;
    }
    echo 'sel> ', $line, "\n";
    try {
        if ($line === ':reset') {
            $context = Value::none();
        } elseif (str_starts_with($line, ':deps ')) {
            echo implode(' ', Sel::compile(substr($line, 6))->dependencies()), "\n";
        } else {
            echo show(Sel::compile($line)->run($context)), "\n";
        }
    } catch (SelError $e) {
        echo "{$e->code} at {$e->line}:{$e->col}\n";
    }
}
// EXAMPLE-END repl
