"""Alternating cross-edit IR-cache trials with identical parse-cache warmup."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pixellang.compiler_service import CompilerService
from pixellang.regression import fingerprint
from pixellang.vm import VM
from scripts.native_vm_probe import power


def workload():
    files={}
    imports=[]
    calls=[]
    for module in range(10):
        name=f'm{module}'
        imports.append(f'import "{name}.pxl" as {name}')
        files[name+'.pxl']='\n'.join(f'export fn f{i}()->int={module*10+i}' for i in range(10))
        calls.extend(f'print({name}.f{i}())' for i in range(10))
    files['main.pxl']='\n'.join(imports)+'\nfn main(){'+';'.join(calls)+'}'
    return files


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--compiler',type=Path,default=ROOT/'pixellang/artifacts/compiler.json')
    parser.add_argument('--trials',type=int,default=3)
    args=parser.parse_args()
    if args.trials<2:parser.error('At least two alternating trials are required')
    args.output.mkdir(parents=True,exist_ok=False)
    code_bytes=args.compiler.read_bytes();code=json.loads(code_bytes)
    before=fingerprint(ROOT)
    report=dict(verified=False,compilerSha256=hashlib.sha256(code_bytes).hexdigest(),
                powerBefore=power(),trials=[],comparisons={})
    (args.output/'input-files.json').write_text(json.dumps(before,indent=2))
    files=workload()
    (args.output/'workload.json').write_text(json.dumps(files,indent=2))
    body=dict(files);body['m0.pxl']=body['m0.pxl'].replace('f0()->int=0','f0()->int=1000')
    reorder=dict(files);reorder['main.pxl']=reorder['main.pxl'].replace('print(m0.f0());','')
    reorder['main.pxl']=reorder['main.pxl'][:-1]+';print(m0.f0())}'
    try:
        for operation in ('compile','debug-bundle'):
            for label,changed,expected in (
                ('dependency-body',body,[1000]+list(range(1,100))),
                ('call-order',reorder,list(range(1,100))+[0])):
                key=operation+'/'+label
                results=[]
                for trial in range(args.trials):
                    for enabled in ((False,True) if trial%2==0 else (True,False)):
                        service=CompilerService(code)
                        options=dict(version=1,operation=operation,reuseArtifacts=False,reuseIR=enabled)
                        began=time.perf_counter()
                        warm=service.request(dict(options,files=files))
                        warmup_seconds=time.perf_counter()-began
                        assert warm['status']=='ok',warm
                        assert VM(warm['result']['bytecode']).run()==list(range(100))
                        started=time.perf_counter()
                        response=service.request(dict(options,files=changed))
                        seconds=time.perf_counter()-started
                        assert response['status']=='ok',response
                        assert VM(response['result']['bytecode']).run()==expected
                        row=dict(case=key,trial=trial,reuseIR=enabled,seconds=seconds,
                                 warmupSeconds=warmup_seconds,warmupSteps=warm['metrics']['steps'],
                                 warmupResultSha256=digest(warm['result']),
                                 metrics=response['metrics'],resultSha256=digest(response['result']))
                        results.append(row);report['trials'].append(row)
                        (args.output/'partial.json').write_text(json.dumps(report,indent=2))
                        print(json.dumps(row),flush=True)
                assert len({r['resultSha256'] for r in results})==1
                assert len({r['warmupResultSha256'] for r in results})==1
                assert all(r['metrics']['irCacheHits']>0 for r in results if r['reuseIR'])
                cold=statistics.median(r['seconds'] for r in results if not r['reuseIR'])
                warm=statistics.median(r['seconds'] for r in results if r['reuseIR'])
                report['comparisons'][key]=dict(withoutIRCacheSeconds=cold,withIRCacheSeconds=warm,
                    reduction=1-warm/cold,identicalResults=True,
                    initialBuildWithoutCacheSeconds=statistics.median(r['warmupSeconds'] for r in results if not r['reuseIR']),
                    initialBuildWithCacheSeconds=statistics.median(r['warmupSeconds'] for r in results if r['reuseIR']))
        report['verified']=True
    finally:
        report['inputsUnchanged']=before==fingerprint(ROOT) and args.compiler.read_bytes()==code_bytes
        report['verified']=report['verified'] and report['inputsUnchanged']
        report['powerAfter']=power()
        (args.output/'summary.json').write_text(json.dumps(report,indent=2))
    if not report['verified']:raise RuntimeError('Incremental IR comparison failed')


if __name__=='__main__':main()
