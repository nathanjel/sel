<?php
require '/work/php/src/bootstrap.php';
require '/work/php/src/Sql/bootstrap.php';
use Sel\Sel;
use Sel\Sql\Binding;
use Sel\Sql\Sql;
$bindings=[];
$replay=getenv('AUDIT_REPLAY_DATA')!==false?explode("\n",rtrim(getenv('AUDIT_REPLAY_DATA'),"\n")):(getenv('AUDIT_REPLAY')?file('/work/tools/adversarial/replay-php.sel',FILE_IGNORE_NEW_LINES):null);$n=0;
foreach(['r','s'] as $t){$fields=[];foreach(['id','cat','v','fk'] as $c)$fields[strtoupper($c)]=Binding::column($c,$t,$c==='cat'?'TEXT':'NUM');$bindings[strtoupper($t)]=Binding::relation($t,$t,$fields);}
if(getenv('AUDIT_UNIQUE_KEY'))$bindings['R']=$bindings['R']->withUniqueKey('id');
foreach(getenv('AUDIT_QUERY')!==false?[getenv('AUDIT_QUERY')]:file('/work/tools/adversarial/queries.sel',FILE_IGNORE_NEW_LINES) as $i=>$source){
 foreach(['sqlite','postgresql','mariadb'] as $d)foreach([false,true] as $strict){
  $out=['i'=>$i,'d'=>$d,'strict'=>$strict];$p=Sel::compile($source);
  try{$f=Sql::translateStatement($p,$d,$bindings,['strict'=>$strict]);$out+=['sql'=>$f->asStatement(),'params_sql'=>$f->asStatement('params'),'params'=>array_map(fn($v)=>$v->asText(),$f->bindings()),'caveats'=>$f->caveats];}catch(Throwable $e){$out['error']=$e->code??get_class($e);}
  try{$p1=Sql::planHybrid($p,$d,$bindings,['strict'=>$strict]);$out['plan']=$p1->pureSql?'pure_sql':($p1->pureMemory?'pure_memory':'hybrid');$out['prefix']=$p1->sqlStatement?->asStatement();if($p1->sqlStatement)$out+=['prefix_params_sql'=>$p1->sqlStatement->asStatement('params'),'prefix_params'=>array_map(fn($v)=>$v->asText(),$p1->sqlStatement->bindings())];}catch(Throwable $e){$out['plan_error']=$e->code??get_class($e);}
  if($replay){$data=Sel::compile($replay[$n])->run();try{if($data->get('error')->asText())throw new Exception('DB_ERROR');$h=Sql::planHybrid($p,$d,$bindings,['strict'=>$strict]);$out['hybrid_value']=Sql::executeHybrid($h,fn()=> $data->get('rows'),$data->get('context'))->dump();}catch(Throwable $e){$out['hybrid_error']=$e instanceof \Sel\SelError?$e->code:$e->getMessage();}}$n++;
  echo json_encode($out),"\n";
 }
}
