#!/usr/bin/env python3
"""Sequential PHP comparisons with controlled GMP/fallback extension sets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--before',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();a.output=a.output.resolve();a.output.mkdir(parents=True,exist_ok=True)
trees={'before':a.before.resolve(),'after':ROOT}
summary={}
for config in ['gmp','fallback']:
    php=['php','-n','-d','extension=ctype']
    if config=='gmp':php+=['-d','extension=gmp']
    php+=['-d','memory_limit=-1','-d','opcache.enable_cli=1','-d','opcache.jit_buffer_size=128M','-d','opcache.jit=1255']
    code='echo json_encode(["php"=>PHP_VERSION,"extensions"=>get_loaded_extensions(),"jit"=>opcache_get_status(false)["jit"],"ini"=>ini_get_all(null,false)]);'
    runtime=json.loads(subprocess.check_output(php+['-r',code],text=True))
    assert runtime['jit']['on'] and (('gmp' in runtime['extensions'])==(config=='gmp'))
    (a.output/('runtime-'+config+'.json')).write_text(json.dumps(dict(command=php,**runtime),indent=2)+'\n')
    labels=['before','after','after-repeat','before-repeat'] if config=='gmp' else ['before','after']
    for label in labels:
        tree=trees[label.removesuffix('-repeat')];name=config+'-'+label
        output=a.output/(name+'.json')
        with (a.output/(name+'.log')).open('w') as log:
            subprocess.run(php+[str(tree/'tools/scale-test/sel_benchmarks.php'),'--dataset',str(ROOT/'tools/scale-test/dataset-10x.json'),
                '--reference',str(ROOT/'tools/scale-test/benchmark_results.json'),'--runs','7','--warmups','3','--output',str(output)],
                cwd=tree,stdout=log,stderr=subprocess.STDOUT,check=True)
        report=json.loads(output.read_text());assert report['passed'] and len(report['scenarios'])==6
        summary[name]={}
        for s in report['scenarios']:
            assert s['passed'] and s['context_unchanged']
            times=[x['prepared_total_ms'] for x in s['samples']]
            summary[name][s['id']]=dict(median_ms=statistics.median(times),mean_ms=statistics.mean(times))
        m=a.output/('mandelbrot-'+name+'.json')
        subprocess.run(php+[str(ROOT/'tools/commit-benchmark/mandelbrot.php'),str(m)],cwd=tree,
                       env={**os.environ,'MANDEL_RUNS':'11','MANDEL_WARMUPS':'3'},check=True)
        report=json.loads(m.read_text())
        for frame in report['outputs']:
            assert hashlib.sha256(frame.encode()).hexdigest()=='3d50a84d3774807aaaa132e9b6c826a89ad11548ab4e53ced220b0a00854bcba'
        summary[name]['mandelbrot']=dict(median_ms=statistics.median(report['samples_ms']),mean_ms=statistics.mean(report['samples_ms']))
        with (a.output/('probes-'+name+'.json')).open('w') as out:
            subprocess.run(php+[str(ROOT/'tools/php-runtime/probes.php')],cwd=tree,stdout=out,check=True)
        print(name,summary[name],flush=True)
    if config=='gmp':
        for label,tree in trees.items():
            name='targeted-'+label;output=a.output/(name+'.json')
            with (a.output/(name+'.log')).open('w') as log:
                subprocess.run(php+[str(tree/'tools/scale-test/sel_benchmarks.php'),'--dataset',str(ROOT/'tools/scale-test/dataset-10x.json'),
                    '--reference',str(ROOT/'tools/scale-test/benchmark_results.json'),'--only','scenario4,scenario6',
                    '--runs','15','--warmups','3','--output',str(output)],cwd=tree,stdout=log,stderr=subprocess.STDOUT,check=True)
            report=json.loads(output.read_text());assert report['passed']
            summary[name]={s['id']:dict(median_ms=statistics.median(x['prepared_total_ms'] for x in s['samples']),
                                      mean_ms=statistics.mean(x['prepared_total_ms'] for x in s['samples'])) for s in report['scenarios']}
            print(name,summary[name],flush=True)
(a.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
