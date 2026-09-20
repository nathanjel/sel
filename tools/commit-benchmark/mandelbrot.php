<?php
require getcwd().'/php/src/bootstrap.php';
$source=file_get_contents('examples/mandelbrot.sel');
$t=hrtime(true);$program=\Sel\Sel::compile($source);$compile_ms=(hrtime(true)-$t)/1e6;
$samples_ms=[];$outputs=[];
$warmups=(int)(getenv('MANDEL_WARMUPS') !== false ? getenv('MANDEL_WARMUPS') : 2);$runs=(int)(getenv('MANDEL_RUNS') ?: 5);
for($i=0;$i<$warmups+$runs;$i++){$t=hrtime(true);$result=$program->run(\Sel\Value::none());$output=$result->asText();$elapsed=(hrtime(true)-$t)/1e6;if($i>=$warmups){$samples_ms[]=$elapsed;$outputs[]=$output;}}
file_put_contents($argv[1],json_encode(compact('compile_ms','samples_ms','outputs','warmups','runs')));
