"""Compare equivalent separate and bundled Studio artifacts with a warm parser."""
import argparse
import json
from pathlib import Path
import sys
import time
from statistics import median

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pixellang.compiler_service import CompilerService
from scripts.native_vm_probe import digest,power


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repeats',type=int,default=5)
    args=parser.parse_args()
    if args.repeats<1:parser.error('repeats must be positive')
    if args.output.exists():parser.error('Use a new output path')
    files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.total([3,1,2]))}',
           'lib.pxl':'export fn total(values:[int])->int{var n=0;for x in values{n+=x};return n}'}
    service=CompilerService.installed()
    def call(op,**kwargs):
        response=service.request(dict(version=1,operation=op,files=files,scale=4,**kwargs))
        assert response['status']=='ok',response
        return response
    call('check')
    report=dict(powerBefore=power(),compilerSha256=service.compiler_sha256,workloadSha256=digest(files),samples=[],verified=False)
    for trial in range(args.repeats):
        values={};sample={}
        for mode in (('separate','bundle') if trial%2==0 else ('bundle','separate')):
            start=time.perf_counter()
            if mode=='separate':
                responses=[call(op) for op in ('debug-compile','debug-pixels','inspect-ir','export-debug-png')]
                values[mode]={k:v for r in responses for k,v in r['result'].items()}
                steps=sum(r['metrics']['steps'] for r in responses)
            else:
                response=call('debug-bundle',includeIR=True,includePNG=True,reuseArtifacts=False)
                values[mode]=response['result'];steps=response['metrics']['steps']
            sample[mode]=dict(seconds=time.perf_counter()-start,steps=steps)
        assert values['separate']==values['bundle']
        report['samples'].append(sample)
    first=call('debug-bundle',includeIR=True,includePNG=True)
    repeat=call('debug-bundle',includeIR=True,includePNG=True)
    assert first['result']==repeat['result']==values['bundle']
    assert repeat['metrics']['artifactCacheHit'] and repeat['metrics']['steps']==0
    report.update(verified=True,resultSha256=digest(values['bundle']),cached=repeat['metrics'],powerAfter=power(),
                  medians={mode:{metric:median(s[mode][metric] for s in report['samples']) for metric in ('seconds','steps')} for mode in ('separate','bundle')})
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
