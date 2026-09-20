import json, sys, time, os
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'python'))
from sel import compile, Value
source = Path('examples/mandelbrot.sel').read_text()
t = time.perf_counter(); program = compile(source); compile_ms = (time.perf_counter()-t)*1000
samples=[]; outputs=[]
warmups=int(os.environ.get('MANDEL_WARMUPS','2')); runs=int(os.environ.get('MANDEL_RUNS','5'))
for i in range(warmups+runs):
    t=time.perf_counter(); result=program.run(Value.none()); output=result.as_text(); elapsed=(time.perf_counter()-t)*1000
    if i>=warmups: samples.append(elapsed); outputs.append(output)
Path(sys.argv[1]).write_text(json.dumps(dict(compile_ms=compile_ms,samples_ms=samples,outputs=outputs,warmups=warmups,runs=runs)))
