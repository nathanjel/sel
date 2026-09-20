<?php
// Untimed control: mixed-representation join keys before/after decimal parsing.
require getcwd().'/php/src/bootstrap.php';
$ctx=\Sel\Value::none();
$ctx->set('A',\Sel\Value::num('1.00'));$ctx->set('B',\Sel\Value::text('1.00'));
$ctx->set('L',\Sel\Value::list([\Sel\Value::record(['id'],[$ctx->get('A')])]));
$ctx->set('R',\Sel\Value::list([\Sel\Value::record(['id'],[$ctx->get('B')])]));
$join=\Sel\Sel::compile('LINK(L, R, X, Y, X["id"] == Y["id"])');
$before=$join->run($ctx)->size();
$equality=\Sel\Sel::compile('A == B')->run($ctx)->asBool();
$after=$join->run($ctx)->size();
echo json_encode(['join_before_comparison'=>$before,'equality'=>$equality,'join_after_comparison'=>$after])."\n";
