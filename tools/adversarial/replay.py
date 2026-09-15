"""Feed real SQL-prefix results through each host's public hybrid executor."""
import json
import os
from run import HERE, CASES, call, literal, row

def main():
    translations=json.loads((HERE/'translations.json').read_text())
    results=json.loads((HERE/'database-results.json').read_text())
    for host in ('js','php','cpp','lisp','python-wheel'):
        data=[]
        for r in translations[host]:
            answer=next((x for x in results if all(x[k]==r[k] for k in ('i','d','strict')) and x['host']==host and x['mode']=='prefix'),{})
            data.append(literal({'context':{'R':CASES[r['i']][2],'S':[row(1,'dimension')]},'rows':answer.get('assoc',[]),'error':answer.get('error','')}))
        (HERE/f'replay-{host}.sel').write_text('\n'.join(data)+'\n')
    commands={
        'js':['node','tools/adversarial/translate.mjs'],
        'cpp':['cpp/build/adversarial'],
        'php':['docker','exec','-e','AUDIT_REPLAY=1','sel-audit-tools-0915','php','tools/adversarial/translate.php'],
        'lisp':['docker','exec','-e','AUDIT_REPLAY=1','sel-audit-tools-0915','sbcl','--noinform','--disable-debugger','--script','tools/adversarial/translate.lisp'],
        'python-wheel':['docker','exec','-e','AUDIT_REPLAY=1','-e','PYTHONPATH=/tmp/sel-wheel','sel-audit-tools-0915','python3','tools/adversarial/translate.py'],
    }
    replays={h:[json.loads(line) for line in call(cmd,env=dict(os.environ,AUDIT_REPLAY='1')).splitlines()] for h,cmd in commands.items()}
    (HERE/'all-host-hybrid.json').write_text(json.dumps(replays,indent=2))
    print('Hybrid executors replayed:',{h:len(rr) for h,rr in replays.items()})

if __name__=='__main__':main()
