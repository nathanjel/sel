<?php
require '/work/php/src/bootstrap.php';
require '/work/php/src/Sql/bootstrap.php';
use Sel\Sel;
use Sel\Sql\Binding;
use Sel\Sql\Sql;
$bindings=[];
$replay=getenv('AUDIT_REPLAY')?file('/work/tools/adversarial/replay-php.sel',FILE_IGNORE_NEW_LINES):null;$n=0;
foreach(['r','s'] as $t){$fields=[];foreach(['id','cat','v','fk'] as $c)$fields[strtoupper($c)]=Binding::column($c,$t,$c==='cat'?'TEXT':'NUM');$bindings[strtoupper($t)]=Binding::relation($t,$t,$fields);}
foreach(file('/work/tools/adversarial/queries.sel',FILE_IGNORE_NEW_LINES) as $i=>$source){
 foreach(['sqlite','postgresql','mariadb'] as $d)foreach([false,true] as $strict){
  $out=['i'=>$i,'d'=>$d,'strict'=>$strict];$p=Sel::compile($source);
  try{$f=Sql::translateStatement($p,$d,$bindings,['strict'=>$strict]);$out+=['sql'=>$f->asStatement(),'params_sql'=>$f->asStatement('params'),'params'=>array_map(fn($v)=>$v->asText(),$f->bindings()),'caveats'=>$f->caveats];}catch(Throwable $e){$out['error']=$e->code??get_class($e);}
  try{$p1=Sql::planHybrid($p,$d,$bindings,['strict'=>$strict]);$out['plan']=$p1->pureSql?'pure_sql':($p1->pureMemory?'pure_memory':'hybrid');$out['prefix']=$p1->sqlStatement?->asStatement();}catch(Throwable $e){$out['plan_error']=$e->code??get_class($e);}
  if($replay){$data=Sel::compile($replay[$n])->run();try{if($data->get('error')->asText())throw new Exception('DB_ERROR');$h=Sql::planHybrid($p,$d,$bindings,['strict'=>$strict]);$out['hybrid_value']=Sql::executeHybrid($h,fn()=> $data->get('rows'),$data->get('context'))->dump();}catch(Throwable $e){$out['hybrid_error']=$e instanceof \Sel\SelError?$e->code:$e->getMessage();}}$n++;
  echo json_encode($out),"\n";
 }
}
