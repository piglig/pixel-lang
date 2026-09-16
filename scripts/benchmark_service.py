"""Measure installed service edit/build/PNG latency and child peak RSS without bootstrap.

This runs a fixed two-file workload. --limits accepts a JSON object with per-phase
seconds/steps maxima and peakRssBytes, allowing CI to enforce a reviewed baseline.
Uncontrolled power measurements are development evidence, not final comparisons.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM

PHASES=('cold-check','warm-check','body-edit','build','png-export','png-recover')

def load_limits(path):
    limits=json.loads(path.read_text())
    for name in PHASES:
        for key in ('seconds','steps'):
            value=limits['phases'][name][key]
            if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
                raise ValueError('Performance limits must be finite positive numbers')
    value=limits['peakRssBytes']
    if type(value) is not int or value<=0:raise ValueError('Peak RSS limit must be a positive integer')
    return limits


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--limits',type=Path)
    parser.add_argument('--power-condition',default='uncontrolled-development')
    args=parser.parse_args()
    if args.output.exists():parser.error('Use a new output file to preserve prior measurements')
    limits=load_limits(args.limits) if args.limits else None
    files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.total([3,1,2]))}',
           'lib.pxl':'export fn total(values:[int])->int {var sum=0;for value in values {sum+=value};return sum}'}
    report={'scope':'installed service, fixed two-file workload; not full bootstrap or release acceptance',
            'powerCondition':args.power_condition,'platform':platform.platform(),
            'compilerManifest':json.loads((ROOT/'pixellang/artifacts/compiler-manifest.json').read_text()),
            'workloadSha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
            'measurements':[],'violations':[]}
    if sys.platform=='darwin':
        report['powerSnapshot']=subprocess.run(['pmset','-g','batt'],capture_output=True,text=True,check=True).stdout
        report['powerSettings']=subprocess.run(['pmset','-g','custom'],capture_output=True,text=True,check=True).stdout
    with CompilerClient() as client:
        def measure(name,operation,**inputs):
            started=time.perf_counter()
            response=client.request(operation,**inputs)
            entry={'name':name,'seconds':time.perf_counter()-started,**response['metrics']}
            # Keep end-to-end latency distinct from the compiler kernel duration.
            entry['kernelSeconds']=response['metrics']['seconds']
            entry['seconds']=time.perf_counter()-started
            report['measurements'].append(entry)
            return response['result']
        measure('cold-check','check',files=files)
        measure('warm-check','check',files=files)
        files['lib.pxl']=files['lib.pxl'].replace('return sum','return sum+1')
        measure('body-edit','check',files=files)
        code=measure('build','compile',files=files)['bytecode']
        assert VM(code).run()==[7]
        image=measure('png-export','export-debug-png',files=files)['image']
        restored=measure('png-recover','recover-png',image=image)
        rebuilt=client.request('compile',**restored)['result']['bytecode']
        assert VM(rebuilt).run()==[7]
    # A fresh benchmark process owns only its compiler child (and short pmset).
    # macOS reports bytes; Linux reports KiB. Include supervisor/cache overhead.
    peak=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    report['peakRssBytes']=int(peak if sys.platform=='darwin' else peak*1024)
    if limits is not None:
        for entry in report['measurements']:
            for key in ('seconds','steps'):
                maximum=limits['phases'][entry['name']][key]
                if entry[key]>maximum:report['violations'].append(f"{entry['name']}.{key}: {entry[key]} > {maximum}")
        if report['peakRssBytes']>limits['peakRssBytes']:report['violations'].append('Child peak RSS exceeds limit')
    report['gate']='not-configured' if limits is None else 'failed' if report['violations'] else 'passed'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return bool(report['violations'])


if __name__=='__main__':sys.exit(main())
