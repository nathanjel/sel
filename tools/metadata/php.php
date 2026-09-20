<?php
// php tools/metadata/php.php [bench]; optional SEL_METADATA_ROOT.
declare(strict_types=1);
require (getenv('SEL_METADATA_ROOT') ?: dirname(__DIR__,2)).'/php/src/bootstrap.php';
use Sel\Value;
use Sel\Sel;
use Sel\RecordShape;
$leaf=Value::text('1'); $ctx=Value::none();
$ctx->set('X',$leaf); $ctx->set('Y',Value::text('two'));
$program=Sel::compile('RECORD("id", X, "name", Y)'); $program->run($ctx);
function churn(int $n): void {
    global $leaf;
    for($i=0;$i<$n;$i++) Value::record(['field_'.$i,'id'],[$leaf,$leaf]);
}
function aliases(int $n): void {
    global $leaf;
    $join=Sel::compile('LINK(L, R, O, C, O["id"] == C["id"])');
    $context=Value::none(); $context->set('R',Value::list([Value::record(['id'],[$leaf])]));
    for($i=0;$i<$n;$i++) {
        $context->set('L',Value::list([Value::record(['id','alias_'.$i],[$leaf,$leaf])]));
        $join->run($context);
    }
}
function verify(bool $ok): void { if(!$ok) throw new RuntimeException('metadata check failed'); }
if(($argv[1]??'')==='bench') {
    $samples=[];
    for($r=0;$r<5;$r++) {
        $start=hrtime(true);
        for($i=0;$i<20000;$i++) $program->run($ctx);
        $samples[]=(hrtime(true)-$start)/20000/1000;
    }
    gc_collect_cycles(); $start=memory_get_usage();
    churn(20000); gc_collect_cycles(); $shapes=memory_get_usage()-$start;
    aliases(5000); gc_collect_cycles(); $both=memory_get_usage()-$start;
    echo json_encode(['runtime'=>PHP_VERSION,'steady_us'=>$samples,
        'shapes_retained'=>$shapes,'shapes_aliases_retained'=>$both]),"\n";
} else {
    $held=Value::record(['held'],[$leaf]); $prepared=$program->run($ctx)->shape;
    churn(2000); $again=Value::record(['held'],[$leaf]);
    verify($held->shape!==$again->shape && $held->eql($again));
    verify($held->structuralHash()===$again->structuralHash());
    verify($program->run($ctx)->shape===$prepared);
    verify(RecordShape::stats()['cache_size']<=256);
    foreach([array_map(fn($i)=>'wide_'.$i,range(0,256)),[str_repeat('x',16385)]] as $keys) {
        $values=array_fill(0,count($keys),$leaf);
        verify(Value::record($keys,$values)->shape!==Value::record($keys,$values)->shape);
    }
    $dynamic=Sel::compile('RECORD(K, X)');
    $ctx->set('K',Value::text('first')); verify($dynamic->run($ctx)->get('first')->scalar==='1');
    $ctx->set('K',Value::text('second')); verify($dynamic->run($ctx)->get('second')->scalar==='1');
    verify(Sel::compile('RECORD("id", X, "id", Y)')->run($ctx)->get('id')->scalar==='two');
    aliases(1000); verify(RecordShape::stats()['alias_cache_entries']<=256);
    echo "PHP metadata checks passed\n";
}
