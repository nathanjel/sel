<?php
// No composer, no autoloader, no dependencies — one file to require.
//
// The function table must be complete before any source is parsed, since unknown
// names and bad arity are compile-time errors.

declare(strict_types=1);

require_once __DIR__ . '/SelError.php';
require_once __DIR__ . '/Utf8.php';
require_once __DIR__ . '/Dec.php';
require_once __DIR__ . '/Value.php';
require_once __DIR__ . '/Registry.php';
require_once __DIR__ . '/Lexer.php';
require_once __DIR__ . '/Parser.php';
require_once __DIR__ . '/Context.php';
require_once __DIR__ . '/Args.php';
require_once __DIR__ . '/Evaluator.php';
require_once __DIR__ . '/Sel.php';
require_once __DIR__ . '/Builtins/Core.php';
require_once __DIR__ . '/Builtins/Text.php';
require_once __DIR__ . '/Builtins/Number.php';
require_once __DIR__ . '/Builtins/Binary.php';
require_once __DIR__ . '/Builtins/Regex.php';

\Sel\Builtins\Core::register();
\Sel\Builtins\Text::register();
\Sel\Builtins\Number::register();
\Sel\Builtins\Binary::register();
\Sel\Builtins\Regex::register();
