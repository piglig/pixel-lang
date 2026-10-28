"""Compare fresh-process recovery with and without typed checkpoints.

Run without concurrent verification jobs. Compilation and journal creation are
excluded from recovery timing. Reports include process startup and power state.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang.compiler_client import CompilerClient
from pixellang.event_journal import JournaledApplicationRuntime, digest
from pixellang.regression import fingerprint
from pixellang.vm import VM

WORKER = '''
import json, sys, time
from pixellang.event_journal import JournaledApplicationRuntime, digest
from pixellang.vm import VM
code=json.loads(open(sys.argv[1]).read())
start=time.perf_counter()
runtime=JournaledApplicationRuntime(VM(code),sys.argv[2],max_checkpoint_bytes=int(sys.argv[3]))
seconds=time.perf_counter()-start
print(json.dumps(dict(recovery_seconds=seconds,completed=runtime.completed,output=digest(runtime.view()),next_output=digest(runtime.step()))))
runtime.close()
'''


def power():
    if sys.platform != 'darwin': return {'condition':'not measured'}
    return {name:subprocess.run(['pmset','-g',name],capture_output=True,text=True,check=True).stdout
            for name in ('batt','custom')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source',type=Path,default=ROOT/'examples/native-orders/main.pxl')
    parser.add_argument('--events',type=int,default=1000)
    parser.add_argument('--samples',type=int,default=3)
    args=parser.parse_args()
    output=args.output.resolve()
    if output.is_relative_to(ROOT) or not 128<=args.events<=100000 or not 1<=args.samples<=10:
        parser.error('Use an external new output directory, 128..100000 events and 1..10 samples')
    output.mkdir(parents=True,exist_ok=False)
    before,power_before=fingerprint(ROOT),power()
    with CompilerClient() as compiler:
        files,entry=compiler.read_project(args.source)
        code=compiler.request('compile',files=files,entry=entry)['result']['bytecode']
    code_path=output/'program.json'
    code_path.write_text(json.dumps(code))
    results=[]
    expected_next=None
    for mode,budget in [('full-replay',1),('checkpoints',64_000_000)]:
        journal=output/(mode+'.pxjournal')
        runtime=JournaledApplicationRuntime(VM(code),journal,max_checkpoint_bytes=budget)
        try:
            for _ in range(args.events): runtime.step()
            expected=digest(runtime.view())
        finally:
            runtime.close()
        samples=[]
        for sample in range(args.samples):
            copied=output/f'{mode}-{sample}.pxjournal'
            shutil.copyfile(journal,copied)
            started=time.perf_counter()
            child=subprocess.run([sys.executable,'-c',WORKER,str(code_path),str(copied),str(budget)],
                cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT)},capture_output=True,text=True,timeout=300)
            process_seconds=time.perf_counter()-started
            if child.returncode: raise RuntimeError(child.stdout+child.stderr)
            measured=json.loads(child.stdout)
            if measured['completed']!=args.events or measured['output']!=expected:
                raise RuntimeError('Recovery changed application state')
            if expected_next is None: expected_next=measured['next_output']
            if measured['next_output']!=expected_next: raise RuntimeError('Continuation diverged')
            samples.append(dict(**measured,process_seconds=process_seconds))
        results.append(dict(mode=mode,journal_bytes=journal.stat().st_size,samples=samples,
                            median_seconds=statistics.median(s['recovery_seconds'] for s in samples)))
    after=fingerprint(ROOT)
    if before!=after: raise RuntimeError('Inputs changed during the benchmark')
    report=dict(verified=True,events=args.events,source=str(args.source),fingerprint=before,
                power_before=power_before,power_after=power(),results=results)
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(report=str(output/'report.json'),results=[dict(mode=r['mode'],median_seconds=r['median_seconds']) for r in results])))


if __name__=='__main__': main()
