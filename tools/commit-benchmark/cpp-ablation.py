#!/usr/bin/env python3
"""Diagnostic payload variants of 619bc31. Builds finish before any timings.
Writes experimental sources only under /tmp; never changes repository runtime.
"""
import difflib,json,os,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];BASE=Path('/tmp/sel-commit-benchmark');OUT=ROOT/'tools/commit-benchmark/results'
variants=['inline-collection','inline-decimal']
for variant in variants:
 tree=BASE/variant
 (tree/'tools/scale-test').mkdir(parents=True,exist_ok=True)
 shutil.copytree(BASE/'619bc31/cpp',tree/'cpp',ignore=shutil.ignore_patterns('build'),dirs_exist_ok=True)
 shutil.copy2(ROOT/'tools/scale-test/sel_benchmarks.cpp',tree/'tools/scale-test/sel_benchmarks.cpp')
 h=tree/'cpp/sel.hpp';s=h.read_text()
 if variant=='inline-collection':
  s=s.replace('std::unique_ptr<Collection> collection;', 'std::optional<Collection> collection;').replace('collection = std::make_unique<Collection>();','collection.emplace();')
 else:
  s=s.replace('mutable std::unique_ptr<Dec> decimal;', 'mutable std::optional<Dec> decimal;').replace('p_->decimal = std::make_unique<Dec>(d);','p_->decimal = d;').replace('return p_ ? p_->decimal.get() : nullptr;', 'return p_ && p_->decimal ? &*p_->decimal : nullptr;')
  p=tree/'cpp/sel.cpp';t=p.read_text().replace('std::make_unique<Dec>(std::move(d))','std::move(d)').replace('std::make_unique<Dec>(*p_->decimal)','*p_->decimal');p.write_text(t)
 h.write_text(s)
 patch=""
 for file in ["sel.hpp","sel.cpp"]:
  patch+="".join(difflib.unified_diff((BASE/"619bc31/cpp"/file).read_text().splitlines(True),(tree/"cpp"/file).read_text().splitlines(True),fromfile="619bc31/cpp/"+file,tofile=variant+"/cpp/"+file))
 (OUT/(variant+".patch")).write_text(patch)
 print('build',variant,flush=True)
 with (BASE/(variant+'-build.log')).open('w') as log:
  subprocess.run(['make','-C','cpp','-j2','build/scale-bench'],cwd=tree,stdout=log,stderr=subprocess.STDOUT,check=True)
sizes={}
for name in ['1614eed','619bc31']+variants:
 source='#include "sel.hpp"\n#include <iostream>\nint main(){std::cout<<sizeof(sel::Value::Impl);}'
 binary=BASE/name/'cpp/build/payload-size'
 subprocess.run(['c++','-std=c++23','-Icpp','-x','c++','-','-o',str(binary)],input=source,text=True,cwd=BASE/name,check=True)
 sizes[name]=int(subprocess.check_output([str(binary)],text=True))
(OUT/'cpp-payload-sizes.json').write_text(json.dumps(sizes,indent=2)+'\n')
# Controls bracket the variants. Full six scenarios, same harness/settings.
for name in ['619bc31','inline-collection','inline-decimal','1614eed']:
 print('benchmark',name,flush=True)
 dest=OUT/(name+'-cpp-ablation.json')
 cmd=[str(BASE/name/'cpp/build/scale-bench'),'--dataset',str(ROOT/'tools/scale-test/dataset-10x.json'),'--reference',str(BASE/'619bc31/tools/scale-test/benchmark_results.json'),'--runs','5','--warmups','2','--output',str(dest)]
 with (BASE/(name+'-ablation.log')).open('w') as log:
  subprocess.run(cmd,cwd=BASE/name,stdout=log,stderr=subprocess.STDOUT,check=True)
