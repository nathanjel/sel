import {readFileSync,writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {compile,Value}=await import(pathToFileURL(process.cwd()+'/js/src/sel.mjs'));
const source=readFileSync('examples/mandelbrot.sel','utf8');
let t=performance.now(); const program=compile(source); const compile_ms=performance.now()-t;
const samples_ms=[],outputs=[];
const warmups=Number(process.env.MANDEL_WARMUPS ?? 2),runs=Number(process.env.MANDEL_RUNS ?? 5);
for(let i=0;i<warmups+runs;i++) { t=performance.now(); const result=program.run(Value.none()); const output=result.asText(); const elapsed=performance.now()-t; if(i>=warmups){samples_ms.push(elapsed);outputs.push(output);} }
writeFileSync(process.argv[2],JSON.stringify({compile_ms,samples_ms,outputs,warmups,runs}));
