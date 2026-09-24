#!/usr/bin/env python3
"""Snapshot startup builtin signatures in all five lanes; callback bodies excluded."""
import json,os,pathlib,subprocess,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
work=pathlib.Path(tempfile.mkdtemp(prefix='sel-registry-scan-'))
def run(cmd,**kw):return subprocess.check_output(cmd,cwd=ROOT,text=True,**kw)
py='''import sel, math
from sel.registry import names,lookup
for name in names():
 s=lookup(name)
 print(name,s.min,-1 if math.isinf(s.max) else s.max,int(s.lazy),int(s.binds),int(s.arity_error is not None),sep='\\t')
for name in ['LINK','LINK_LEFT']:
 try:
  sel.compile(name+'(L, R, TRUE, TRUE)'); result='accepted'
 except sel.SelError as e: result=e.code
 print('PROBE',name,result,sep='\\t')
'''
js="""import './js/src/sel.mjs';import {names,lookup} from './js/src/registry.mjs';
for(const n of names()){const s=lookup(n);console.log([n,s.min,s.max===Infinity?-1:s.max,+s.lazy,+s.binds,+(s.arityError!==null)].join('\\t'));}"""
php="""require 'php/src/bootstrap.php'; foreach(\\Sel\\Registry::names() as $n){$s=\\Sel\\Registry::lookup($n);echo implode("\\t",[$n,$s['min'],$s['max']===PHP_INT_MAX?-1:$s['max'],(int)$s['lazy'],(int)$s['binds'],(int)($s['arityError']!==null)])."\\n";}"""
lisp=work/'registry.lisp'
lisp.write_text('''(load "lisp/bin/boot.lisp")
(ql:quickload :sel-lang :silent t)
(dolist (name (sel:function-names))
 (let ((s (sel::registry-lookup name)))
  (format t "REG~c~a~c~d~c~d~c~d~c~d~c~d~%" #\\Tab name #\\Tab (sel::spec-min s) #\\Tab
   (if (= (sel::spec-max s) sel::+variadic+) -1 (sel::spec-max s)) #\\Tab
   (if (sel::spec-lazy s) 1 0) #\\Tab (if (sel::spec-binds s) 1 0) #\\Tab (if (sel::spec-arity-error s) 1 0))))
''')
outputs={
 'python':run(['python3','-c',py],env={**os.environ,'PYTHONPATH':str(ROOT/'python')}),
 'javascript':run(['node','--input-type=module','-e',js]),
 'php':run(['php','-r',php])}
outputs['javascript']+=run(['node','--input-type=module','-e',"import {compile} from './js/src/sel.mjs'; for(const n of ['LINK','LINK_LEFT']){let r='accepted';try{compile(n+'(L,R,TRUE,TRUE)')}catch(e){r=e.code}console.log(['PROBE',n,r].join('\\t'))}"])
outputs['php']+=run(['php','-r',"require 'php/src/bootstrap.php';foreach(['LINK','LINK_LEFT'] as $n){$r='accepted';try{\\Sel\\Sel::compile($n.'(L,R,TRUE,TRUE)');}catch(\\Sel\\SelError $e){$r=$e->code;}echo \"PROBE\\t$n\\t$r\\n\";}"])
lisp.write_text(lisp.read_text()+'''\n(dolist (name '("LINK" "LINK_LEFT"))
 (format t "PROBE~c~a~c~a~%" #\\Tab name #\\Tab
  (handler-case (progn (sel:compile-source (concatenate 'string name "(L,R,TRUE,TRUE)")) "accepted")
   (sel:sel-error (e) (sel:sel-error-code e)))))
''')
subprocess.run(['g++','-std=c++23','-O0','-Icpp','tools/code-scan/registry.cpp','cpp/sel.cpp','-o',str(work/'registry')],cwd=ROOT,check=True)
outputs['cpp']=run([str(work/'registry')])
text=run(['sbcl','--noinform','--disable-debugger','--non-interactive','--load',str(lisp)],env={**os.environ,'XDG_CACHE_HOME':str(work/'cache')})
outputs['lisp']='\n'.join(line[4:] if line.startswith('REG\t') else line for line in text.splitlines() if line.startswith(('REG\t','PROBE\t')))
rows={lane:{parts[0]:list(map(int,parts[1:])) for line in text.splitlines() if (parts:=line.split('\t')) and len(parts)==6} for lane,text in outputs.items()}
assert all(rows.values())
names=set().union(*(set(r) for r in rows.values()))
diffs={name:{lane:r.get(name) for lane,r in rows.items()} for name in sorted(names) if len({tuple(r[name]) if name in r else None for r in rows.values()})>1}
report=dict(columns=['min','max (-1=variadic)','lazy','binds','has_extra_arity_rule'],lanes=rows,differences=diffs,
 four_argument_compile={lane:{line.split('\t')[1]:line.split('\t')[2] for line in text.splitlines() if line.startswith('PROBE\t')} for lane,text in outputs.items()},
 limitation='Compares metadata and extra-rule presence, not rule bodies, binding-position semantics, registry mutation or behavior.')
(ROOT/'tools/code-scan/results/registry.json').write_text(json.dumps(report,indent=2)+'\n')
print({lane:len(r) for lane,r in rows.items()},'differences:',diffs)
