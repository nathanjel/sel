<?php
// The SQL layer, opt-in.
//
//     require 'php/src/bootstrap.php';        // the language
//     require 'php/src/Sql/bootstrap.php';    // and its SQL translator
//
// Deliberately not required by the language's own bootstrap. The core is one
// file to require with no dependencies and no cost you did not ask for, and a
// host that never translates should not parse a thousand lines of dialect
// tables to find that out.

declare(strict_types=1);

require_once __DIR__ . '/../bootstrap.php';
require_once __DIR__ . '/SqlError.php';
require_once __DIR__ . '/MapData.php';
require_once __DIR__ . '/Map.php';
require_once __DIR__ . '/Emit.php';
require_once __DIR__ . '/Fragment.php';
require_once __DIR__ . '/Binder.php';
require_once __DIR__ . '/Binding.php';
require_once __DIR__ . '/Bindings.php';
require_once __DIR__ . '/Constants.php';
require_once __DIR__ . '/Normalise.php';
require_once __DIR__ . '/Translator.php';
require_once __DIR__ . '/Sql.php';
