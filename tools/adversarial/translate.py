"""Standalone adapter that also exercises the installed Python wheel."""
import json
import os
import io
from sel import compile
from sel.sql import Binding, Sql

b={t.upper():Binding.relation(t,t,{c.upper():Binding.column(c,t,'TEXT' if c=='cat' else 'NUM') for c in ('id','cat','v','fk')}) for t in ('r','s')}
if os.getenv('AUDIT_UNIQUE_KEY'): b['R'] = b['R'].with_unique_key('id')
replay=io.StringIO(os.environ['AUDIT_REPLAY_DATA']) if 'AUDIT_REPLAY_DATA' in os.environ else open('tools/adversarial/replay-python-wheel.sel') if os.getenv('AUDIT_REPLAY') else None
with (io.StringIO(os.environ['AUDIT_QUERY']) if 'AUDIT_QUERY' in os.environ else open('tools/adversarial/queries.sel')) as lines:
 for i,source in enumerate(lines):
  for d in ('sqlite','postgresql','mariadb'):
   for strict in (False,True):
    out=dict(i=i,d=d,strict=strict);p=compile(source)
    try:
     f=Sql.translate_statement(p,d,b,{'strict':strict})
     out.update(sql=f.as_statement(),params_sql=f.as_statement('params'),params=[v.as_text() for v in f.bindings()],caveats=f.caveats)
    except Exception as e:out['error']=getattr(e,'code',str(e))
    try:
     h=Sql.plan_hybrid(p,d,b,{'strict':strict})
     out.update(plan='pure_sql' if h.pure_sql else 'pure_memory' if h.pure_memory else 'hybrid',prefix=h.sql_statement.as_statement() if h.sql_statement else None)
     if h.sql_statement: out.update(prefix_params_sql=h.sql_statement.as_statement('params'), prefix_params=[v.as_text() for v in h.sql_statement.bindings()])
    except Exception as e:out['plan_error']=getattr(e,'code',str(e))
    if replay:
     data=compile(next(replay)).run()
     try:
      if data.get('error').as_text():raise RuntimeError('DB_ERROR')
      h=Sql.plan_hybrid(p,d,b,{'strict':strict})
      out['hybrid_value']=Sql.execute_hybrid(h,lambda *_:data.get('rows'),data.get('context')).dump()
     except Exception as e:out['hybrid_error']=getattr(e,'code',str(e))
    print(json.dumps(out))
