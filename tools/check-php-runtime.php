<?php
declare(strict_types=1);
// The budget checks below build values at the language's size caps (conformance/29):
// a million-element list is 250-500 MB of PHP objects, over the default 128M limit.
ini_set('memory_limit', '1G');
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
        Dec::forceGmp($gmp);
        try {
            $octal = ['neg' => false, 'digits' => '010', 'scale' => 0];   // un-normalised: never octal
            $hex = ['neg' => false, 'digits' => '0', 'scale' => 0];
            return Dec::format(Dec::add($octal, Dec::parse('1'))) === '11'
                && Dec::format(Dec::mul($octal, ['neg' => false, 'digits' => '010', 'scale' => 0])) === '100'
                && Dec::format(Dec::div(['neg' => false, 'digits' => '0100', 'scale' => 0], Dec::parse('4'))) === '25'
                && Dec::format(Dec::sub($hex, Dec::parse('1'))) === '-1';
        } finally { Dec::forceGmp(null); }
    });
    $expect("limb product carries before an accumulator can overflow, $label", function () use ($gmp) {
        $was = Dec::$mulCarryEvery;
        try {
            foreach ([1, 2, 7] as $every) {
                Dec::$mulCarryEvery = $every;
                Dec::forceGmp($gmp);
                $a = str_repeat('9', 2100); $b = str_repeat('9', 2113);
                $got = Dec::mul(Dec::parse($a), Dec::parse($b))['digits'];
                // (10^m - 1)(10^n - 1) = 10^(m+n) - 10^m - 10^n + 1, worked by hand:
                // 2099 nines, an 8, 13 nines, 2099 zeros and a 1.
                $want = str_repeat('9', 2099) . '8' . str_repeat('9', 13) . str_repeat('0', 2099) . '1';
                if ($got !== $want) return "every=$every";
            }
            return true;
        } finally { Dec::$mulCarryEvery = $was; Dec::forceGmp(null); }
    });
}
$expect('the default carry interval keeps a slot under PHP_INT_MAX', fn() =>
    is_int(Dec::$mulCarryEvery * (10 ** 7 - 1) ** 2 + 10 ** 7) && Dec::$mulCarryEvery > 0);
// The GMP-vs-plain results agree on random operands.
$expect('ext-gmp and pure-PHP multiplication agree', function () {
    mt_srand(29);
    for ($k = 0; $k < 40; $k++) {
        $a = (string) mt_rand(1, 9) . implode('', array_map(fn() => (string) mt_rand(0, 9), range(1, mt_rand(1, 400))));
        $b = (string) mt_rand(1, 9) . implode('', array_map(fn() => (string) mt_rand(0, 9), range(1, mt_rand(1, 400))));
        Dec::forceGmp(true);  $x = Dec::format(Dec::mul(Dec::parse($a), Dec::parse($b)));
        Dec::forceGmp(false); $y = Dec::format(Dec::mul(Dec::parse($a), Dec::parse($b)));
        Dec::forceGmp(null);
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
    && $portable('[[.]') === '[[.]');
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

$expect('an integer Value builds its decimal lazily', function () {
    $v = Value::int(7);
    return $v->decVal === null && Dec::format($v->asDecimal()) === '7';
});
if ($boundary) {
    fwrite(STDERR, 'PHP runtime: ' . count($boundary) . " host-boundary contract(s) broken:\n  " . implode("\n  ", $boundary) . "\n");
    exit(1);
}
echo "PHP runtime: $checks checks passed\n";
