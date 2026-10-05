<?php
declare(strict_types=1);
// The budget checks below build values at the language's size caps (conformance/29):
// a million-element list is 250-500 MB of PHP objects, over the default 128M limit.
if (ini_get('memory_limit') !== '-1') ini_set('memory_limit', '1G');   // a caller's -d memory_limit=-1 stands
require __DIR__ . '/../php/src/bootstrap.php';
use Sel\Dec;
use Sel\Value;
use Sel\SelError;
$checks = 0;
function verify(bool $ok, string $label): void {
    global $checks;
    $checks++;
    if (!$ok) throw new RuntimeException($label);
}
$pos = ['line'=>3, 'col'=>7, 'offset'=>12];
function errorAt(callable $call, string $code): void {
    global $pos;
    try { $call(); } catch (SelError $e) {
        verify($e->code === $code && $e->line === $pos['line'] &&
            $e->col === $pos['col'] && $e->offset === $pos['offset'], $code);
        return;
    }
    verify(false, 'missing '.$code);
}
$first = Value::text('first');
$ordinary = Value::none();
for ($i=0; $i<10000; $i++) $ordinary->set('k'.$i, $first);
foreach ([$ordinary, Value::list([$first, Value::text('second')]),
          Value::record(['a','b'], [$first, Value::text('second')]),
          Value::list([Value::list([$first])])] as $v) {
    verify($v->scalarSource() === $first, 'first child identity');
}
errorAt(fn()=>Value::null()->asText($pos), 'E_NULL');
errorAt(fn()=>Value::list([])->asText($pos), 'E_NO_SCALAR');
$deep = $first;
for ($i=0; $i<1001; $i++) $deep = Value::list([$deep]);
errorAt(fn()=>$deep->asText($pos), 'E_DEPTH');
foreach ([
    ['add','9223372036854775807','1','9223372036854775808'],
    ['sub','-9223372036854775808','1','-9223372036854775809'],
    ['mul','-9223372036854775808','-1','9223372036854775808'],
    ['mul','9223372036854775807','2','18446744073709551614'],
    ['add','0.01','1.001','1.011'],
    ['mul','-0.00','2','0.00'],
    ['div','1','8','0.125'],
] as [$op,$a,$b,$want]) {
    verify(Dec::format(Dec::$op(Dec::parse($a),Dec::parse($b))) === $want, "$op $a $b");
}
verify(Dec::format(Dec::fromInt(PHP_INT_MIN)) === (string)PHP_INT_MIN, 'native minimum');
$legacy = ['neg'=>false,'digits'=>'123','scale'=>2];
verify(Dec::format(Dec::add($legacy, Dec::parse('1'))) === '2.23', 'legacy descriptor');
$changed = Dec::parse('1.23'); $changed['digits'] = '456';
verify(Dec::format(Dec::add($changed, Dec::parse('1'))) === '5.56', 'mutated digits');
$changed['neg'] = true;
verify(Dec::format(Dec::add($changed, Dec::parse('1'))) === '-3.56', 'mutated sign');
$changed['scale'] = 3;
verify(Dec::format(Dec::add($changed, Dec::parse('1'))) === '0.544', 'mutated scale');
errorAt(fn()=>Dec::div(Dec::fromInt(1),Dec::zero(),$pos), 'E_DIV_ZERO');
errorAt(fn()=>Dec::round(Dec::fromInt(1),Dec::MAX_FRAC_DIGITS+1,$pos), 'E_RANGE');
// Numeric join keys are one key per number whichever path builds them: a
// cached decimal (Value::num) against a text nobody has parsed yet, in a fresh
// process, before and after an equality parses the text, and in both orders.
$join = \Sel\Sel::compile('LINK(L, R, _1["id"] == _2["id"])');
$equal = \Sel\Sel::compile('L[1]["id"] == R[1]["id"]');
foreach ([['1.00','1.00'],['1.50','1.5'],['-0.0','0'],['0','-0'],['7','007'],['0.5','.5'],
          ['123456789012345678901.50','123456789012345678901.5'],
          ['-123456789012345678901','-123456789012345678901.0']] as [$num, $text]) {
    if (Dec::parse($text) === null) continue; // not every spelling above is a SEL number
    foreach ([false, true] as $swap) {
        $ctx = Value::none();
        $a = Value::list([Value::record(['id'], [Value::num($num)])]);
        $b = Value::list([Value::record(['id'], [Value::text($text)])]);
        $ctx->set('L', $swap ? $b : $a);
        $ctx->set('R', $swap ? $a : $b);
        $cold = $join->run($ctx)->size();
        $eq = $equal->run($ctx)->asBool();
        $warm = $join->run($ctx)->size();
        verify($cold === 1 && $eq && $warm === 1, "join key $num/$text cold=$cold warm=$warm");
    }
}

// --- the host boundary (spec/SPEC.md §8, review 2026-09-25) ------------------
// Collected, so one run reports every broken contract.
$boundary = [];
$expect = function (string $name, callable $fn) use (&$boundary): void {
    global $checks; $checks++;
    try { $r = $fn(); if ($r !== true) $boundary[] = "$name: got " . var_export($r, true); }
    catch (\Throwable $e) { $boundary[] = "$name: threw " . ($e instanceof SelError ? $e->code : get_class($e)) . ' ' . $e->getMessage(); }
};
$code = function (callable $fn): string {
    try { $fn(); return 'no error'; } catch (SelError $e) { return $e->code; }
};
$run = fn(string $src, array $ctx = []) => \Sel\Sel::compile($src)->run($ctx);
// HOST-01: a scalar with a child named "_" has no native form.
$expect('toNative refuses a scalar with a child named _', fn() =>
    $code(fn() => $run('A = "s"; A["_"] = "c"; A')->toNative()) === 'E_BAD_ARG');
// HOST-05: every text entering is checked, keys included.
$expect('Value::text rejects malformed UTF-8', fn() => $code(fn() => Value::text("\xFF")) === 'E_UTF8');
$expect('fromNative rejects malformed UTF-8', fn() => $code(fn() => Value::fromNative("\xFF")) === 'E_UTF8');
$expect('fromNative rejects a malformed key', fn() => $code(fn() => Value::fromNative(["\xFF" => 'x'])) === 'E_UTF8');
$expect('Value::set rejects a malformed key', fn() => $code(fn() => Value::none()->set("\xC3", Value::text('x'))) === 'E_UTF8');
// A regex error quotes an excerpt of the pattern; it is cut between code
// points, so the message is UTF-8 however the pattern ends.
$expect('a long non-ASCII regex excerpt stays valid UTF-8', function () {
    try { \Sel\Sel::evaluate('RMATCH("' . str_repeat('é', 90) . '(", "x")'); return 'no error'; }
    catch (SelError $e) { return \Sel\Utf8::firstInvalid($e->getMessage()) === null && json_encode($e->getMessage()) !== false; }
});
$expect('Value raises on a property name other than scalar, read or written', function () {
    $v = Value::text('x');
    try { $v->scaler; return 'read returned'; } catch (\Error) {}
    try { $v->scaler = 'y'; return 'write accepted'; } catch (\Error) {}
    return $v->scalar === 'x' && !isset($v->scaler);
});
$expect('a supplementary character is text', fn() => Value::text("\u{1F600}")->dump() === "t\"\u{1F600}\"");
// HOST-08 / HOST-09: toNative and fromNative are inverses, except the one
// spec/SPEC.md §8 names: a record keyed "0" … "n-1" is a PHP list.
foreach (['FILTER(LIST(1,2,3), _ > 1)', 'RECORD("5","a","9","b")', 'FALSE', 'RECORD("a", FALSE)', 'LIST(TRUE, NULL)',
          'RECORD("1x","a","1y","b")'] as $src) {
    $expect("round trip of $src", function () use ($run, $src) {
        $v = $run($src); return Value::fromNative($v->toNative())->dump() === $v->dump();
    });
}
$expect('a record keyed 0, 1 comes back as a list keyed 1, 2 (the named exception)', fn() =>
    Value::fromNative($run('RECORD("0","a","1","b")')->toNative())->dump() === '-{"1"=t"a", "2"=t"b"}');
// SEM-09: fromEntries keeps keys that only look numeric.
$expect('fromEntries keeps "1x", "2" as keys of a list', fn() =>
    Value::fromEntries([['1x', Value::text('a')], ['2', Value::text('b')]], true)->dump() === '-{"1x"=t"a", "2"=t"b"}');
$expect('fromEntries keeps "01" as the first key', fn() =>
    Value::fromEntries([['01', Value::text('a')]], true)->dump() === '-{"01"=t"a"}');
// HOST-07 control: an over-deep host value cannot be hashed any more than dumped.
$deepValue = function (int $levels): Value { $v = Value::text('x'); for ($i = 0; $i < $levels; $i++) $v = Value::list([$v]); return Value::list([$v]); };
foreach (['COUNT(DEDUPE(A))', 'COUNT(DISTINCT(A))', 'COUNT(BUCKET(A, _, COUNT(_)))'] as $src) {
    $expect("$src over a value nested past the cap", fn() => $code(fn() => $run($src, ['A' => $deepValue(250)])) === 'E_DEPTH');
}
// HOST-10 control: a compiled program keeps nothing from one run to the next.
$expect('a compiled program reads the key of each run', function () {
    $p = \Sel\Sel::compile('A[K]'); $A = Value::fromNative(['x' => '1', 'y' => '2']);
    return $p->run(['A' => $A, 'K' => 'x'])->dump() . $p->run(['A' => $A, 'K' => 'y'])->dump() === 't"1"t"2"';
});
// --- every public constructor (spec/SPEC.md §8, review 2026-09-28 HOST-12..20) --
$one = Value::text('1'); $two = Value::text('2');
// HOST-12: keys given side by side are checked like any other text.
foreach ([
    'shaped' => fn() => Value::shaped(["a\xff"], [$one]),
    'record' => fn() => Value::record(["a\xff"], [$one]),
    'fromEntries' => fn() => Value::fromEntries([["a\xff", $one]]),
    'list with keys' => fn() => Value::list([$one], ["a\xff"]),
    'RecordShape::intern' => fn() => Value::fromShape(\Sel\RecordShape::intern(["a\xff"]), [$one]),
    'the first row of fromNativeRows' => fn() => Value::fromNativeRows([["a\xff" => 1]]),
] as $what => $f) {
    $expect("$what rejects an invalid UTF-8 key", fn() => $code($f) === 'E_UTF8');
}
// HOST-13 / HOST-14: the decimal form is a number within the caps, canonical.
$expect('num of a decimal with 1,000,001 fractional digits is E_RANGE', fn() =>
    $code(fn() => Value::num(['neg' => false, 'digits' => '1', 'scale' => 1000001])) === 'E_RANGE');
$expect('num of a decimal with 1,000,001 integer digits is E_RANGE', fn() =>
    $code(fn() => Value::num(['neg' => false, 'digits' => str_repeat('1', 1000001), 'scale' => 0])) === 'E_RANGE');
$expect('num of a decimal canonicalises -0 and leading zeros', fn() =>
    Value::num(['neg' => true, 'digits' => '000', 'scale' => 0])->dump() . Value::num(['neg' => false, 'digits' => '007', 'scale' => 1])->dump() === 't"0"t"0.7"');
foreach ([['neg' => false, 'digits' => 'x', 'scale' => 0], ['neg' => false, 'digits' => '7', 'scale' => -1],
          ['neg' => false, 'digits' => '', 'scale' => 0], ['digits' => '1', 'scale' => 0], 5] as $bad) {
    $expect('num of the malformed decimal ' . json_encode($bad) . ' is E_BAD_ARG', fn() => $code(fn() => Value::num($bad)) === 'E_BAD_ARG');
}
// HOST-17: keys and values pair up.
foreach ([
    'shaped' => fn() => Value::shaped(['a'], [$one, $two]),
    'record' => fn() => Value::record(['a', 'b'], [$one]),
    'fromShape' => fn() => Value::fromShape(\Sel\RecordShape::intern(['a']), []),
    'list with keys' => fn() => Value::list([$one], ['1', '2']),
] as $what => $f) {
    $expect("$what with counts that differ is E_BAD_ARG", fn() => $code($f) === 'E_BAD_ARG');
}
// HOST-18: a repeated key is RECORD's last write in its first position; a list's is refused.
$expect('shaped keeps a repeated key once', fn() =>
    Value::shaped(['a', 'b', 'a'], [$one, $two, $two])->dump() === '-{"a"=t"2", "b"=t"2"}');
$expect('a list with a repeated key is E_BAD_ARG', fn() => $code(fn() => Value::list([$one, $two], ['5', '5'])) === 'E_BAD_ARG');
$expect('a shape with a repeated key is E_BAD_ARG', fn() => $code(fn() => \Sel\RecordShape::intern(['a', 'a'])) === 'E_BAD_ARG');
// HOST-20: a malformed call is E_BAD_ARG, never the host's own exception.
foreach ([
    'fromNative(1.5)' => fn() => Value::fromNative(1.5),
    'fromNative(an object)' => fn() => Value::fromNative(new \stdClass()),
    'list(["1"])' => fn() => Value::list(['1']),
    'record(["a"], ["1"])' => fn() => Value::record(['a'], ['1']),
] as $what => $f) {
    $expect("$what is E_BAD_ARG", fn() => $code($f) === 'E_BAD_ARG');
}
// SPEC §2: E_UTF8 for invalid source is at the first invalid unit, counted in
// code points of the valid prefix (line, col, offset), LF the only line end.
$utf8At = function (string $src): ?array {
    try { \Sel\Sel::compile($src); } catch (SelError $e) {
        return $e->code === 'E_UTF8' ? [$e->line, $e->col, $e->offset] : [$e->code];
    }
    return null;
};
foreach ([
    'bad byte in a literal' => ["\"a\xffb\"", [1, 3, 2]],
    'bad byte on line two' => ["1 +\n \"a\xffb\"", [2, 4, 7]],
    'bad byte after a multibyte prefix' => ["\"\xc5\x82\xff\"", [1, 3, 2]],
    'bad byte at the very start' => ["\xff", [1, 1, 0]],
    'truncated sequence at end of source' => ["\"\xe2\x82", [1, 2, 1]],
    'overlong encoding' => ["\"\xc0\x80\"", [1, 2, 1]],
    'encoded surrogate' => ["\"\xed\xa0\x80\"", [1, 2, 1]],
    'above U+10FFFF' => ["\"\xf4\x90\x80\x80\"", [1, 2, 1]],
    'CR is not a line end' => ["1 +\r\xff", [1, 5, 4]],
] as $what => [$src, $want]) {
    $expect("E_UTF8 in source: $what", fn() => $utf8At($src) === $want);
}
$expect('valid multibyte source is not E_UTF8', fn() => $utf8At("\"\xc5\x82\"") === null);
// PHP-C43: case folding must not ask the locale (PHP < 8.2 does; composer.json allows 8.1).
$expect('ASCII case helpers ignore the locale', function () {
    $before = setlocale(LC_CTYPE, '0');
    $set = false;
    foreach (['tr_TR.ISO-8859-9', 'tr_TR.iso88599', 'tr_TR.UTF-8', 'tr_TR.utf8', 'tr_TR'] as $loc) {
        if (setlocale(LC_CTYPE, $loc) !== false) { $set = true; break; }
    }
    try {
        $ok = \Sel\Utf8::upper("iabc\xc3\xa9") === "IABC\xc3\xa9"
            && \Sel\Utf8::lower("IABC\xc3\x89") === "iabc\xc3\x89"
            && \Sel\Utf8::casecmp('Id', 'iD') === 0;
        // A program's identifiers still resolve under the Turkish locale.
        $ok = $ok && \Sel\Sel::compile('i = 1; I + 1')->run(Value::none())->scalar === '2';
        return $ok;
    } finally {
        setlocale(LC_CTYPE, $before);
    }
});

// --- T02/T03 (review 2026-09-29): decimal caches, GMP, constructors, ownership -------
// PHP-C1: BOOL values are not shared between programs.
$expect('Value::bool returns a fresh value each time', function () {
    $t = Value::bool(true);
    $t->set('k', Value::text('poison'));
    return Value::bool(true)->size() === 0 && Value::bool(true) !== $t;
});
$expect('a program that writes into TRUE does not change the next program', function () use ($run) {
    // The evaluator gets its BOOL from Value::bool(), so isolation is checked through the API.
    $a = $run('X = TRUE; X["k"] = 1; X');
    $b = $run('COUNT(TRUE)');
    return $b->scalar === '0' && $a->size() === 1;
});
// PHP-C2 and the §3.4 table: collectors copy; a new container is always returned.
$expect('`,` copies what it collects', fn() =>
    $run('X = LIST(RECORD("k",1)); (X, 2)[(X[1]["k"] = 9; 1)]["k"]')->scalar === '1');
$expect('LIST and RECORD copy their arguments', fn() =>
    $run('X = RECORD("k",1); Y = LIST(X); X["k"] = 9; Y[1]["k"]')->scalar === '1'
    && $run('X = RECORD("k",1); Y = RECORD("a", X); X["k"] = 9; Y["a"]["k"]')->scalar === '1');
$expect('MAP, FILTER, SORT, SORT_BY, TOP and BUCKET copy what they collect', function () use ($run) {
    foreach ([
        'MAP(X, _)', 'FILTER(X, TRUE)', 'SORT(X)', 'SORT(X, 1)', 'SORT_DESC(X, 1)', 'SORT_BY(X, 1)',
        'TOP(X, 1)', 'TOP_DESC(X, 1)', 'TOP_BY(X, 1, 1)',
    ] as $agg) {
        if ($run("X = LIST(RECORD(\"k\",1)); $agg" . '[(X[1]["k"] = 9; 1)]["k"]')->scalar !== '1') return $agg;
    }
    return $run('X = LIST(RECORD("k",1)); BUCKET(X, 1)["1"][(X[1]["k"] = 9; 1)]["k"]')->scalar === '1'
        && $run('X = LIST(RECORD("k",1)); BUCKET(X, 1, _)[(X[1]["k"] = 9; 1)][1]["k"]')->scalar === '1';
});
$expect('TAKE and DROP alias their elements but not their container', fn() =>
    $run('X = LIST(RECORD("k",1)); TAKE(X, 1)[(X[1]["k"] = 9; 1)]["k"]')->scalar === '9'
    && $run('X = LIST(1,2,3); Y = TAKE(X, 2); X[1] = 9; Y[1]')->scalar === '1');
// The depth of a stored value is checked from where it is stored.
$expect('assignment checks path plus value depth, at the target', function () use ($code, $run) {
    $deep = str_repeat('[1]', 150);
    $src = "A{$deep} = 7; B" . str_repeat('[1]', 50) . ' = A; 7';
    return $code(fn() => $run($src)) === 'E_DEPTH'
        && $code(fn() => $run("A{$deep} = 7; B" . str_repeat('[1]', 49) . ' = A; 7')) === 'no error';
});
$expect('a constructor reports the depth at the node that built it', function () use ($run) {
    $deep = str_repeat('[1]', 199);
    try { $run("A = 7; A{$deep} = 1; LIST(A); 7"); } catch (SelError $e) { return $e->code === 'E_DEPTH' && $e->line === 1 && $e->col > 1; }
    return 'no error';
});
// PHP-C10: a numeral is canonicalised on the way in.
$expect('Value::num(string) canonicalises', fn() =>
    Value::num('007')->scalar === '7' && Value::num('-0')->scalar === '0' && Value::num('1.50')->scalar === '1.50');
// PHP-C39: a malformed constructor call is E_BAD_ARG, never the host's exception.
$expect('malformed constructor calls are E_BAD_ARG', function () use ($code) {
    $v = Value::text('v');
    foreach ([
        'text(5)' => fn() => Value::text(5),
        'text(null)' => fn() => Value::text(null),
        'text([])' => fn() => Value::text([]),
        'int(1.5)' => fn() => Value::int(1.5),
        'int("7")' => fn() => Value::int('7'),
        'int(true)' => fn() => Value::int(true),
        'int(null)' => fn() => Value::int(null),
        'bool("x")' => fn() => Value::bool('x'),
        'bool(1)' => fn() => Value::bool(1),
        'bin(5)' => fn() => Value::bin(5),
        'bin([65, 300])' => fn() => Value::bin([65, 300]),
        'bin([65, "x"])' => fn() => Value::bin([65, 'x']),
        'list("x")' => fn() => Value::list('x'),
        'list([1])' => fn() => Value::list([1]),
        'record(["a"], "x")' => fn() => Value::record(['a'], 'x'),
        'record("a", [])' => fn() => Value::record('a', []),
        'fromEntries null key' => fn() => Value::fromEntries([[null, $v]]),
        'fromEntries array key' => fn() => Value::fromEntries([[[], $v]]),
    ] as $what => $call) {
        if ($code($call) !== 'E_BAD_ARG') return $what . ' -> ' . $code($call);
    }
    return Value::bin([65, 66])->scalar === 'AB' && Value::int(-3)->scalar === '-3'
        && Value::fromEntries([[5, $v]])->has('5');
});
// PHP-C40: a native cache that no longer describes its descriptor is not trusted.
$expect('Dec::cmp ignores a stale cache after the sign is edited', function () {
    $d = Dec::parse('5');
    $d['neg'] = true;
    return Dec::cmp($d, Dec::parse('3')) === -1 && Dec::cmp(Dec::parse('3'), $d) === 1
        && Dec::cmp($d, Dec::parse('-5')) === 0;
});
$expect('Dec::checked does not trust a forged native cache', function () {
    $d = Dec::parse('5');
    $d['native'] = 7;
    $forged = Dec::checked($d);
    $neg = Dec::parse('5');
    $neg['neg'] = true;
    $edited = Dec::checked($neg);
    return Dec::format(Dec::add($forged, Dec::parse('1'))) === '6'
        && Dec::format(Dec::add($edited, Dec::parse('1'))) === '-4'
        && Dec::format(Dec::add(Dec::checked(Dec::parse('5')), Dec::parse('1'))) === '6';
});
// PHP-C41 and PHP-C17, on both arithmetic paths where the extension exists.
foreach ([true, false] as $gmp) {
    $label = $gmp ? 'with ext-gmp' : 'without ext-gmp';
    $expect("digit strings are base 10, $label", function () use ($gmp) {
        Dec::testHooks(['gmp' => $gmp]);
        try {
            $octal = ['neg' => false, 'digits' => '010', 'scale' => 0];   // un-normalised: never octal
            $hex = ['neg' => false, 'digits' => '0', 'scale' => 0];
            return Dec::format(Dec::add($octal, Dec::parse('1'))) === '11'
                && Dec::format(Dec::mul($octal, ['neg' => false, 'digits' => '010', 'scale' => 0])) === '100'
                && Dec::format(Dec::div(['neg' => false, 'digits' => '0100', 'scale' => 0], Dec::parse('4'))) === '25'
                && Dec::format(Dec::sub($hex, Dec::parse('1'))) === '-1';
        } finally { Dec::testHooks(['gmp' => null]); }
    });
    $expect("limb product carries before an accumulator can overflow, $label", function () use ($gmp) {
        $was = Dec::testHooks()['mulCarryEvery'];
        try {
            foreach ([1, 2, 7] as $every) {
                Dec::testHooks(['mulCarryEvery' => $every]);
                Dec::testHooks(['gmp' => $gmp]);
                $a = str_repeat('9', 2100); $b = str_repeat('9', 2113);
                $got = Dec::format(Dec::mul(Dec::parse($a), Dec::parse($b)));
                // (10^m - 1)(10^n - 1) = 10^(m+n) - 10^m - 10^n + 1, worked by hand:
                // 2099 nines, an 8, 13 nines, 2099 zeros and a 1.
                $want = str_repeat('9', 2099) . '8' . str_repeat('9', 13) . str_repeat('0', 2099) . '1';
                if ($got !== $want) return "every=$every";
            }
            return true;
        } finally { Dec::testHooks(['mulCarryEvery' => $was]); Dec::testHooks(['gmp' => null]); }
    });
}
$expect('Dec test hooks refuse a value that would break arithmetic, and restore', function () {
    $before = Dec::testHooks();
    foreach ([['mulCarryEvery' => 0], ['mulCarryEvery' => 92234], ['karatsubaFrom' => 0], ['fastPaths' => 1], ['nosuch' => true]] as $bad) {
        try { Dec::testHooks($bad); return 'accepted ' . json_encode($bad); } catch (\InvalidArgumentException) {}
    }
    $was = Dec::testHooks(['mulCarryEvery' => 3]);
    Dec::testHooks($was);
    return Dec::testHooks() === $before && (new ReflectionProperty(Dec::class, 'mulCarryEvery'))->isPrivate();
});
$expect('Dec::fitsInt is exact at the native boundary', fn() =>
    Dec::fitsInt('0') && Dec::fitsInt((string) PHP_INT_MAX) && !Dec::fitsInt('9223372036854775808')
    && Dec::fitsInt('999999999999999999') && !Dec::fitsInt('10000000000000000000')
    && Dec::toInt(Dec::parse('9223372036854775808')) === PHP_INT_MAX
    && Dec::toInt(Dec::parse('-9223372036854775807')) === -PHP_INT_MAX);
$expect('the default carry interval keeps a slot under PHP_INT_MAX', fn() =>
    is_int(Dec::testHooks()['mulCarryEvery'] * (10 ** 7 - 1) ** 2 + 10 ** 7) && Dec::testHooks()['mulCarryEvery'] > 0);
// The GMP-vs-plain results agree on random operands.
$expect('ext-gmp and pure-PHP multiplication agree', function () {
    mt_srand(29);
    for ($k = 0; $k < 40; $k++) {
        $a = (string) mt_rand(1, 9) . implode('', array_map(fn() => (string) mt_rand(0, 9), range(1, mt_rand(1, 400))));
        $b = (string) mt_rand(1, 9) . implode('', array_map(fn() => (string) mt_rand(0, 9), range(1, mt_rand(1, 400))));
        Dec::testHooks(['gmp' => true]);  $x = Dec::format(Dec::mul(Dec::parse($a), Dec::parse($b)));
        Dec::testHooks(['gmp' => false]); $y = Dec::format(Dec::mul(Dec::parse($a), Dec::parse($b)));
        Dec::testHooks(['gmp' => null]);
        if ($x !== $y) return "$a * $b";
    }
    return true;
});
// GO-C12 for every host: CEIL/FLOOR carrying past the digit cap fail AT THE CALL.
$expect('CEIL and FLOOR past the digit cap are E_RANGE at 1:1', function () use ($run) {
    foreach (['CEIL(REPEAT("9", 1000000) & ".5")', 'FLOOR("-" & REPEAT("9", 1000000) & ".5")'] as $src) {
        try { $run($src); return 'no error'; }
        catch (SelError $e) { if ($e->code !== 'E_RANGE' || $e->line !== 1 || $e->col !== 1) return "$src -> {$e->code} {$e->line}:{$e->col}"; }
    }
    return true;
});
// PHP-C14: an assignment target's bracket chain was collected with array_unshift
// (every element moves on every step), so 40,000 brackets cost seconds. The
// answer must stay the same -- E_DEPTH at the target -- and the time linear.
$expect('a 40,000-bracket assignment target is E_DEPTH in linear time', function () use ($run) {
    $t0 = microtime(true);
    try { $run('A' . str_repeat('[1]', 40000) . ' = 1'); return 'no error'; }
    catch (SelError $e) { if ($e->code !== 'E_DEPTH' || $e->col !== 119999) return "{$e->code} at {$e->col}"; }
    $dt = microtime(true) - $t0;
    return $dt < 4.0 ? true : sprintf('took %.1fs', $dt);
});
// PHP-C15: a syntax error raised while a very long flat chain is held in a local
// used to free the chain recursively on unwind, and the process died with SIGSEGV
// before the error could be reported. Run in a child process so a crash is a
// failed check rather than the end of this one; the raised memory limit is the
// reviewer's (the default 128M stops at a PHP fatal first).
$expect('an error after a 250,000-operator chain is reported, not a crash', function () {
    $n = 250000;
    $chain = '1' . str_repeat('+1', $n);
    $cases = [
        ['NOSUCH(' . $chain . ')', 'E_UNKNOWN_FUNC'],
        [$chain . ')', 'E_SYNTAX'],
        ['A[' . $chain . ' ', 'E_SYNTAX'],
        ['1 == ' . $chain . ' == 2', 'E_SYNTAX'],
        ['(' . $chain, 'E_SYNTAX'],
        ['1 .> LEFT(' . $chain . ',2,3,4)', 'E_ARITY'],
    ];
    foreach ($cases as [$src, $code]) {
        $file = tempnam(sys_get_temp_dir(), 'sel');
        file_put_contents($file, $src);
        $out = shell_exec(escapeshellarg(PHP_BINARY) . ' -d memory_limit=-1 ' . escapeshellarg(__DIR__ . '/../php/bin/sel') . ' ' . escapeshellarg($file) . ' 2>&1');
        unlink($file);
        if (!is_string($out) || !str_starts_with($out, $code)) return substr($code . ' expected, got: ' . (string) $out, 0, 100);
    }
    return true;
});

// --- T05/T06/T07: relational edges, regex portability, text budgets ----------
// The host-level mechanisms behind conformance/27, 28 and 29; each check names the
// finding it holds.
$val = function (string $src, array $ctx = []) use ($run): string {
    $v = $run($src, $ctx);
    return $v->kind === Value::BOOL ? ($v->getScalar() ? 'true' : 'false') : $v->asText();
};
$dump = fn(string $src, array $ctx = []): string => $run($src, $ctx)->dump();

// PHP-C10: an aggregate visits a SNAPSHOT (spec §7.3). A body that adds a key to
// the record being walked used to read the storage the new key had replaced.
$expect('SORT_BY over a record whose body adds a key does not crash', fn() =>
    $val('R = RECORD("a", 2, "b", 1); COUNT(SORT_BY(R, (R["c"] = 9; _)))') === '2');
$expect('MAP visits the elements it started with (add-key)', fn() =>
    $val('R = RECORD("a", 1, "b", 2); COUNT(MAP(R, (R["z"] = 0; _)))') === '2');
$expect('SUM over a shaped record does not see an overwrite of a later field', fn() =>
    $val('R = RECORD("a", 1, "b", 2); SUM(R, x, (R["b"] = 99; x))') === '3');
// PHP-C24: `_K` of an element whose record key is the empty string is "".
$expect('_K is the empty key, not a counter', fn() =>
    $val('COUNT(FILTER(RECORD("", 5, "x", 6), _K $== ""))') === '1');
// PHP-C25/C46: a scalar source is a one-element list; two-argument BUCKET groups by key TEXT.
$expect('BUCKET of a scalar is one group', fn() => $val('COUNT(BUCKET("abc", _))') === '1');
$expect('bare BUCKET keeps every row of a text key that carries children',
    fn() => $val('A = "x"; A["k"] = 1; COUNT(BUCKET(LIST(A, "x", A), _)["x"])') === '3');
// PHP-C22: one total order, ranked before compared.
$expect('numeric text sorts before other text, by value', fn() =>
    $val('LIST("10", "1a", "9", "b") .> SORT() .> JOIN(",")') === '9,10,1a,b');
$expect('descending keeps ties in input order', fn() =>
    $val('LIST("b", "a") .> SORT_BY(1, "DESC") .> JOIN(",")') === 'b,a');
$expect('compareValues is antisymmetric across ranks', function () {
    $vs = [Value::null(), Value::bool(false), Value::bool(true), Value::text('9'), Value::text('10'),
        Value::text('1a'), Value::text(''), Value::bin('a')];
    foreach ($vs as $a) foreach ($vs as $b) {
        if (\Sel\Builtins\Core::compareValues($a, $b) !== -\Sel\Builtins\Core::compareValues($b, $a)) return 'asymmetric';
    }
    return true;
});
// Direction and count are arguments like any other (spec §7.4).
$expect('a bad direction on a NULL source is refused', fn() => $code(fn() => $run('SORT_BY(NULL, _, "UP")')) === 'E_BAD_ARG');
$expect('a bad direction on an empty list is refused', fn() => $code(fn() => $run('SORT_BY(LIST(), _ + 0, "X")')) === 'E_BAD_ARG');
$expect('TOP_BY checks its direction before the count returns empty', fn() => $code(fn() => $run('TOP_BY(LIST(1), _, "UP", 0)')) === 'E_BAD_ARG');
// Join: the digit cap is enforced in the equi-join fast path (PY-C12 / CPP-C22).
$expect('an equi-join key over the digit cap is E_RANGE', fn() =>
    $code(fn() => $run('BIG = PADL("9", 1000005, "9"); LINK(LIST(RECORD("a", "1")), LIST(RECORD("b", BIG)), _1["a"] == _2["b"]) .> COUNT()')) === 'E_RANGE');
// Same-named binders: the right shadows the left, and the fast path must not split the comparison.
$expect('a self join with one binder name reads only the right element', fn() =>
    $val('P = LIST(RECORD("k", 1), RECORD("k", 2)); Q = LIST(RECORD("k", 1), RECORD("k", 3)); COUNT(LINK(P, Q, x, x, x["k"] == x["k"]))') === '4'
    && $val('T = LIST(RECORD("id", 1, "mgr", 2), RECORD("id", 2, "mgr", 1)); COUNT(LINK(T, T, T["id"] == T["mgr"]))') === '0');
// Regex: the validator is a parser (spec §7.8).
$portable = fn(string $p): string => \Sel\Builtins\Regex::portableSource($p);
$expect('the portable source of \d+ and classes is unchanged', fn() =>
    $portable('^\d+[a-c\s]?(?:x|y){2,3}?$') === '^[0-9]+[a-c \t\n\r\f\x0b]?(?:x|y){2,3}?$');
$expect('a nullable loop body is refused', fn() =>
    $code(fn() => $portable('(a*)*')) === 'E_REGEX_SYNTAX' && $code(fn() => $portable('(?:a?)+')) === 'E_REGEX_SYNTAX');
$expect('a quantified group that is not a loop is fine', fn() => $portable('(\d*)?') === '([0-9]*)?');
$expect('a capture that need not take part in a loop is refused', fn() =>
    $code(fn() => $portable('(?:(a)|b)*')) === 'E_REGEX_SYNTAX' && $portable('(a|b)+') === '(a|b)+');
$expect('an anchor cannot be quantified', fn() => $code(fn() => $portable('^*')) === 'E_REGEX_SYNTAX' && $code(fn() => $portable('a$?')) === 'E_REGEX_SYNTAX');
$expect('PCRE verbs are refused', fn() => $code(fn() => $portable('(*FAIL)')) === 'E_REGEX_SYNTAX');
$expect('a class escape as a range endpoint is refused, a hyphen at the edge is not', fn() =>
    $code(fn() => $portable('[\d-z]')) === 'E_REGEX_SYNTAX' && $portable('[\d-]') === '[0-9-]');
$expect('POSIX bracket forms are refused wherever they stand', fn() =>
    $code(fn() => $portable('[a[:digit:]')) === 'E_REGEX_SYNTAX' && $code(fn() => $portable('[a[.x.]]')) === 'E_REGEX_SYNTAX'
    && $code(fn() => $portable('[[.]')) === 'E_REGEX_SYNTAX' && $code(fn() => $portable('[a[=]')) === 'E_REGEX_SYNTAX'
    && $portable('[.[]') === '[.[]');
$expect('group depth over 200, group count over 1000 and pattern length over 65535 are refused', fn() =>
    $code(fn() => $portable(str_repeat('(?:', 201) . 'a' . str_repeat(')', 201))) === 'E_REGEX_SYNTAX'
    && $portable(str_repeat('(?:', 200) . 'a' . str_repeat(')', 200)) !== ''
    && $code(fn() => $portable(str_repeat('(a)', 1001))) === 'E_REGEX_SYNTAX'
    && $code(fn() => $portable(str_repeat('a', 65536))) === 'E_REGEX_SYNTAX');
$expect('a group nested 50000 deep is refused without recursing to the end', fn() =>
    $code(fn() => $portable(str_repeat('(?:', 50000) . 'a' . str_repeat(')', 50000))) === 'E_REGEX_SYNTAX');
$expect('only the flag i is a flag, compared exactly', fn() =>
    $code(fn() => $run('RMATCH("a", "A", "I")')) === 'E_BAD_ARG' && $val('RMATCH("a", "A", "i")') === 'true');
$expect('a literal pattern is refused when the program compiles, in a dead branch too', fn() =>
    $code(fn() => \Sel\Sel::compile('IF(FALSE, RMATCH("(?=a)", "a"), 1)')) === 'E_REGEX_SYNTAX'
    && $val('IF(FALSE, RMATCH("(?=" & "a)", "a"), 1)') === '1');
// PHP-C3: PCRE resource failures are never "no match".
$expect('a 100,000-character subject through an alternation loop matches', fn() =>
    $val('RMATCH("^(?:a|b)*$", REPEAT("a", 100000))') === 'true'
    && $val('RFIND("(?:a|b)*c", REPEAT("a", 100000) & "c")') === '1'
    && $val('LEN(RREPLACE("^(?:a|b)*$", "X", REPEAT("a", 100000)))') === '1');
$expect('the ini settings the retry changes are restored', function () use ($val) {
    $before = [ini_get('pcre.jit'), ini_get('pcre.backtrack_limit'), ini_get('pcre.recursion_limit')];
    $val('RMATCH("^(?:a|b)*$", REPEAT("a", 100000))');
    return $before === [ini_get('pcre.jit'), ini_get('pcre.backtrack_limit'), ini_get('pcre.recursion_limit')];
});
// PCRE's compiled program is 64 KB: repeated literals fold, counted single-char groups unwrap.
$expect('a 65,533-character pattern of one letter compiles', fn() =>
    $val('RMATCH("^" & REPEAT("a", 65533) & "$", REPEAT("a", 65533))') === 'true');
$expect('a counted capture of one character compiles, and captures the last one', fn() =>
    $val('RMATCH(\'^(a){20000}$\', REPEAT("a", 20000))') === 'true'
    && $dump('RGROUPS(\'^(.){3000}$\', REPEAT("a", 2999) & "z")') === '-{"1"=t"' . str_repeat('a', 2999) . 'z", "2"=t"z"}');
$expect('a pattern PCRE cannot hold has an answer when the subject is too short', fn() =>
    $val('RMATCH(\'(?:a{60000}){60000}\', "a")') === 'false'
    && $val('RFIND(\'(?:a{60000}){60000}\', "a")') === '0'
    && $val('RREPLACE(\'(?:a{60000}){60000}\', "-", "abc")') === 'abc');
$expect('the pattern cache is bounded', function () use ($val) {
    for ($i = 0; $i < 600; $i++) $val('RMATCH(\'x{' . ($i + 1) . '}\', "y")');
    $cache = (new ReflectionProperty(\Sel\Builtins\Regex::class, 'cache'))->getValue();
    return count($cache) <= 256 ? true : count($cache);
});
// P3: RREPLACE's scan is spec §7.8's, not preg_match_all's.
$expect('RREPLACE resumes one code point after an empty match', fn() =>
    $val('RREPLACE("b*?", "-", "abb")') === '-a-b-b-'
    && $val('RREPLACE("a*", "-", "baac")') === '-b--c-'
    && $val('RREPLACE("x*", "-", "é😀")') === '-é-😀-');
// Text: bytes underneath, code points on top.
$expect('LEFT/RIGHT/SUBSTR/FIND count code points on multibyte text', fn() =>
    $val('LEFT("a😀é", 2)') === 'a😀' && $val('RIGHT("a😀é", 2)') === '😀é'
    && $val('SUBSTR("a😀é", 2, 1)') === '😀' && $val('FIND("é", "a😀é")') === '3'
    && $val('FIND("é", "aé😀é", 3)') === '4');
$expect('BACKWARDS reverses code points, not bytes', fn() => $val('BACKWARDS("a😀éz")') === 'zé😀a');
$expect('a pad is cut at a code point', fn() => $val('PADL("😀", 3, "é😀")') === 'é😀😀');
$expect('counts past the machine integer saturate', fn() =>
    $val('LEFT("abc", 1 & REPEAT("0", 400))') === 'abc' && $val('RIGHT("abc", 1 & REPEAT("0", 400))') === 'abc'
    && $val('SUBSTR("abc", 1 & REPEAT("0", 400))') === '' && $val('FIND("b", "abc", 1 & REPEAT("0", 400))') === '0');
$expect('Utf8::length agrees with the split length', function () {
    foreach (['', 'abc', 'é', '😀a', str_repeat('é', 40) . 'x', str_repeat('😀', 100)] as $s) {
        if (\Sel\Utf8::length($s) !== count(\Sel\Utf8::chars($s))) return $s;
    }
    return true;
});
// Budgets (spec §6.4): refused at the call, before allocating.
$expect('REPEAT past the text cap is E_RANGE at the call, an empty repeat is empty', fn() =>
    $code(fn() => $run('REPEAT("a", 16777217)')) === 'E_RANGE'
    && $code(fn() => $run('REPEAT("ab", 99999999999999999999)')) === 'E_RANGE'
    && $val('REPEAT("", 99999999999999999999)') === ''
    && $val('LEN(REPEAT("a", 16777216))') === '16777216');
$expect('PAD, concatenation and JOIN past the cap are E_RANGE', fn() =>
    $code(fn() => $run('PADL("a", 16777217, "x")')) === 'E_RANGE'
    && $code(fn() => $run('REPEAT("a", 16777216) & "b"')) === 'E_RANGE'
    && $code(fn() => $run('JOIN(SPLIT(REPEAT("a,", 100) & "a", ","), REPEAT("b", 10000000))')) === 'E_RANGE');
$expect('SPLIT/BTL/list flattening past the collection cap are E_RANGE', fn() =>
    $code(fn() => $run('COUNT(SPLIT(REPEAT("a,", 1000000) & "a", ","))')) === 'E_RANGE'
    && $code(fn() => $run('COUNT(BTL(TO_UTF8(REPEAT("a", 1000001))))')) === 'E_RANGE');
$expect('TO_HEX and ENCODE_BASE64 past the cap are E_RANGE', fn() =>
    $code(fn() => $run('TO_HEX(TO_UTF8(REPEAT("a", 8388609)))')) === 'E_RANGE'
    && $code(fn() => $run('ENCODE_BASE64(TO_UTF8(REPEAT("a", 12582915)))')) === 'E_RANGE'
    && $val('ENCODE_BASE64(TO_UTF8("é"))') === 'w6k=');
$expect('REPLACE and RREPLACE past the cap are E_RANGE', fn() =>
    $code(fn() => $run('REPLACE("a", REPEAT("b", 200000), REPEAT("a", 200))')) === 'E_RANGE'
    && $code(fn() => $run('RREPLACE("a", REPEAT("b", 40), REPEAT("a", 500000))')) === 'E_RANGE');
$expect('LINK past the row cap is E_RANGE', fn() =>
    $code(fn() => $run('A = SPLIT(REPEAT("a,", 1000) & "a", ","); B = SPLIT(REPEAT("b,", 999) & "b", ","); COUNT(LINK(A, B, TRUE))')) === 'E_RANGE');
$expect('LTB: empty list, integral scaled bytes, fractional and out of range', fn() =>
    $val('BLEN(LTB(LIST()))') === '0' && $val('TO_HEX(LTB(LIST(65, 1.0)))') === '4101'
    && $code(fn() => $run('LTB(LIST(1.5))')) === 'E_NOT_INT' && $code(fn() => $run('LTB(LIST(256.0))')) === 'E_RANGE');
// --- P5: exponential ambiguity (spec §7.8), against the reference verdicts -----
$verdict = static function (string $pattern, bool $ic = false): string {
    try {
        \Sel\Builtins\Regex::validate($pattern, null, $ic);
        return 'A';
    } catch (\Sel\SelError $e) {
        return $e->code === 'E_REGEX_SYNTAX' ? 'R' : $e->code;
    }
};
$expect('exponentially ambiguous patterns are refused', function () use ($verdict) {
    foreach (['(a+)+$', '(a|aa)+$', '(a|b|ab)*c', '(.+)+x', '([a-z]+)*$', '(\w+\s*)*$', '(?:\d|\d\d)+$',
              '(?:a|a)*$', '(a+){2,}$', '^(?:a|b|ab)+$', '(?:a{1,20}){1,20}b'] as $p) {
        if ($verdict($p) !== 'R') { fwrite(STDERR, "not refused: $p\n"); return false; }
    }
    return true;
});
$expect('ordinary patterns, polynomial ambiguity and huge counts are accepted', function () use ($verdict) {
    foreach (['(\d+,)+', '(?:ab|cd)*', '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$', '(\w+\s)*',
              'a*a*$', '^(a{300}){300}$', '(?:a{60000}){60000}', '^(?:ab){65535}$', '^[a-z]{1,65535}$', ''] as $p) {
        if ($verdict($p) !== 'A') { fwrite(STDERR, "refused: $p\n"); return false; }
    }
    return true;
});
$expect('the i flag reaches the analysis, and the budget has its boundary', fn() =>
    $verdict('(?:a|A)+$') === 'A' && $verdict('(?:a|A)+$', true) === 'R'
    && $verdict(str_repeat('(a|a)', 8) . 'x') === 'A' && $verdict(str_repeat('(a|a)', 9) . 'x') === 'R'
    && $verdict('\W+k$', true) === 'A');
$expect('an exact count of a group PCRE cannot hold matches, with the pattern\'s own groups only', fn() =>
    $val('RMATCH(\'^(?:ab){65535}$\', REPEAT("ab", 65535))') === 'true'
    && $val('RMATCH(\'^(?:ab){65535}$\', REPEAT("ab", 65534))') === 'false'
    && $val('COUNT(RGROUPS(\'^(x)(?:ab){40000}(y)$\', "x" & REPEAT("ab", 40000) & "y"))') === '3'
    && $val('RREPLACE(\'^(x)(?:ab){40000}$\', "[$1]", "x" & REPEAT("ab", 40000))') === '[x]');


// --- SQL layer (T08-T11): API-level contracts the shared .sqlt cases cannot state ---
require_once __DIR__ . '/../php/src/Sql/bootstrap.php';
$sqlRefused = function (callable $f, string $code): bool {
    try { $f(); } catch (\Sel\Sql\SqlError $e) { return $e->code === $code; }
    return false;
};
$ords = ['ORDERS' => \Sel\Sql\Binding::relation('orders', 'o', [
    'ID' => \Sel\Sql\Binding::column('id', 'o', 'NUM'), 'NAME' => \Sel\Sql\Binding::column('name', 'o', 'TEXT')])];
$sqlStmt = fn (string $src, string $d = 'mariadb') => \Sel\Sql\Sql::translateStatement(\Sel\Sel::compile($src), $d, $ords)->asStatement();
$expect('two bindings, or two fields, that differ only by ASCII case are refused', fn() =>
    $sqlRefused(fn() => new \Sel\Sql\Bindings(['x' => \Sel\Sql\Binding::column('a', 't', 'NUM'), 'X' => \Sel\Sql\Binding::column('b', 't', 'NUM')]), 'E_SQL_BINDING')
    && $sqlRefused(fn() => \Sel\Sql\Binding::relation('t', 't', ['A' => \Sel\Sql\Binding::column('x', 't', 'NUM'), 'a' => \Sel\Sql\Binding::column('y', 't', 'NUM')]), 'E_SQL_BINDING'));
$expect('a column cannot be typed LIST, STATEMENT or a non-string, and the message does not warn', function () use ($sqlRefused) {
    foreach (['LIST', 'STATEMENT', ['NUM']] as $t) {
        if (!$sqlRefused(fn() => \Sel\Sql\Binding::column('a', 't', $t), 'E_SQL_BINDING')) return false;
    }
    return true;
});
$expect('an unknown render mode is refused on a fragment with no slot', function () use ($ords) {
    $f = \Sel\Sql\Sql::translate(\Sel\Sel::compile('1 + 1 == 2'), 'mariadb', []);
    try { $f->asValue('bogus'); } catch (\InvalidArgumentException) { return true; }
    return false;
});
$expect('dialect registration: quote pairing, same parent replaces, another parent is refused', function () {
    $logic = function (callable $f): bool { try { $f(); } catch (\LogicException) { return true; } return false; };
    $bad = [['extends' => 'ansi', 'version' => '1', 'lexical' => ['textQuote' => '"']],
            ['extends' => 'sqlite', 'version' => '3.48', 'lexical' => ['textEscape' => []]],
            ['extends' => 'mariadb', 'version' => '10.5', 'lexical' => ['textEscape' => ["'" => "\\'"]]]];
    foreach ($bad as $i => $spec) {
        if (!$logic(fn() => \Sel\Sql\Map::defineDialect("chk-bad-$i", $spec))) return false;
        if (\Sel\Sql\Map::exists("chk-bad-$i")) return false;           // nothing half-registered
    }
    \Sel\Sql\Map::defineDialect('chk-redef', ['extends' => 'mariadb', 'version' => '10.5']);
    \Sel\Sql\Map::defineDialect('chk-redef', ['extends' => 'mariadb', 'version' => '10.6']);
    return \Sel\Sql\Map::version('chk-redef') === '10.6'
        && $logic(fn() => \Sel\Sql\Map::defineDialect('chk-redef', ['extends' => 'postgresql', 'version' => '16']));
});
$expect('a numeric guard that disagrees with ISNUM is refused on EVERY use, not the first', function () use ($sqlRefused) {
    \Sel\Sql\Map::defineDialect('chk-guard', ['extends' => 'postgresql', 'version' => '16', 'lexical' => [
        'numericGuard' => "CASE WHEN ({textCast:0} ~ '^.*$') THEN CAST({0} AS NUMERIC) ELSE NULL END"]]);
    $named = ['N' => \Sel\Sql\Binding::column('n', 't', 'TEXT')];
    for ($i = 0; $i < 3; $i++) {
        try { \Sel\Sql\Sql::translate(\Sel\Sel::compile('N + 1'), 'chk-guard', $named); return false; }
        catch (\LogicException) {}
    }
    return true;
});
$expect('TAKE/DROP counts: a whole number with a scale is a count, past int64 is clamped, offsets add exactly', fn() =>
    str_contains($sqlStmt('ORDERS .> TAKE(2.0)'), 'LIMIT 2')
    && str_contains($sqlStmt('ORDERS .> TAKE(99999999999999999999999)'), 'LIMIT 9223372036854775807')
    && str_contains($sqlStmt('ORDERS .> DROP(9223372036854775806) .> DROP(1) .> TAKE(1)'), 'OFFSET 9223372036854775807')
    && $sqlRefused(fn() => $sqlStmt('ORDERS .> TAKE(1.5)'), 'E_NOT_INT'));
$expect('the size budget refuses a doubling helper chain quickly, with no position', function () use ($sqlRefused) {
    $prog = 'X0 = N;' . implode('', array_map(fn ($k) => "X$k = X" . ($k - 1) . " + X" . ($k - 1) . ';', range(1, 30))) . 'X30 > 0';
    $t = microtime(true);
    try {
        \Sel\Sql\Sql::translate(\Sel\Sel::compile($prog), 'mariadb', ['N' => \Sel\Sql\Binding::column('n', 't', 'NUM')]);
        return false;
    } catch (\Sel\Sql\SqlError $e) {
        return $e->code === 'E_SQL_SIZE' && $e->line === 0 && microtime(true) - $t < 5.0;
    }
});
$expect('an unrolled list above 256 operands folds as a balanced tree', function () {
    $list = implode(',', array_fill(0, 1100, 'N'));
    $sql = \Sel\Sql\Sql::translate(\Sel\Sel::compile("ANY(($list), _ > 0)"), 'mariadb',
        ['N' => \Sel\Sql\Binding::column('n', 't', 'NUM')])->asValue();
    $depth = 0; $max = 0;
    foreach (str_split($sql) as $c) { if ($c === '(') $max = max($max, ++$depth); elseif ($c === ')') $depth--; }
    return $max < 300;            // a left fold of 1100 is 1100 deep; halves down to 256 are about 140
});
$expect('a hybrid plan never mutates the caller context, and reads no table for a name the program shadows', function () {
    $b = ['ORDERS' => \Sel\Sql\Binding::relation('orders', 'o', ['ID' => \Sel\Sql\Binding::column('id', 'o', 'NUM')])];
    $plan = \Sel\Sql\Sql::planHybrid(\Sel\Sel::compile('S = 1; A = LIST(1, 2); COUNT(A)'), 'mariadb', $b);
    $ctx = Value::fromNative(['K' => 5]);
    \Sel\Sql\Hybrid::execute($plan, fn() => [], $ctx);
    $shadow = \Sel\Sql\Sql::planHybrid(\Sel\Sel::compile('ORDERS = LIST(1); COUNT(ORDERS)'), 'mariadb', $b);
    $binder = \Sel\Sql\Sql::planHybrid(\Sel\Sel::compile('MAP(LIST(1, 2), ORDERS, ORDERS + 1)'), 'mariadb', $b);
    return $ctx->keys() === ['K'] && $shadow->sourceTables === [] && $binder->sourceTables === [];
});

$expect('an integer Value builds its decimal lazily', function () {
    $v = Value::int(7);
    return $v->decVal === null && Dec::format($v->asDecimal()) === '7';
});
$expect('records sort by scalar context and tied records keep input order (SPEC 3.2 / 7.3)', function () {
    $rows = 'LIST(RECORD("k", 3, "v", "c"), RECORD("k", 1, "v", "a"), RECORD("k", 2, "v", "b"))';
    $ties = 'LIST(RECORD("k", 1, "v", "a"), RECORD("k", 2, "v", "b"), RECORD("k", 1.0, "v", "c"), RECORD("k", "1", "v", "d"))';
    $j = fn(string $e): string => \Sel\Sel::evaluate('JOIN(MAP(' . $e . ', _["v"]), ",")')->scalar;
    return $j($rows . ' .> SORT()') === 'a,b,c'
        && $j($rows . ' .> SORT_DESC()') === 'c,b,a'
        && $j($ties . ' .> SORT()') === 'a,c,d,b'
        && $j($ties . ' .> SORT_DESC()') === 'b,a,c,d'
        && $j($ties . ' .> TOP_DESC(4)') === 'b,a,c,d';
});
// --- T12: flow-sensitive dependencies(), E_BAD_ARG at the host boundary (SPEC §8, §8.1).
$deps = fn(string $src): string => implode(' ', \Sel\Sel::compile($src)->dependencies());
$expect('dependencies(): a read before the definite assignment is a dependency', fn() =>
    $deps('A + 1; A = 2') === 'A' && $deps('A = 1; A + B') === 'B' && $deps('A = A + 1') === 'A');
$expect('dependencies(): op= and A[k] op= read their target, A[k] = x creates it', fn() =>
    $deps('X += 1') === 'X' && $deps('A[1] += 1') === 'A' && $deps('A[1] = 2') === '' && $deps('A[1] = 2; A') === '');
$expect('dependencies(): conditional, short-circuit and aggregate assignments are not definite', fn() =>
    $deps('IF(X, A = 1, 0); A') === 'A X' && $deps('IF(X, A = 1, A = 2); A') === 'X'
    && $deps('X AND (A = 1); A') === 'A X' && $deps('X ?? (A = 1); A') === 'A X'
    && $deps('MAP(L, A = _); A') === 'A L' && $deps('COND(X, A = 1, Y, A = 2, A = 3); A') === 'X Y'
    && $deps('COALESCE(X, A = 1); A') === 'A X'
    && $deps('GET(T, "k", A = 1); A') === 'A T');
$expect('dependencies(): an assignment in an argument is definite for what follows', fn() =>
    $deps('LEFT("abc", (N = 2)); N') === '');
$expect('dependencies() keeps its E_DEPTH cap on a long flat chain', function () {
    try { \Sel\Sel::compile('A' . str_repeat('+A', 5000))->dependencies(); } catch (SelError $e) { return $e->code === 'E_DEPTH'; }
    return false;
});
$expect('compile() of a non-string is E_BAD_ARG, not a TypeError', function () {
    foreach ([12, null, ['1'], 1.5, new stdClass] as $bad) {
        try { \Sel\Sel::compile($bad); return false; } catch (SelError $e) { if ($e->code !== 'E_BAD_ARG') return false; }
    }
    return true;
});
$expect('reading an argument a host function was not given is E_BAD_ARG', function () {
    \Sel\Sel::registerFunction('T12_OOB', 1, 2, static fn (\Sel\Args $a): Value => Value::text($a->text(5)));
    foreach (['T12_OOB("x")', 'T12_OOB("x", "y")'] as $src) {
        try { \Sel\Sel::evaluate($src); return false; } catch (SelError $e) { if ($e->code !== 'E_BAD_ARG') return false; }
    }
    return true;
});
// --- performance wave (PHP-P1..P11): each rewritten hot path against a plain reference ----------------------------
$cpList = static function (string $s): array {
    $o = []; $i = 0; $n = strlen($s);
    while ($i < $n) { $b = ord($s[$i]); $l = $b < 0x80 ? 1 : ($b < 0xe0 ? 2 : ($b < 0xf0 ? 3 : 4)); $o[] = substr($s, $i, $l); $i += $l; }
    return $o;
};
$expect('P1: Utf8::length / retreat / BACKWARDS / RIGHT / LEN agree with a code-point walk on random valid UTF-8', function () use ($cpList) {
    mt_srand(7);
    $pool = ['a', 'Z', ' ', 'é', 'ß', '漢', '😀', "\u{10FFFF}", "\u{7FF}", "\u{800}", "\u{FFFF}", "\u{10000}", "\0"];
    for ($t = 0; $t < 4000; $t++) {
        $s = ''; for ($i = mt_rand(0, 30); $i > 0; $i--) $s .= $pool[mt_rand(0, count($pool) - 1)];
        $cps = $cpList($s); $k = mt_rand(0, 34);
        if (\Sel\Utf8::length($s) !== count($cps)) return false;
        if (substr($s, \Sel\Utf8::retreat($s, $k)) !== implode('', array_slice($cps, max(0, count($cps) - $k)))) return false;
        $ev = static fn (string $src) => \Sel\Sel::evaluate($src, ['S' => $s, 'K' => $k])->asText();
        if ($ev('BACKWARDS(S)') !== implode('', array_reverse($cps))) return false;
        if ($ev('RIGHT(S, K)') !== implode('', array_slice($cps, max(0, count($cps) - $k)))) return false;
        if ($ev('LEN(S)') !== (string) count($cps)) return false;
    }
    return true;
});
$expect('P1: the preg fallback of BACKWARDS (no mbstring) agrees with the UTF-32 path', function () use ($cpList) {
    $m = new ReflectionMethod(\Sel\Builtins\Text::class, 'reverseCodePoints');
    $m->setAccessible(true);
    $fallback = static fn (string $s): string => preg_replace(
        ['/([\x80-\xbf])([\xc0-\xdf])/', '/([\x80-\xbf])([\x80-\xbf])([\xe0-\xef])/', '/([\x80-\xbf])([\x80-\xbf])([\x80-\xbf])([\xf0-\xf7])/'],
        ['$2$1', '$3$2$1', '$4$3$2$1'], strrev($s));
    foreach (['aé漢😀', '😀😀a', 'é', "x\u{10FFFF}\u{7FF}y\u{800}"] as $s) {
        if ($m->invoke(null, $s) !== implode('', array_reverse($cpList($s)))) return false;
        if ($fallback($s) !== implode('', array_reverse($cpList($s)))) return false;
    }
    return true;
});
$expect('P3: sort keys give the same order as compareValues, and looksNumeric of non-numeric text builds no error', function () {
    $vals = [Value::null(), Value::bool(true), Value::bool(false), Value::text('10'), Value::text('9'), Value::text('1a'), Value::text(''),
        Value::text(' 2'), Value::text('-0'), Value::text('1e3'), Value::text('007'), Value::bin("\x01"), Value::text('abc'), Value::text('1.50'), Value::text('1.5')];
    foreach ($vals as $x) foreach ($vals as $y) {
        if (\Sel\Builtins\Core::compareKeys(\Sel\Builtins\Core::sortKey($x), \Sel\Builtins\Core::sortKey($y))
            !== \Sel\Builtins\Core::compareValues($x, $y)) return false;
    }
    $v = Value::text('not a number');
    return $v->looksNumeric() === false && Value::text('12')->looksNumeric() === true && Value::text('1' . str_repeat('0', 1000001))->looksNumeric() === false;
});
$expect('P4/P5: limb division and Karatsuba multiplication agree with GMP (forced off/on), add-back cases included', function () {
    mt_srand(5);
    $limbs = [0, 1, 2, 9999999, 9999998, 5000000, 4999999, 5000001, 1234567, 7654321];
    $num = static function (int $n) use ($limbs): string { $s = ''; for ($i = 0; $i < $n; $i++) { $v = $limbs[mt_rand(0, 9)]; if ($i === 0 && $v === 0) $v = 1; $s .= $i === 0 ? (string) $v : sprintf('%07d', $v); } return $s; };
    $from = Dec::testHooks()['karatsubaFrom'];
    try {
        foreach ([2, 3, 40] as $k) {
            Dec::testHooks(['karatsubaFrom' => $k]);
            for ($t = 0; $t < 400; $t++) {
                $nb = mt_rand(2, 14); $na = mt_rand($nb, $nb + 14);
                $a = $num($na); $b = $num($nb);
                foreach (['div', 'mod', 'mul'] as $op) {
                    Dec::testHooks(['gmp' => null]); $g = Dec::format(Dec::$op(Dec::parse($a), Dec::parse($b)));
                    Dec::testHooks(['gmp' => false]); $p = Dec::format(Dec::$op(Dec::parse($a), Dec::parse($b)));
                    if ($g !== $p) return false;
                }
            }
        }
    } finally { Dec::testHooks(['karatsubaFrom' => $from]); Dec::testHooks(['gmp' => null]); }
    return true;
});
$expect('P6: LINK with an outside variable in the key uses the hash path and answers as the nested loop does', function () {
    $rows = static fn (int $n, int $d): array => array_map(static fn ($i) => ['id' => $i + $d], range(0, $n - 1));
    $ctx = ['L' => $rows(40, 0), 'R' => $rows(40, 1), 'K' => 1];
    $fast = \Sel\Sel::evaluate('COUNT(LINK(L, R, A, B, A["id"] + K == B["id"]))', $ctx)->asText();
    $slow = \Sel\Sel::evaluate('COUNT(LINK(L, R, A, B, A["id"] + K == B["id"] AND TRUE))', $ctx)->asText();
    if ($fast !== $slow || $fast !== '40') return false;
    // an assignment to the outside name inside a key keeps the general path (and its answer)
    $assigned = \Sel\Sel::evaluate('COUNT(LINK(L, R, A, B, (K = K + 1; A["id"] + K) == B["id"]))', $ctx)->asText();
    $assignedSlow = \Sel\Sel::evaluate('COUNT(LINK(L, R, A, B, (K = K + 1; A["id"] + K) == B["id"] AND TRUE))', $ctx)->asText();
    // empty side: the key is never evaluated, so an undefined name raises nothing
    $empty = \Sel\Sel::evaluate('COUNT(LINK(L, LIST(), A, B, A["id"] + NOPE == B["id"]))', $ctx)->asText();
    try { \Sel\Sel::evaluate('COUNT(LINK(L, R, A, B, A["id"] + NOPE == B["id"]))', $ctx); return false; }
    catch (SelError $e) { if ($e->code !== 'E_UNDEF_VAR') return false; }
    return $assigned === $assignedSlow && $empty === '0';
});
$expect('P11: textTrusted is internal; host input still goes through the UTF-8 check', function () {
    try { Value::text("a\xffb"); return false; } catch (SelError $e) { if ($e->code !== 'E_UTF8') return false; }
    return Value::textTrusted('abc')->asText() === 'abc'
        && \Sel\Sel::evaluate('UPPER(SUBSTR("héllo", 2, 3)) & LEFT("😀x", 1)')->asText() === "éLL😀";
});
$expect('P9: the lexer rejects invalid UTF-8 at the same position as before the PCRE fast path', function () {
    try { \Sel\Lexer::tokenizeSource("1 +\n \"a\xffb\""); return false; }
    catch (SelError $e) { return $e->code === 'E_UTF8' && $e->line === 2 && $e->col === 4 && $e->offset === 7; }
});

// ---- performance wave, round 2 (PHP-P12 .. P21): each optimisation is held to the path it replaced ----------------
$expect('P12: numTrusted is what num() builds from a library result; num() still refuses a forged decimal', function () {
    $d = Dec::add(Dec::parse('12.50'), Dec::parse('0.25'));
    if (Value::numTrusted($d)->dump() !== Value::num($d)->dump()) return false;
    foreach ([['neg' => 'x', 'digits' => '5', 'scale' => 0], ['neg' => false, 'digits' => '5x', 'scale' => 0],
              ['neg' => false, 'digits' => '5', 'scale' => -1]] as $forged) {
        try { Value::num($forged); return false; } catch (SelError $e) { if ($e->code !== 'E_BAD_ARG') return false; }
    }
    return \Sel\Sel::evaluate('ABS(-3) + MAX(1, 2.50) * 2')->asText() === '8.00'
        && \Sel\Sel::evaluate('SUM((1, 2.5, 3), X, X)')->asText() === '6.5';
});
$expect('P13/P15: parse, div and mod on native mantissas give the arrays of the digit-string paths', function () {
    mt_srand(20260930);
    $num = static function (): string {
        $w = mt_rand(1, 19);
        $t = (mt_rand(0, 4) === 0 ? '-' : '');
        for ($i = 0; $i < $w; $i++) $t .= (string) mt_rand(0, 9);
        if (mt_rand(0, 5) === 0) $t = ($t[0] === '-' ? '-' : '') . str_repeat('9', $w);
        $sc = mt_rand(0, 3) === 0 ? 0 : mt_rand(0, 12);
        if ($sc > 0) {
            $neg = $t[0] === '-'; $body = ltrim($t, '-');
            $body = str_pad($body, $sc + 1, '0', STR_PAD_LEFT);
            $t = ($neg ? '-' : '') . substr($body, 0, -$sc) . '.' . substr($body, -$sc);
        }
        return $t;
    };
    $edge = ['', '-', '.', '5.', '.5', '-0', '0', '007', "5\n", ' 1', '1.2.3', '123456789012345678', '1234567890123456789',
             '9223372036854775807', '-9223372036854775808', '0.1', '1.0', '7', '-7', '0.0000001'];
    $texts = $edge;
    for ($i = 0; $i < 6000; $i++) $texts[] = $num();
    $same = static function (callable $f) {
        Dec::testHooks(['fastPaths' => true]);  $a = $f();
        Dec::testHooks(['fastPaths' => false]); $b = $f();
        Dec::testHooks(['fastPaths' => true]);
        return $a === $b;
    };
    foreach ($texts as $t) {
        if (!$same(static fn () => Dec::parse($t))) return false;
    }
    for ($i = 0; $i < 12000; $i++) {
        $x = $texts[mt_rand(0, count($texts) - 1)]; $y = $texts[mt_rand(0, count($texts) - 1)];
        $a = Dec::parse($x); $b = Dec::parse($y);
        if ($a === null || $b === null) continue;
        foreach (['div', 'mod'] as $op) {
            $run = static function () use ($op, $a, $b) {
                try { return Dec::$op($a, $b); } catch (SelError $e) { return $e->code; }
            };
            if (!$same($run)) return false;
        }
    }
    Dec::testHooks(['fastPaths' => true]);
    return true;
});
$expect('P14: cmp on digit strings agrees with GMP, equal widths differing in the last digit included', function () {
    if (!extension_loaded('gmp')) return true;
    mt_srand(14);
    for ($i = 0; $i < 4000; $i++) {
        $w = mt_rand(1, 400);
        $a = (string) mt_rand(1, 9); for ($j = 1; $j < $w; $j++) $a .= (string) mt_rand(0, 9);
        $b = $a;
        if (mt_rand(0, 1)) { $k = mt_rand(0, $w - 1); $b[$k] = (string) ((int) $b[$k] === 9 ? 8 : (int) $b[$k] + 1); if ($b[0] === '0') $b[0] = '1'; }
        $want = gmp_cmp($a, $b);
        $got = Dec::cmp(Dec::parse($a), Dec::parse($b));
        if ($got !== ($want < 0 ? -1 : ($want > 0 ? 1 : 0))) return false;
    }
    return true;
});
$expect('P16: a keyed list copies keys and elements, independently of the original', function () {
    $ctx = ['L' => [5, 6, 7, 8]];
    $src = 'A = FILTER(L, X, X > 5); B = A; B["99"] = 1; C = A; C["2"] = 0; COUNT(A) & "/" & COUNT(B) & "/" & COUNT(C) & "/" & A["2"] & "/" & A["3"] & "/" & C["2"]';
    return \Sel\Sel::evaluate($src, $ctx)->asText() === '3/4/3/6/7/0';
});
$expect('P18: RREPLACE parses its replacement once and expands $0-$9, $$ and a lone $ per match', function () {
    $e = static fn (string $src) => \Sel\Sel::evaluate($src)->asText();
    if ($e('RREPLACE("(a)(b)", "<$2$1$$|$0|$x|$>", "abab")') !== '<ba$|ab|$x|$>' . '<ba$|ab|$x|$>') return false;
    if ($e('RREPLACE("é", "e$0", "aéb")') !== 'aeéb') return false;
    if ($e('RREPLACE("x", "", "axbx")') !== 'ab') return false;
    if ($e('RREPLACE("b*?", "-", "abb")') !== '-a-b-b-') return false;
    try { $e('RREPLACE("(a)", "$2", "a")'); return false; } catch (SelError $x) { if ($x->code !== 'E_BAD_ARG') return false; }
    // no match: the bad group reference is never reached
    return $e('RREPLACE("(a)", "$2", "zzz")') === 'zzz';
});
$expect('P19: CRC32 and base64 are the written-out algorithms\' answers; invalid base64 is still refused', function () {
    mt_srand(19);
    for ($i = 0; $i < 300; $i++) {
        $b = random_bytes(mt_rand(0, 300));
        if (crc32($b) !== \Sel\Builtins\Binary::crc32Reference($b)) return false;
    }
    if (\Sel\Sel::evaluate('CRC32(TO_UTF8("123456789"))')->asText() !== 'cbf43926') return false;
    $e = static fn (string $src) => \Sel\Sel::evaluate($src)->asText();
    if ($e('TO_HEX(DECODE_BASE64("QUJD"))') !== '414243' || $e('TO_HEX(DECODE_BASE64("QQ=="))') !== '41'
        || $e('TO_HEX(DECODE_BASE64("QR=="))') !== '41' || $e('TO_HEX(DECODE_BASE64(""))') !== '') return false;
    foreach (['A===', '====', 'AA=A', 'QQ=', 'QUJD=', 'QU JD', "QUJD\n", 'AAAA====', 'é==='] as $bad) {
        try { \Sel\Sel::evaluate('DECODE_BASE64(S)', ['S' => $bad]); return false; }
        catch (SelError $x) { if ($x->code !== 'E_BAD_ARG') return false; }
    }
    // past the old PCRE JIT-stack limit of a quantified-group regex
    $big = base64_encode(str_repeat('abcdefghij', 60000));
    return \Sel\Sel::evaluate('BLEN(DECODE_BASE64(S))', ['S' => $big])->asText() === '600000';
});
$expect('P20: the compiled LINK projector is cached across calls and stays correct over many shape pairs', function () {
    $run = static fn (array $l, array $r): string => \Sel\Sel::evaluate('COUNT(LINK(L, R, A, B, A["id"] == B["id"]))', ['L' => $l, 'R' => $r])->asText();
    for ($round = 0; $round < 3; $round++) {
        if ($run([['id' => 1, 'v' => 'a'], ['id' => 2, 'v' => 'b']], [['id' => 2, 'w' => 'c']]) !== '1') return false;
    }
    // more distinct (ops, slots) plans than the cache holds: it is dropped and refilled, never wrong
    for ($i = 0; $i < 700; $i++) {
        $left = ['id' => 1]; $right = ['id' => 1];
        for ($j = 0; $j <= $i % 9; $j++) { $left['l' . $j] = $j; $right['r' . ($i % 7) . $j] = $j; }
        if ($i % 5 === 0) $left['id2'] = [1, 2];
        if ($run([$left], [$right]) !== '1') return false;
    }
    return true;
});
$expect('P21: nested-loop LINK shares the aliased right rows only when the predicate cannot write', function () {
    $ctx = ['L' => array_map(static fn ($i) => ['id' => $i], range(1, 12)), 'R' => array_map(static fn ($i) => ['id' => $i], range(1, 9))];
    $pure = \Sel\Sel::evaluate('COUNT(LINK(L, R, A, B, A["id"] + B["id"] == 10))', $ctx)->asText();
    $same = \Sel\Sel::evaluate('COUNT(LINK(L, R, A, B, A["id"] + B["id"] == 10 AND TRUE))', $ctx)->asText();
    if ($pure !== $same || $pure !== '9') return false;
    // the predicate assigns (an outside name): the aliases are made per pair as before, and the answer is the same
    $write = \Sel\Sel::evaluate('COUNT(LINK(L, R, A, B, (N = N + 1; A["id"] + B["id"] == 10))) & "/" & N', $ctx + ['N' => 0])->asText();
    $leftJoin = \Sel\Sel::evaluate('COUNT(LINK_LEFT(L, R, A, B, A["id"] > 100))', $ctx)->asText();
    return $write === '9/108' && $leftJoin === '12';
});

// ---- round 3: PHP-P22 .. P30 -----------------------------------------------------------------------------------------
$expect('P22: a hybrid continuation runs on a root that owns only what it assigns; the caller is never written to', function () {
    $b = ['ORDERS' => \Sel\Sql\Binding::relation('orders', 'o', ['ID' => \Sel\Sql\Binding::column('id', 'o', 'NUM')])];
    $rows = static fn () => [['ID' => 1], ['ID' => 2], ['ID' => 3]];
    $plan = \Sel\Sql\Sql::planHybrid(\Sel\Sel::compile('X = COUNT(T); ORDERS .> TAKE(3) .> MAP(_["ID"] + X)'), 'postgresql', $b);
    if ($plan->pureSql || $plan->pureMemory) return false;
    $ctx = Value::fromNative(['T' => [['n' => 'a'], ['n' => 'b'], ['n' => 'c'], ['n' => 'd']], 'U' => 'u',
        'ORDERS' => [['ID' => 1], ['ID' => 2], ['ID' => 3]]]);
    $before = $ctx->dump();
    $out = \Sel\Sql\Sql::executeHybrid($plan, $rows, $ctx);
    if ($out->dump() !== Value::fromNative([5, 6, 7])->dump() || $ctx->dump() !== $before || $ctx->has('X')) return false;
    // a continuation that writes INTO a caller name writes to a copy of it
    $write = \Sel\Sql\Sql::planHybrid(\Sel\Sel::compile('T[1]["n"] = "z"; ORDERS .> TAKE(3) .> MAP(_["ID"] + COUNT(T))'), 'postgresql', $b);
    $out2 = \Sel\Sql\Sql::executeHybrid($write, $rows, $ctx);
    if ($ctx->dump() !== $before || $out2->dump() !== Value::fromNative([5, 6, 7])->dump()) return false;
    // copyWritable: the written names are copies, every other top-level child is shared
    foreach ([Value::fromNative(['T' => [1, 2], 'U' => [3]]), (function () { $v = Value::none(); $v->set('T', Value::fromNative([1, 2])); $v->set('U', Value::fromNative([3])); return $v; })()] as $root) {
        $c = $root->copyWritable(['T']);
        if ($c->get('U') !== $root->get('U') || $c->get('T') === $root->get('T') || $c->dump() !== $root->dump()) return false;
    }
    // pure-memory plan: the same rule
    $mem = \Sel\Sql\Sql::planHybrid(\Sel\Sel::compile('T[2]["n"] = "q"; COUNT(T)'), 'postgresql', $b);
    $outm = \Sel\Sql\Sql::executeHybrid($mem, $rows, $ctx);
    return $mem->pureMemory && $outm->asText() === '4' && $ctx->dump() === $before;
});
$expect('P24: the dialect chain and lexical memos follow registration and reset', function () {
    \Sel\Sql\Map::reset();
    $base = \Sel\Sql\Map::lexical('mariadb', 'textCollate');
    \Sel\Sql\Map::defineDialect('p24-x', ['extends' => 'mariadb', 'lexical' => ['textCollate' => 'COLLATE one']]);
    if (\Sel\Sql\Map::lexical('p24-x', 'textCollate') !== 'COLLATE one' || \Sel\Sql\Map::lexical('mariadb', 'textCollate') !== $base) return false;
    if (\Sel\Sql\Map::chain('p24-x') !== ['p24-x', 'mariadb', 'mysql-family', 'ansi']) return false;
    \Sel\Sql\Map::defineDialect('p24-x', ['extends' => 'mariadb', 'lexical' => ['textCollate' => 'COLLATE two']]);
    if (\Sel\Sql\Map::lexical('p24-x', 'textCollate') !== 'COLLATE two') return false;
    \Sel\Sql\Map::reset();
    return !\Sel\Sql\Map::exists('p24-x') && \Sel\Sql\Map::chain('p24-x') === [] && \Sel\Sql\Map::lexical('mariadb', 'textCollate') === $base;
});
$expect('P24: text literals escape the same after the escape map is memoised', function () {
    $m = \Sel\Sql\Emit::textLiteral('mariadb', "a'b\\c\n");
    $pg = \Sel\Sql\Emit::textLiteral('postgresql', "a'b\\c");
    return $m === \Sel\Sql\Emit::textLiteral('mariadb', "a'b\\c\n") && $pg === "'a''b\\c'" && str_starts_with($m, "'") && str_ends_with($m, "'");
});
$expect('P25: DEDUPE and DISTINCT keep the first of equal items, for leaves and containers alike', function () {
    mt_srand(25);
    $pool = ['a', 'b', '1', '1.0', 1, '2.5', true, false, [], [1], [1, 2], ['k' => 1], ['k' => '1'], ['x' => [1]], '', ' '];
    $pool = array_values($pool);
    for ($round = 0; $round < 40; $round++) {
        $items = [];
        for ($i = 0; $i < 30; $i++) $items[] = $pool[mt_rand(0, count($pool) - 1)];
        $list = Value::fromNative($items);
        $want = []; $kept = [];
        $list->forEachElement(static function (string $k, Value $v) use (&$kept): void {
            foreach ($kept as $e) if ($v->eql($e)) return;
            $kept[] = $v;
        });
        foreach (['DEDUPE(L)', 'DISTINCT(L)'] as $src) {
            $out = \Sel\Sel::evaluate($src, ['L' => $list]);
            if ($out->size() !== count($kept)) return false;
            $i = 0;
            $ok = true;
            $out->forEachElement(static function (string $k, Value $v) use (&$i, $kept, &$ok): void { if (!$v->eql($kept[$i++])) $ok = false; });
            if (!$ok) return false;
        }
    }
    // a value with no children hashes to its own key; containers to a digest; NULL and [] agree
    return Value::null()->structuralHash() === Value::list([])->structuralHash()
        && Value::text('ab')->structuralHash() !== Value::text('a')->structuralHash()
        && Value::list([Value::text('a')])->structuralHash() !== Value::text('a')->structuralHash()
        && !str_contains(Value::list([Value::text('a')])->structuralHash(), ':');
});
$expect('P26: TOP equals SORT then TAKE, with and without `_K`, both below and above the row count', function () {
    mt_srand(26);
    $rows = []; for ($i = 0; $i < 60; $i++) $rows[] = ['v' => mt_rand(0, 9), 'w' => 'w' . $i];
    foreach ([1, 5, 59, 60, 61, 1000] as $n) {
        foreach (['ASC' => 'TOP', 'DESC' => 'TOP_DESC'] as $dir => $fn) {
            $top = \Sel\Sel::evaluate("JOIN($fn(R, _[\"v\"], $n) .> MAP(_[\"w\"]), \",\")", ['R' => $rows])->asText();
            $sortFn = $dir === 'ASC' ? 'SORT_BY' : 'SORT_BY';
            $ref = \Sel\Sel::evaluate("JOIN(R .> SORT_BY(_[\"v\"], \"$dir\") .> TAKE($n) .> MAP(_[\"w\"]), \",\")", ['R' => $rows])->asText();
            if ($top !== $ref) return false;
        }
        // a body that reads _K is keyed per row; one that does not skips the binding
        $withK = \Sel\Sel::evaluate("JOIN(TOP(R, _[\"v\"] + LEN(_K) * 0, $n) .> MAP(_[\"w\"]), \",\")", ['R' => $rows])->asText();
        $noK = \Sel\Sel::evaluate("JOIN(TOP(R, _[\"v\"], $n) .> MAP(_[\"w\"]), \",\")", ['R' => $rows])->asText();
        if ($withK !== $noK) return false;
    }
    return true;
});
$expect('P27: the native running total gives exactly the answer of a chain of Dec::add', function () {
    mt_srand(27);
    $gen = static function (): string {
        $k = mt_rand(0, 6);
        $int = match ($k) { 0 => (string) mt_rand(0, 999), 1 => (string) mt_rand(), 2 => str_repeat('9', mt_rand(15, 25)), 3 => '0', 4 => (string) mt_rand(0, 9), default => (string) mt_rand(0, 99999) };
        $frac = mt_rand(0, 2) === 0 ? '' : '.' . str_repeat((string) mt_rand(0, 9), mt_rand(1, 22));
        return (mt_rand(0, 3) === 0 ? '-' : '') . $int . $frac;
    };
    for ($round = 0; $round < 400; $round++) {
        $texts = []; for ($i = 0, $n = mt_rand(0, 14); $i < $n; $i++) $texts[] = $gen();
        $chain = Dec::zero();
        $acc = ['m' => 0, 's' => 0];
        foreach ($texts as $t) {
            $d = Dec::parse($t);
            $chain = Dec::add($chain, $d);
            Dec::sumAccumulate($acc, $d);
        }
        if (Dec::format(Dec::sumResult($acc)) !== Dec::format($chain)) return false;
        $viaSel = \Sel\Sel::evaluate('SUM(L, X, X)', ['L' => $texts])->asText();
        if ($viaSel !== Dec::format($chain)) return false;
    }
    return true;
});
$expect('P29: Sel::evaluate of a program with no per-element work equals run(); literal concatenation folds', function () {
    $srcs = ['X * 2 + 1 > 10 AND S $== "a"', '"abc" & "def"', '"a" & "b" & S', 'IF(X > 1, "p" & "q", "r")', 'LEN("é" & "漢")', 'X / 0'];
    foreach ($srcs as $src) {
        $ctx = ['X' => 6, 'S' => 'a'];
        try { $a = \Sel\Sel::evaluate($src, $ctx)->dump(); } catch (SelError $e) { $a = $e->code . '@' . $e->line . ':' . $e->col; }
        try { $b = \Sel\Sel::compile($src)->run(Value::fromNative($ctx))->dump(); } catch (SelError $e) { $b = $e->code . '@' . $e->line . ':' . $e->col; }
        if ($a !== $b) return false;
    }
    $folded = \Sel\Optimizer::optimize(\Sel\Sel::compile('"abc" & "def"')->ast, true);
    $kept = \Sel\Optimizer::optimize(\Sel\Sel::compile('"abc" & S')->ast, true);
    return $folded['t'] === 'text' && $folded['v'] === 'abcdef' && $kept['t'] === 'bin';
});
$expect('P30: a cached `i` pattern is not re-scanned, and a non-ASCII pattern still refuses the flag every time', function () {
    for ($i = 0; $i < 3; $i++) {
        if (\Sel\Sel::evaluate('RMATCH(P, "ABC", "i")', ['P' => 'abc'])->dump() !== Value::bool(true)->dump()) return false;
        try { \Sel\Sel::evaluate('RMATCH(P, "x", "i")', ['P' => 'é']); return false; }
        catch (SelError $e) { if ($e->code !== 'E_BAD_ARG') return false; }
    }
    return true;
});

// --- Item 1 (2026-10-01): lazy digits -----------------------------------------
// A big result may keep its magnitude as GMP and write its digits only when text
// is asked for (the lazyDigits test hook, Dec::testHooks()). Whatever it keeps, these must hold: every
// operation answers as the pure-PHP digit-string path does, the host sees
// today's arrays, the digit cap and its errors are where they were, and a chain
// of big products does not convert digits in between (the conversion counter).
$item1Modes = extension_loaded('gmp') ? [[true, true], [true, false], [false, false]] : [[false, false]];
$item1With = static function (bool $gmp, bool $lazy, callable $f) {
    $wasLazy = Dec::testHooks()['lazyDigits'];
    Dec::testHooks(['gmp' => $gmp]);
    Dec::testHooks(['lazyDigits' => $lazy]);
    try { return $f(); } finally { Dec::testHooks(['gmp' => null]); Dec::testHooks(['lazyDigits' => $wasLazy]); }
};
$item1Num = static function (int $w, int $sc, bool $neg): string {
    $t = (string) mt_rand(1, 9);
    for ($i = 1; $i < $w; $i++) $t .= (string) mt_rand(0, 9);
    if ($sc > 0) {
        $t = str_pad($t, $sc + 1, '0', STR_PAD_LEFT);
        $t = substr($t, 0, -$sc) . '.' . substr($t, -$sc);
    }
    return ($neg ? '-' : '') . $t;
};
// What an answer is, whatever form it came in: a descriptor by its text and
// scale, anything else (an int, a bool, an error code) as it is.
$item1Canon = static fn ($x) => is_array($x) ? [$x['neg'], Dec::format($x), $x['scale']] : $x;
$item1Try = static function (callable $f) {
    try { return $f(); } catch (SelError $e) { return $e->code; }
};
$expect('item 1 T1: every operation agrees with lazy digits on and off, with and without GMP', function () use ($item1Modes, $item1With, $item1Num, $item1Canon, $item1Try) {
    mt_srand(20261001);
    $texts = ['0', '0.000', '7', '-7', '9223372036854775807', '-9223372036854775808', '9223372036854775808',
              '18446744073709551616', '0.5', '-2.50'];
    for ($i = 0; $i < 70; $i++) {
        $w = [mt_rand(1, 18), mt_rand(19, 25), mt_rand(26, 120), mt_rand(100, 600)][mt_rand(0, 3)];
        if (mt_rand(0, 19) === 0) $w = mt_rand(1500, 3000);
        $texts[] = $item1Num($w, [0, 0, 1, mt_rand(0, 30), mt_rand(0, 30)][mt_rand(0, 4)], (bool) mt_rand(0, 1));
    }
    $ops = [
        'add' => fn($a, $b) => Dec::add($a, $b), 'sub' => fn($a, $b) => Dec::sub($a, $b),
        'mul' => fn($a, $b) => Dec::mul($a, $b), 'div' => fn($a, $b) => Dec::div($a, $b),
        'mod' => fn($a, $b) => Dec::mod($a, $b), 'cmp' => fn($a, $b) => Dec::cmp($a, $b),
        'round' => fn($a, $b) => Dec::round($a, 3), 'trunc' => fn($a, $b) => Dec::trunc($a),
        'floor' => fn($a, $b) => Dec::floor($a), 'ceil' => fn($a, $b) => Dec::ceil($a),
        'negate' => fn($a, $b) => Dec::negate($a), 'abs' => fn($a, $b) => Dec::abs($a),
        'isZero' => fn($a, $b) => Dec::isZero($a), 'sign' => fn($a, $b) => Dec::sign($a),
        'isInteger' => fn($a, $b) => Dec::isInteger($a), 'toInt' => fn($a, $b) => Dec::toInt($a),
        'trimScale' => fn($a, $b) => Dec::trimScale($a), 'power' => fn($a, $b) => Dec::power($a, 3),
        'sum' => function ($a, $b) { $acc = ['m' => 0, 's' => 0]; Dec::sumAccumulate($acc, $a); Dec::sumAccumulate($acc, $b); Dec::sumAccumulate($acc, $a); return Dec::sumResult($acc); },
    ];
    for ($i = 0; $i < 300; $i++) {
        $x = $texts[mt_rand(0, count($texts) - 1)];
        $y = $texts[mt_rand(0, count($texts) - 1)];
        foreach ($ops as $name => $op) {
            $want = $item1With(false, false, fn() => $item1Canon($item1Try(fn() => $op(Dec::parse($x), Dec::parse($y)))));
            foreach ($item1Modes as [$gmp, $lazy]) {
                // The operands as parsed, and as an earlier operation leaves them
                // (adding zero keeps the value and, with lazy digits, its GMP form).
                $got = $item1With($gmp, $lazy, function () use ($op, $x, $y, $item1Canon, $item1Try) {
                    $a = Dec::parse($x); $b = Dec::parse($y);
                    $la = Dec::add($a, Dec::zero()); $lb = Dec::add($b, Dec::zero());
                    return [$item1Canon($item1Try(fn() => $op($a, $b))), $item1Canon($item1Try(fn() => $op($la, $lb))),
                            $item1Canon($item1Try(fn() => $op($la, $b))), $item1Canon($item1Try(fn() => $op($a, $lb)))];
                });
                foreach ($got as $k => $g) {
                    if ($g !== $want) return "$name($x, $y) gmp=" . (int) $gmp . ' lazy=' . (int) $lazy . " form $k: "
                        . json_encode($g) . ' want ' . json_encode($want);
                }
            }
        }
    }
    return true;
});
$expect('item 1 T2: a host sees today\'s arrays, and its edits take effect', function () use ($item1Modes, $item1With) {
    foreach ($item1Modes as [$gmp, $lazy]) {
        $r = $item1With($gmp, $lazy, function () {
            $a = str_repeat('7', 40);
            $v = \Sel\Sel::compile('A * A + 1')->run(['A' => $a]);
            $d = $v->asDecimal();
            if (!is_string($d['digits']) || array_key_exists('gmp', $d)) return 'asDecimal() is not today\'s array';
            if (Dec::format($d) !== $v->asText()) return 'asDecimal() and asText() differ';
            if (Value::num($v->decVal)->asText() !== $v->asText()) return 'Value::num(decVal) differs';
            $big = Dec::mul(Dec::parse($a), Dec::parse($a));
            $edited = $big; $edited['digits'] = '5'; unset($edited['gmp']);
            if (Dec::format(Dec::add($edited, Dec::parse('1'))) !== '6') return 'an edited result kept its old value';
            $parsed = Dec::parse(str_repeat('9', 300));
            if (!is_string($parsed['digits']) || array_key_exists('gmp', $parsed)) return 'parse() gave a lazy value';
            return true;
        });
        if ($r !== true) return "gmp=" . (int) $gmp . " lazy=" . (int) $lazy . ": $r";
    }
    return true;
});
$expect('item 1 T3: the integer-digit cap holds for every form, at the same place', function () use ($item1Modes, $item1With, $pos) {
    $L = Dec::MAX_INT_DIGITS;
    $nines = str_repeat('9', $L);
    foreach ($item1Modes as [$gmp, $lazy]) {
        $r = $item1With($gmp, $lazy, function () use ($L, $nines, $pos) {
            foreach ([0, 5] as $sc) {
                $top = Dec::parse($sc === 0 ? $nines : $nines . '.' . str_repeat('9', $sc));
                $atCap = Dec::add($top, Dec::zero(), $pos);                   // exactly L integer digits
                if (strlen(Dec::format($atCap)) !== $L + ($sc ? $sc + 1 : 0)) return "at the cap, scale $sc";
                $ulp = Dec::parse($sc === 0 ? '1' : '0.' . str_repeat('0', $sc - 1) . '1');
                try { Dec::add($atCap, $ulp, $pos); return "past the cap by add, scale $sc"; }
                catch (SelError $e) { if ($e->code !== 'E_RANGE' || $e->line !== $pos['line'] || $e->col !== $pos['col']) return "add error {$e->code}"; }
            }
            if (!Dec::testHooks()['lazyDigits']) return true;   // the products below are GMP-sized: lazy mode only
            $half = Dec::parse('1' . str_repeat('0', intdiv($L, 2)));
            $ok = Dec::mul($half, Dec::parse(str_repeat('9', intdiv($L, 2))), $pos);   // 10^L - 10^(L/2): L digits
            if (strlen(Dec::format($ok)) !== $L) return 'a product at the cap';
            try { Dec::mul($half, $half, $pos); return 'past the cap by mul'; }
            catch (SelError $e) { if ($e->code !== 'E_RANGE' || $e->col !== $pos['col']) return "mul error {$e->code}"; }
            return true;
        });
        if ($r !== true) return "gmp=" . (int) $gmp . " lazy=" . (int) $lazy . ": $r";
    }
    return true;
});
$expect('item 1 T4: comparisons across scale gaps agree in every form', function () use ($item1Modes, $item1With, $item1Num) {
    mt_srand(4);
    for ($i = 0; $i < 300; $i++) {
        $gap = [0, 1, 18, 19, 5000][mt_rand(0, 4)];
        $a = $item1Num(mt_rand(20, 400), mt_rand(0, 20), (bool) mt_rand(0, 1));
        $b = Dec::format(Dec::round(Dec::parse($a), Dec::parse($a)['scale'] + $gap));
        if (mt_rand(0, 1)) { $b[strlen($b) - 1] = (string) ((int) $b[strlen($b) - 1] === 9 ? 8 : (int) $b[strlen($b) - 1] + 1); }
        $want = $item1With(false, false, fn() => Dec::cmp(Dec::parse($a), Dec::parse($b)));
        foreach ($item1Modes as [$gmp, $lazy]) {
            $got = $item1With($gmp, $lazy, function () use ($a, $b) {
                $x = Dec::add(Dec::parse($a), Dec::zero()); $y = Dec::add(Dec::parse($b), Dec::zero());
                return [Dec::cmp($x, $y), Dec::cmp($y, $x), Dec::cmp($x, Dec::parse($b)), Dec::cmp(Dec::parse($a), $y)];
            });
            if ($got !== [$want, -$want, $want, $want]) return "$a vs $b gap $gap: " . json_encode($got) . " want $want";
        }
    }
    return true;
});
$expect('item 1 T5: a chain of big products converts no digits in between', function () use ($run, $item1With) {
    if (!extension_loaded('gmp')) return true;
    return $item1With(true, Dec::testHooks()['lazyDigits'], function () use ($run) {
        $count = function (int $k) use ($run): int {
            Dec::testHooks(['conversions' => 0]);
            $run('X = A * A; Y = X; ' . str_repeat('Y = Y * X; ', $k) . 'Y > 1', ['A' => '123456789012345678901234567890']);
            return Dec::testHooks()['conversions'];
        };
        $growth = $count(41) - $count(1);
        // Three conversions a product before item 1 (two operands in, one result out).
        return $growth <= 0 ? true : "$growth conversions for 40 more products";
    });
});
if ($boundary) {
    fwrite(STDERR, 'PHP runtime: ' . count($boundary) . " host-boundary contract(s) broken:\n  " . implode("\n  ", $boundary) . "\n");
    exit(1);
}
echo "PHP runtime: $checks checks passed\n";
