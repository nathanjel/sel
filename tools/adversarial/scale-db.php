<?php
$results=[];
$dbs=[
 'postgresql'=>new PDO('pgsql:host='.getenv('AUDIT_PG').';dbname=sel_audit','postgres','sel_audit'),
 'mariadb'=>new PDO('mysql:host='.getenv('AUDIT_MARIA').';dbname=sel_audit;charset=utf8mb4','root','sel_audit'),
];
foreach($dbs as $d=>$db){
 $db->setAttribute(PDO::ATTR_ERRMODE,PDO::ERRMODE_EXCEPTION);
 foreach([1000,10000,100000] as $n){
  $db->exec('DROP TABLE IF EXISTS audit_revisions');
  $db->exec('CREATE TABLE audit_revisions(id INTEGER PRIMARY KEY,cat VARCHAR(20),v INTEGER,fk INTEGER)');
  for($start=1;$start<=$n;$start+=1000){$values=[];for($i=$start;$i<min($start+1000,$n+1);$i++)$values[]="($i,'weight',".($i%100).','. (($i-1)%100).')';$db->exec('INSERT INTO audit_revisions VALUES '.implode(',',$values));}
  $db->exec('CREATE INDEX entity_revision ON audit_revisions(fk,id)');
  $sql='SELECT r.* FROM audit_revisions r JOIN (SELECT fk, MAX(id) AS latest FROM audit_revisions GROUP BY fk) g ON r.id=g.latest';
  $start=hrtime(true);$rows=$db->query($sql)->fetchAll(PDO::FETCH_ASSOC);$sqlMs=(hrtime(true)-$start)/1e6;
  $ids=array_map(fn($r)=>(int)$r['id'],$rows);sort($ids);if($ids!==range($n-99,$n))throw new Exception('baseline mismatch');
  $start=hrtime(true);$all=$db->query('SELECT * FROM audit_revisions')->fetchAll(PDO::FETCH_ASSOC);$fetchMs=(hrtime(true)-$start)/1e6;
  $results[]=['dialect'=>$d,'n'=>$n,'groups'=>count($rows),'sql_ms'=>$sqlMs,'fetch_all_ms'=>$fetchMs,'fetched_rows'=>count($all),'transfer_amplification'=>$n/count($rows)];
 }
}
echo json_encode($results,JSON_PRETTY_PRINT),"\n";
