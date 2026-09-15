import {readFileSync} from 'node:fs';
import {compile} from '../../js/src/sel.mjs';
import {Binding, Sql} from '../../js/src/sql/index.mjs';
const bindings = Object.fromEntries(['r','s'].map(t=>[t.toUpperCase(),Binding.relation(t,t,Object.fromEntries(['id','cat','v','fk'].map(c=>[c.toUpperCase(),Binding.column(c,t,c==='cat'?'TEXT':'NUM')])))]));
const replay=process.env.AUDIT_REPLAY?readFileSync('tools/adversarial/replay-js.sel','utf8').trimEnd().split('\n'):null;
let n=0;
for(const [i,source] of readFileSync('tools/adversarial/queries.sel','utf8').trimEnd().split('\n').entries()) {
 for(const d of ['sqlite','postgresql','mariadb']) for(const strict of [false,true]) {
  const out={i,d,strict}; const p=compile(source);
  try {const f=Sql.translateStatement(p,d,bindings,{strict}); Object.assign(out,{sql:f.asStatement(),params_sql:f.asStatement('params'),params:f.bindings().map(v=>v.asText()),caveats:f.caveats});} catch(e){out.error=e.code??String(e);}
  try {const p1=Sql.planHybrid(p,d,bindings,{strict});out.plan=p1.pureSql?'pure_sql':p1.pureMemory?'pure_memory':'hybrid';out.prefix=p1.sqlStatement?.asStatement()??null;} catch(e){out.plan_error=e.code??String(e);}
  if(replay){const data=compile(replay[n]).run();try{if(data.get('error').asText())throw Error('DB_ERROR');const h=Sql.planHybrid(p,d,bindings,{strict});out.hybrid_value=Sql.executeHybrid(h,()=>data.get('rows'),data.get('context')).dump();}catch(e){out.hybrid_error=e.code??String(e);}}n++;
  console.log(JSON.stringify(out));
 }
}
