<?php
// Runs only fixture/query pairs supplied by the audit in disposable containers.
$all=json_decode(stream_get_contents(STDIN),true,512,JSON_THROW_ON_ERROR);
$dbs=[
 'sqlite'=>new PDO('sqlite::memory:'),
 'postgresql'=>new PDO('pgsql:host='.getenv('AUDIT_PG').';dbname=sel_audit','postgres','sel_audit'),
 'mariadb'=>new PDO('mysql:host='.getenv('AUDIT_MARIA').';dbname=sel_audit;charset=utf8mb4','root','sel_audit'),
];
$results=[];
foreach($all as $r){
 try{
  $db=$dbs[$r['dialect']];$db->setAttribute(PDO::ATTR_ERRMODE,PDO::ERRMODE_EXCEPTION);$db->setAttribute(PDO::ATTR_STRINGIFY_FETCHES,true);
  if($r['dialect']!=='sqlite')$db->setAttribute(PDO::ATTR_EMULATE_PREPARES,false);
  $db->exec($r['fixture']);
  $s=$db->prepare($r['sql']);$s->execute($r['params']);
  $cols=[];for($i=0;$i<$s->columnCount();$i++)$cols[]=$s->getColumnMeta($i)['name'];
  $rows=$s->fetchAll(PDO::FETCH_NUM);$assoc=[];
  foreach($rows as $row){$a=[];foreach($cols as $i=>$c)$a[$c]=$row[$i];$assoc[]=$a;}
  $results[]=['columns'=>$cols,'rows'=>$rows,'assoc'=>$assoc];
 }catch(Throwable $e){$results[]=['error'=>$e->getMessage()];}
}
echo json_encode($results,JSON_THROW_ON_ERROR);
