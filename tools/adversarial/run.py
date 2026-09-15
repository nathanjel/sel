"""Reproduce the audit across all five translators and database variants.

Provision sel-audit-tools-0915 (PHP/Lisp), sel-audit-pg-0915 and
sel-audit-maria-0915 first. See README.md. All generated outputs stay here.
"""
import json
import os
import subprocess
from pathlib import Path
from probe import CASES, ROOT, bindings, fixture, row, Sql, compile, Value

HERE=Path(__file__).resolve().parent

def call(cmd, **kw):
    p=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=240,**kw)
    if p.returncode: raise RuntimeError(f'{cmd}: {p.stderr[-4000:]}')
    return p.stdout

def literal(v):
    if v is None:return 'NULL'
    if isinstance(v,str):return json.dumps(v,ensure_ascii=False)
    if isinstance(v,list):return 'LIST('+','.join(map(literal,v))+')'
    if isinstance(v,dict):return 'RECORD('+','.join(literal(x) for kv in v.items() for x in kv)+')'
    raise TypeError(v)

def main():
    sources=[x[1] for x in CASES]
    locals_=['R='+literal(x[2])+';S='+literal([row(1,'dimension')])+';'+x[1] for x in CASES]
    (HERE/'queries.sel').write_text('\n'.join(sources)+'\n')
    (HERE/'local.sel').write_text('\n'.join(locals_)+'\n')
    (HERE/'local.selc').write_text(''.join('### '+x[0]+'\n'+s+'\n' for x,s in zip(CASES,locals_)))
    runs={
     'js':['node','tools/adversarial/translate.mjs'],
     'php':['docker','exec','sel-audit-tools-0915','php','/work/tools/adversarial/translate.php'],
     'cpp':['cpp/build/adversarial'],
     'lisp':['docker','exec','sel-audit-tools-0915','sbcl','--noinform','--disable-debugger','--script','/work/tools/adversarial/translate.lisp'],
     'python-wheel':['docker','exec','-e','PYTHONPATH=/tmp/sel-wheel','sel-audit-tools-0915','python3','tools/adversarial/translate.py'],
    }
    translations={host:[json.loads(s) for s in call(cmd).splitlines()] for host,cmd in runs.items()}
    translations['python']=[]
    for i,source in enumerate(sources):
     for d in ('sqlite','postgresql','mariadb'):
      for strict in (False,True):
       out=dict(i=i,d=d,strict=strict);p=compile(source)
       try:
        f=Sql.translate_statement(p,d,bindings(),{'strict':strict})
        out.update(sql=f.as_statement(),params_sql=f.as_statement('params'),params=[v.as_text() for v in f.bindings()],caveats=f.caveats)
       except Exception as e:out['error']=getattr(e,'code',str(e))
       try:
        h=Sql.plan_hybrid(p,d,bindings(),{'strict':strict})
        out.update(plan='pure_sql' if h.pure_sql else 'pure_memory' if h.pure_memory else 'hybrid',prefix=h.sql_statement.as_statement() if h.sql_statement else None)
       except Exception as e:out['plan_error']=getattr(e,'code',str(e))
       translations['python'].append(out)
    (HERE/'translations.json').write_text(json.dumps(translations,indent=2))
    local_cmds={
     'js':['node','tools/run-batch.mjs','tools/adversarial/local.selc'],
     'js-bundle':['node','tools/run-batch.mjs','tools/adversarial/local.selc'],
     'js-bundle-min':['node','tools/run-batch.mjs','tools/adversarial/local.selc'],
     'python':['python3','python/bin/batch.py','tools/adversarial/local.selc'],
     'python-wheel':['docker','exec','-e','PYTHONPATH=/tmp/sel-wheel','sel-audit-tools-0915','python3','python/bin/batch.py','tools/adversarial/local.selc'],
     'php':['docker','exec','sel-audit-tools-0915','php','tools/run-batch.php','tools/adversarial/local.selc'],
     'cpp':['cpp/build/batch','tools/adversarial/local.selc'],
     'lisp':['docker','exec','sel-audit-tools-0915','sbcl','--noinform','--disable-debugger','--script','/work/tools/adversarial/local.lisp'],
    }
    local_results={}
    for host,cmd in local_cmds.items():
     env=dict(os.environ,PYTHONPATH=str(ROOT/'python'))
     if host.startswith('js-bundle'):env['SEL_JS_ENTRY']=str(ROOT/'dist'/('sel.min.mjs' if host.endswith('-min') else 'sel.mjs'))
     local_results[host]=call(cmd,env=env).splitlines()
    (HERE/'local-results.json').write_text(json.dumps(local_results,indent=2))
    requests=[];keys=[]
    for host,rr in translations.items():
     for r in rr:
      i=r['i'];fix=fixture(CASES[i][2],[row(1,'dimension')])
      for mode in ('inline','params','prefix'):
       sql=r.get('prefix' if mode=='prefix' else 'sql' if mode=='inline' else 'params_sql')
       if not sql:continue
       requests.append(dict(dialect=r['d'],fixture=fix,sql=sql,params=r.get('params',[]) if mode=='params' else []))
       keys.append(dict(host=host,i=i,d=r['d'],strict=r['strict'],mode=mode))
    ip=lambda name:call(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',name]).strip()
    dbcmd=['docker','exec','-i','-e','AUDIT_PG='+ip('sel-audit-pg-0915'),'-e','AUDIT_MARIA='+ip('sel-audit-maria-0915'),'sel-audit-tools-0915','php','/work/tools/adversarial/db.php']
    answers=json.loads(call(dbcmd,input=json.dumps(requests)))
    results=[dict(**key,**answer) for key,answer in zip(keys,answers)]
    (HERE/'database-results.json').write_text(json.dumps(results,indent=2))
    hybrid=[]
    for r in translations['python']:
     i=r['i'];h=Sql.plan_hybrid(compile(sources[i]),r['d'],bindings(),{'strict':r['strict']})
     res=next((x for x in results if x['host']=='python' and x['i']==i and x['d']==r['d'] and x['strict']==r['strict'] and x['mode']=='prefix'),None)
     out={k:r[k] for k in ('i','d','strict','plan')}
     try:
      if res and 'error' in res:raise RuntimeError(res['error'])
      v=Sql.execute_hybrid(h,lambda sql,params:Value.from_native(res['assoc']),Value.from_native({'R':CASES[i][2],'S':[row(1,'dimension')]}))
      out['value']=v.dump()
     except Exception as e:out['error']=str(e)
     hybrid.append(out)
    (HERE/'hybrid-results.json').write_text(json.dumps(hybrid,indent=2))
    print(f'{len(CASES)} scenarios; {len(translations)} translators; {len(results)} live SQL executions; {len(hybrid)} Python hybrid executions')

if __name__=='__main__':main()
