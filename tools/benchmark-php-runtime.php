<?php
// Run against either source tree: php tools/benchmark-php-runtime.php [bootstrap.php]
declare(strict_types=1);
require $argv[1] ?? __DIR__ . '/../php/src/bootstrap.php';
use Sel\Dec;
use Sel\Value;
use Sel\Sel;
$results = [];
function measure(string $name, callable $fn, int $count = 20000): void {
    global $results;
    $fn(0); $samples = [];
    for ($r = 0; $r < 5; $r++) {
        $start = hrtime(true);
        for ($i = 0; $i < $count; $i++) $fn($i);
        $samples[] = (hrtime(true) - $start) / $count / 1000;
    }
    sort($samples); $results[$name] = ['median_us' => $samples[2], 'samples_us' => $samples];
}
foreach ([10, 1000, 100000] as $size) {
    $leaf = Value::text('first'); $record = Value::none();
    for ($i=0; $i<$size; $i++) $record->set('k'.$i, $leaf);
    $packed = Value::list(array_fill(0, $size, $leaf));
    measure('scalar.record.'.$size, fn() => $record->asText(), 200);
    measure('scalar.packed.'.$size, fn() => $packed->asText(), 200);
}
foreach (['small'=>['123.45','67.89'], 'signed'=>['-123.45','0.6789'],
          'large'=>['9223372036854775808','12345678901234567890']] as $label=>$pair) {
    $a=Dec::parse($pair[0]); $b=Dec::parse($pair[1]);
    foreach (['add','sub','mul','div','cmp'] as $op) {
        measure('decimal.'.$label.'.'.$op, fn() => Dec::$op($a,$b));
    }
}
$a=Dec::parse('123.45'); $b=Dec::parse('67.89'); $c=Dec::parse('0.01');
measure('decimal.chain', fn() => Dec::add(Dec::mul($a,$b),$c));
$rule=Sel::compile('ROUND((A * B + C) / 3, 2)');
$context=Value::fromNative(['A'=>'123.45','B'=>'67.89','C'=>'0.01']);
$rule->run($context);
measure('program.math', fn() => $rule->run($context), 5000);

// Representation experiment: dynamic inputs prevent constant-array reuse.
final class DecimalRecordProbe {
    public function __construct(public readonly bool $neg, public readonly string $digits,
                                public readonly int $scale) {}
}
final class NativeDecimalProbe {
    public function __construct(public readonly bool $neg, public readonly ?string $digits,
                                public readonly int $scale, public readonly ?int $native) {}
}
measure('representation.array.construct', fn($n) => ['neg'=>false,'digits'=>(string)$n,'scale'=>2]);
measure('representation.object.construct', fn($n) => new DecimalRecordProbe(false,(string)$n,2));
$x=new NativeDecimalProbe(false,'12345',2,12345); $y=new NativeDecimalProbe(false,'6789',2,6789);
measure('representation.object.lazy_mul', function() use($x,$y) {
    $n=$x->native*$y->native;
    if (!is_int($n)) throw new RuntimeException('probe inputs must fit native integers');
    return new NativeDecimalProbe($n<0,null,$x->scale+$y->scale,$n);
});
function retainedBytes(callable $factory): int {
    $items=[]; $start=memory_get_usage();
    for($i=0;$i<10000;$i++) $items[]=$factory($i);
    return memory_get_usage()-$start;
}
$memory = [
    'array_10000' => retainedBytes(fn($n)=>['neg'=>false,'digits'=>(string)$n,'scale'=>2]),
    'object_10000' => retainedBytes(fn($n)=>new DecimalRecordProbe(false,(string)$n,2)),
    'decimal_10000' => retainedBytes(fn($n)=>Dec::fromInt($n)),
];
echo json_encode(['php'=>PHP_VERSION,'int_size'=>PHP_INT_SIZE,'gmp'=>extension_loaded('gmp'),
                  'opcache_jit'=>ini_get('opcache.jit'),'memory'=>$memory,'results'=>$results], JSON_PRETTY_PRINT),"\n";
