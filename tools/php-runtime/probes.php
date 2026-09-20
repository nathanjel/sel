<?php
// Run from the source tree being measured. Times exclude setup and validation.
declare(strict_types=1);
require getcwd().'/php/src/bootstrap.php';
use Sel\Sel;
use Sel\Value;
$results=[];
function measure(string $name, callable $fn, int $count): void {
    global $results;
    for($i=0;$i<3;$i++) $fn();
    $samples=[];
    for($r=0;$r<7;$r++) {
        $start=hrtime(true);
        for($i=0;$i<$count;$i++) $fn();
        $samples[]=(hrtime(true)-$start)/$count/1000;
    }
    $sorted=$samples;sort($sorted);
    $results[$name]=['median_us'=>$sorted[3],'samples_us'=>$samples];
}
$value=Value::num('123.4500');
if($value->getScalar()!=='123.4500' || $value->scalar!==$value->getScalar()) throw new RuntimeException('scalar API');
measure('scalar.magic',fn()=>$value->scalar,200000);
measure('scalar.explicit',fn()=>$value->getScalar(),200000);
foreach(['numeric_text','numeric_cached','literal_lazy'] as $kind) {
    $rows=[];
    for($i=0;$i<500;$i++) {
        $key=match($kind) {
            'numeric_text'=>Value::text((string)$i),
            'numeric_cached'=>Value::int($i),
            default=>Value::num($i.'.00'),
        };
        $rows[]=Value::record(['id'],[$key]);
    }
    $context=Value::none();$context->set('L',Value::list($rows));$context->set('R',Value::list($rows));
    $op=$kind==='literal_lazy'?'$==':'==';
    $program=Sel::compile('LINK(L, R, A, B, A["id"] '.$op.' B["id"])');
    if($program->run($context)->size()!==500) throw new RuntimeException('join parity');
    measure('join.'.$kind,fn()=>$program->run($context),10);
}
$program=Sel::compile('SORT(X)');$context=Value::none();
$context->set('X',Value::list(array_map(fn($i)=>Value::bool(($i%2)===0),range(0,499))));
$expected=array_merge(array_fill(0,250,false),array_fill(0,250,true));
if($program->run($context)->toNative()!==$expected) throw new RuntimeException('sort parity');
measure('sort.booleans',fn()=>$program->run($context),20);
echo json_encode(['php'=>PHP_VERSION,'results'=>$results],JSON_PRETTY_PRINT)."\n";
